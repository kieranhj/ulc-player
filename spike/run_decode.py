"""ulc decoder spike: decode a real .ulc stream end to end on emulated ARM2 / ARM250 / ARM3.

For each machine: boot RISC OS 3.11, run bench_dec,ff8, read the cycle
counter after every block, then read the decoded 8-bit output back and compare
it with a float reference decoder (ulc_tools.RefDecoder).

Stream source, in order of preference:
  --ulc FILE        an existing .ulc file (e.g. from the real ulcencodetool)
  --wav FILE        16-bit WAV, encoded with the spike's MiniEncoder
  (default)         synthetic music, encoded with MiniEncoder

Usage:  python spike/run_decode.py [--stereo] [--block 2048] [--rate 20833]
                                   [--coarse 1.0] [--transient-every 4]
                                   [--configs a,b] [--mode 13] [--out FILE]
"""
import argparse
import json
import os
import struct
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_bench as rb                                          # noqa: E402
from ulc_tools import (MiniEncoder, RefDecoder, read_wav,       # noqa: E402
                       set_header_rate, synth_music, vidc_lin8_to_log,
                       vidc_lin_to_log, lin11_to_lin8, lin11_low8)

BUILD = rb.BUILD
MARK = 0x800C
CH2_OFFS = 0x10000


def make_stream(args):
    if args.ulc:
        with open(args.ulc, "rb") as f:
            return f.read(), "file " + os.path.basename(args.ulc)
    if args.wav:
        pcm, rate = read_wav(args.wav, args.rate)
        pcm = pcm[:, :int(args.seconds * rate)]
        if not args.stereo:
            pcm = pcm.mean(axis=0, keepdims=True)
        elif pcm.shape[0] == 1:
            pcm = np.vstack([pcm, pcm])
        src = "wav " + os.path.basename(args.wav)
    else:
        pcm = synth_music(args.rate, args.seconds, args.stereo)
        src = "synthetic music"
    nch = 2 if args.stereo else 1
    enc = MiniEncoder(args.block, nch, coarse=args.coarse, transient_every=args.transient_every)
    data = enc.encode(pcm)
    nblk = struct.unpack_from("<I", data, 8)[0]
    kbps = (len(data) - 24) * 8 / (nblk * args.block / args.rate) / 1000
    return set_header_rate(data, args.rate, kbps), src + " (MiniEncoder)"


def build(data, mode, nblk, ring=0x7FFFFFFF, table=0):
    os.makedirs(BUILD, exist_ok=True)
    with open(os.path.join(BUILD, "stream.ulc"), "wb") as f:
        f.write(data)
    for name, tab in (("vidc_table.bin", vidc_lin8_to_log()), ("ident256.bin", bytes(range(256))),
                      ("lin11_8.bin", lin11_to_lin8()), ("lin11_lo.bin", lin11_low8()),
                      ("vidc11.bin", vidc_lin_to_log(11))):
        with open(os.path.join(BUILD, name), "wb") as f:
            f.write(tab)
    __import__("ulc_tools").write_fourier_tables(BUILD)
    out = os.path.join(BUILD, "bench_dec,ff8")
    lst = os.path.join(BUILD, "bench_dec.lst")
    subprocess.run([rb.VASM, "-m2", "-opt-adr", "-Fbin", "-quiet", "-L", lst, "-o", out,
                    "bench_dec.asm"], cwd=HERE, check=True)
    with open(out, "rb") as f:
        img = bytearray(f.read())
    struct.pack_into("<II", img, 4, mode, nblk)
    struct.pack_into("<II", img, 0x24, ring, table)
    names = ("ulcfile", "outbuf", "vidcbuf", "ulc_State", "buf_end")
    syms = dict(zip(names, struct.unpack_from("<5I", img, 0x10)))
    return bytes(img), syms


def read_mem(m, addr, length):
    out = bytearray()
    while length > 0:
        n = min(length, 0x4000)
        out += m.command(f"read {addr:x} {n:x}")
        addr += n
        length -= n
    return bytes(out)


