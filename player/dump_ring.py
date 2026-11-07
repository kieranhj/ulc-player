"""Run the player on a file, then dump its 32 KB VIDC ring and analyse it.

Separates "decode glue writes bad data" from "the sound output path mangles
good data": the ring holds exactly what the fill routine copies to DMA.

    python player/dump_ring.py music/sine440-mono-128k.ulc
"""
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "spike"))
sys.path.insert(0, HERE)
import run_bench as rb                                  # noqa: E402
import run_player as rp                                 # noqa: E402
from ulc_tools import vidc_log_to_lin                   # noqa: E402


def main():
    track = open(sys.argv[1], "rb").read()
    image = rp.build()
    info = struct.unpack_from("<7I", image, 4)          # underruns, cpu, blocks, ring_rd, ring_wr, dma
    lst = open(os.path.join(rp.BUILD, "player.lst"), encoding="latin-1").read()
    pool = rb.MachinePool(os.path.join(rb.ARC_ROOT, "arculator-headless.exe"),
                          rb.ARC_ROOT, os.path.join(rp.BUILD, "sandbox"))
    try:
        mid, m = pool.create(config="a3020-riscos311")
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
        for _ in range(40):
            m.command_json("run_us 100000")
        p_ring = None
        # p_ring holds the ring address: find it from the image (word after p_outbuf)
        vals = [struct.unpack("<I", m.command(f"read {a:x} 4"))[0] for a in info[:6]]
        print("underruns, cpu, blocks, ring_rd, ring_wr, dma =", vals)
        ring = info[6]
        outbuf = ring - 4 * 2048 - 2 * 0x800
        data = m.command(f"read {ring:x} {32768:x}")
        out = m.command(f"read {outbuf:x} {512:x}")
    finally:
        pool.destroy_all()
    log2lin = np.array([vidc_log_to_lin(c) for c in range(256)], dtype=float)
    x = log2lin[np.frombuffer(data, np.uint8)]
    ring_wr = vals[4] % 32768
    np.save(os.path.join(rp.BUILD, "ring_raw.npy"), np.frombuffer(data, np.uint8))
    np.save(os.path.join(rp.BUILD, "ring_vals.npy"), np.array(vals))
    x = np.roll(x, -ring_wr)                # oldest first
    print("ring: mean %.1f std %.1f" % (x.mean(), x.std()))
    sp = np.abs(np.fft.rfft((x - x.mean()) * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / 20833)
    pk = f[sp.argmax()]
    band = abs(f - pk) < 15
    print("ring: peak %.1f Hz, peak vs rest %.1f dB" % (pk, 10 * np.log10(sp[band].sum() / sp[~band].sum())))
    print("block means:", [round(x[i:i + 512].mean()) for i in range(0, 8 * 512, 512)])
    o = np.frombuffer(out, np.int8).astype(float)
    print("outbuf (last block, 8-bit): mean %.2f std %.1f" % (o.mean(), o.std()))
    np.save(os.path.join(rp.BUILD, "ring.npy"), x)


if __name__ == "__main__":
    main()
