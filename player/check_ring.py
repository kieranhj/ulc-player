"""Exact check of what the player writes to its sound ring.

Runs the player on a file for --settle seconds, dumps the 32 KB ring of VIDC
bytes, and compares every complete block in it with what it should hold:

  ADPCM  the IMA decode (adpcm_encode.decode) through the 12-bit VIDC table
  ULC    --golden: an 11-bit decoder output saved by spike/check_vs_cdec.py
         (mono), through the 11-bit VIDC table

    python player/check_ring.py music/particle-mono-adpcm.adp
    python player/check_ring.py music/particle-mono-128k.ulc --golden spike/build/check_arm11.npy
"""
import argparse
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "spike"))
sys.path.insert(0, HERE)
import run_bench as rb                                  # noqa: E402
import run_player as rp                                 # noqa: E402
import adpcm_encode                                     # noqa: E402
from ulc_tools import vidc_lin_to_log                   # noqa: E402

RING = 32768


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--golden")
    ap.add_argument("--config", default="a3020-riscos311")
    ap.add_argument("--settle", type=float, default=1.0)
    a = ap.parse_args()
    track = open(a.file, "rb").read()
    image = rp.build()
    info = struct.unpack_from("<7I", image, 4)          # underruns, cpu, blocks, ring_rd, ring_wr, dma
    pool = rb.MachinePool(os.path.join(rb.ARC_ROOT, "arculator-headless.exe"),
                          rb.ARC_ROOT, os.path.join(rp.BUILD, "sandbox"))
    try:
        local = os.path.join(HERE, "..", "spike", "configs", a.config + ".cfg")
        mid, m = (pool.create(config_file=local) if os.path.exists(local)
                  else pool.create(config=a.config))
        open(os.path.join(m.hostfs, "player,ff8"), "wb").write(image)
        open(os.path.join(m.hostfs, "track,ffd"), "wb").write(track)
        for _ in range(12):
            m.command_json("run_us 1000000")
        rb.tap(m, rb.keymap.SCANCODES["f12"])
        m.command_json("run_us 500000")
        slot = (len(image) + 16384 + 4096 + 32768 + len(track)) // 1024 + 64
        rb.type_text(m, f"WimpSlot -min {slot}K")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        m.command_json("run_us 300000")
        rb.type_text(m, "HostFS::HostFS.$.player HostFS::HostFS.$.track")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        for _ in range(int(a.settle * 10)):
            m.command_json("run_us 100000")
        vals = [struct.unpack("<I", m.command(f"read {x:x} 4"))[0] for x in info[:6]]
        ring = info[6]
        data = b"".join(m.command(f"read {ring + o:x} 4000") for o in range(0, RING, 0x4000))
        vals2 = [struct.unpack("<I", m.command(f"read {x:x} 4"))[0] for x in info[:6]]
    finally:
        pool.destroy_all()
    under, cpu, blocks, rd, wr, dma = vals
    if vals2[4] != wr:
        print("warning: the decoder wrote while the ring was read; rerun")
    ring_b = np.frombuffer(data, np.uint8)

    if track[:4] == b"ADP4":
        _, rate, nch, nblk, bs, offs, _, _ = struct.unpack_from("<4sIIIIIII", track)
        pcm, _ = adpcm_encode.decode(track)             # (nch, nblk*bs) 16-bit
        tab = np.frombuffer(vidc_lin_to_log(12), np.uint8)
        exp_blocks = None

        def expect(b):
            x = pcm[:, (b % nblk) * bs:(b % nblk + 1) * bs]
            v = tab[(x >> 4) + 2048]
            return v.T.reshape(-1)                      # interleave L,R
    else:
        _, bs, _, nblk, rate, nch, kbps, offs = struct.unpack_from("<IHHIIHHI", track)
        assert nch == 1 and a.golden, "ULC: mono with --golden only"
        g = np.load(a.golden).astype(int)
        tab = np.frombuffer(vidc_lin_to_log(11), np.uint8)
        exp_blocks = len(g) // bs

        def expect(b):
            return tab[g[b * bs:(b + 1) * bs] + 1024]
    bb = bs * nch
    nb = wr // bb
    print(f"{os.path.basename(a.file)}: blocks written {nb}, ring_rd {rd}, ring_wr {wr}, "
          f"underruns {under}, CPU {cpu}%")
    first = max(0, nb - RING // bb)
    checked = bad = 0
    for b in range(first, nb):
        if exp_blocks is not None and b >= exp_blocks:
            break
        o = (b * bb) % RING
        got = ring_b[o:o + bb]
        e = expect(b)
        n = int((got != e).sum())
        if n and b == first and (exp_blocks is None or nb < exp_blocks):
            # The oldest slot is where the decoder is writing block nb right now
            # (ring_wr only moves when it finishes): accept bytes from either.
            e2 = expect(nb)
            if np.all((got == e) | (got == e2)):
                print(f"  block {b}: partly overwritten by block {nb} (in progress), both exact")
                n = 0
        checked += 1
        if n:
            bad += 1
            if bad <= 3:
                i = int(np.argmax(got != e))
                print(f"  block {b}: {n} bytes differ, first at {i}: got {got[i]} want {e[i]}")
    print(f"checked {checked} blocks: {'ALL EXACT' if checked and not bad else f'{bad} differ'}")
    sys.exit(0 if checked and not bad else 1)


if __name__ == "__main__":
    main()