def run_machine(pool, config, image, syms, nblk, bsize, nch, ring=None):
    """Returns marks, the output ring as read (slot order), and the VIDC buffer."""
    ring = min(nblk, ring or nblk)
    local = os.path.join(HERE, "configs", config + ".cfg")
    mid, m = (pool.create(config_file=local) if os.path.exists(local)
              else pool.create(config=config))
    try:
        with open(os.path.join(m.hostfs, "bench_dec,ff8"), "wb") as f:
            f.write(image)
        for _ in range(12):
            m.command_json("run_us 1000000")
        rb.tap(m, rb.keymap.SCANCODES["f12"])
        m.command_json("run_us 500000")
        need_k = (syms["buf_end"] - 0x8000 + 64 * 1024) // 1024
        if need_k > 600:                         # default slot is ~640K: grow it first
            rb.type_text(m, f"WimpSlot -min {need_k}K")
            rb.tap(m, rb.keymap.SCANCODES["return"])
            m.command_json("run_us 200000")
        rb.type_text(m, "HostFS::HostFS.$.bench_dec")
        m.command_text(f"bp add {MARK:x}")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        marks = {}
        for _ in range(400000):                 # up to 2000 s of emulated time
            res = m.command_json("run_us 5000")
            if res["reason"] == "budget":
                continue
            regs = m.command_json("regs")
            tag, cyc = regs["r"][12], regs["cycles"]
            if tag == 0xEEEE:
                raise RuntimeError("bench_dec: not enough application memory")
            marks[tag] = cyc
            if tag == 0x50001:
                break
        else:
            raise RuntimeError("benchmark did not finish")
        raw = read_mem(m, syms["outbuf"], ring * bsize * nch)   # L,R interleaved for stereo
        lin = [raw[c::nch] for c in range(nch)]
        return marks, lin, raw
    finally:
        pool.destroy(mid)


def compare(lin, vidc, ref, nch):
    out = {}
    got = np.array([np.frombuffer(b, dtype=np.int8).astype(np.float64) for b in lin])
    # RefDecoder mirrors the ARM coefficient stage exactly (including the
    # GBA's negated tonal coefficients); 1.0 in the reference is 128 LSB.
    want = ref[:, :got.shape[1]] * 128.0
    out["fitted_scale"] = round(float((got * want).sum() / max((want * want).sum(), 1e-9)), 4)
    err = got - want
    sig = (want ** 2).sum()
    out["snr_db"] = round(float(10 * np.log10(sig / max((err ** 2).sum(), 1e-9))), 1)
    out["max_abs_err_lsb"] = round(float(np.abs(err).max()), 2)
    out["within_1lsb_pct"] = round(float((np.abs(err) <= 1.0).mean() * 100), 2)
    if nch == 2:   # the ARM truncates M and S to 8 bits first, then L=M+S, R=M-S saturating
        m8 = np.clip(np.floor((want[0] + want[1]) / 2), -128, 127)
        s8 = np.clip(np.floor((want[0] - want[1]) / 2), -128, 127)
        want8 = np.clip(np.array([m8 + s8, m8 - s8]), -128, 127)
    else:          # the ARM truncates (ASR) to 8 bits
        want8 = np.clip(np.floor(want), -128, 127)
    eq = got - want8
    out["snr_vs_8bit_ref_db"] = round(float(10 * np.log10(sig / max((eq ** 2).sum(), 1e-9))), 1)
    out["exact_vs_8bit_ref_pct"] = round(float((eq == 0).mean() * 100), 2)
    out["clipped_pct"] = round(float((np.abs(want) >= 127.5).mean() * 100), 3)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ulc")
    ap.add_argument("--wav")
    ap.add_argument("--stereo", action="store_true")
    ap.add_argument("--block", type=int, default=2048)
    ap.add_argument("--rate", type=int, default=20833)
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--coarse", type=float, default=1.0)
    ap.add_argument("--transient-every", type=int, default=4)
    ap.add_argument("--mode", type=int, default=13)
    ap.add_argument("--configs", default=",".join(rb.CONFIGS))
    ap.add_argument("--max-blocks", type=int, default=0, help="0 = whole stream")
    ap.add_argument("--out", default=os.path.join(BUILD, "decode-results.json"))
    args = ap.parse_args()

    data, src = make_stream(args)
    magic, bsize, _, nblocks, rate, nch, kbps, offs = struct.unpack_from("<IHHIIHHI", data)
    ring = CH2_OFFS // bsize                    # output ring; long streams wrap
    nblk = min(nblocks, args.max_blocks) if args.max_blocks else nblocks
    nyb = 2 * (len(data) - offs) / (nblocks * bsize * nch)
    print(f"stream: {src}, {nch} ch, N={bsize}, {rate} Hz, {nblocks} blocks "
          f"({nblk} decoded), {kbps} kbps, {nyb:.2f} nybbles/coef")

    image, syms = build(data, args.mode, nblk, ring)
    ref_dec = RefDecoder(data, syms["ulcfile"])
    blocks = [ref_dec.decode_block() for _ in range(nblk)]
    # the ARM's ring holds the last min(nblk, ring) blocks, block b in slot b % ring
    held = min(nblk, ring)
    slot = {b % ring: blocks[b] for b in range(nblk - held, nblk)}
    ref = np.concatenate([slot[j] for j in range(held)], axis=1)

    pool = rb.MachinePool(os.path.join(rb.ARC_ROOT, "arculator-headless.exe"),
                          rb.ARC_ROOT, os.path.join(BUILD, "sandbox"))
    results = {"stream": {"source": src, "nchan": nch, "block": bsize, "rate": rate,
                          "kbps": kbps, "nybbles_per_coef": round(nyb, 3),
                          "blocks_decoded": nblk}, "machines": {}}
    try:
        for cfg in args.configs.split(","):
            name, mhz = rb.CONFIGS[cfg]
            print(f"\n== {cfg}: {name}, MODE {args.mode}")
            marks, lin, vidc = run_machine(pool, cfg, image, syms, nblk, bsize, nch, ring)
            per = [marks[0x30000 + b + 1] - marks[0x30000 + b] for b in range(nblk)]
            warm = per[1:] if len(per) > 1 else per
            mean = sum(warm) / len(warm)
            vidc_cyc = marks[0x50001] - marks[0x50000]
            chk = compare(lin, vidc, ref, nch)
            r = {"name": name, "mhz": mhz,
                 "block_cycles": per,
                 "mean_block_cycles": round(mean),
                 "max_block_cycles": max(warm),
                 "decode_cycles_per_sample": round(mean / bsize, 1),
                 "worst_cycles_per_sample": round(max(warm) / bsize, 1),
                 "p99_cycles_per_sample": round(float(np.percentile(warm, 99)) / bsize, 1),
                 "vidc_cycles_per_sample": round(vidc_cyc / (min(nblk, ring) * bsize), 1),
                 "check": chk}
            total = r["decode_cycles_per_sample"] + r["vidc_cycles_per_sample"]
            r["cpu_pct"] = {str(hz): round(total * hz / (mhz * 1e6) * 100, 1)
                            for hz in (12500, 15625, 20833, 31250)}
            results["machines"][cfg] = r
            print(f"  decode: {r['decode_cycles_per_sample']} cyc/sample mean, "
                  f"{r['worst_cycles_per_sample']} worst block, {r['p99_cycles_per_sample']} p99; VIDC pass "
                  f"{r['vidc_cycles_per_sample']} cyc/sample")
            print(f"  CPU % (decode + VIDC pass): " + ", ".join(
                f"{k} Hz {v}%" for k, v in r["cpu_pct"].items()))
            print(f"  vs reference: SNR {chk['snr_db']} dB, max err {chk['max_abs_err_lsb']} LSB, "
                  f"{chk['within_1lsb_pct']}% within 1 LSB; vs 8-bit-floored ref: SNR {chk['snr_vs_8bit_ref_db']} dB, "
                  f"{chk['exact_vs_8bit_ref_pct']}% identical")
    finally:
        pool.destroy_all()
    with open(args.out, "w") as f:
        json.dump(results, f, indent=1)
    print(f"\nresults -> {args.out}")


if __name__ == "__main__":
    main()
