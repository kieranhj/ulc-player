"""Check the ARM decoder against ulc-codec's own C decoder (ulcdecodetool).

run_decode.py compares against RefDecoder, which mirrors the ARM decoder's
quirks and so can share its mistakes. This compares with the real thing.

    python spike/check_vs_cdec.py <file.ulc> <cdec.wav> [--blocks 40] [--config a3020-riscos311]
"""
import argparse
import os
import struct
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_decode as rd                                 # noqa: E402
import run_bench as rb                                  # noqa: E402
from ulc_tools import RefDecoder                        # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ulc")
    ap.add_argument("cdec_wav")
    ap.add_argument("--blocks", type=int, default=40)
    ap.add_argument("--config", default="a3020-riscos311")
    a = ap.parse_args()
    data = open(a.ulc, "rb").read()
    _, bsize, _, nblocks, rate, nch, kbps, offs = struct.unpack_from("<IHHIIHHI", data)
    nblk = min(a.blocks, nblocks)
    ring = rd.CH2_OFFS // bsize
    assert nblk <= ring
    pool = rb.MachinePool(os.path.join(rb.ARC_ROOT, "arculator-headless.exe"),
                          rb.ARC_ROOT, os.path.join(rd.BUILD, "sandbox"))
    try:
        image, syms = rd.build(data, 13, nblk, ring, table=0)
        marks, lin, vidc = rd.run_machine(pool, a.config, image, syms, nblk, bsize, nch, ring)
        cyc = (marks[0x30000 + nblk] - marks[0x30001]) / ((nblk - 1) * bsize)
        if nch == 1:           # second run: the low 8 bits of the 11-bit output
            image, syms = rd.build(data, 13, nblk, ring, table=1)
            _, lo, _ = rd.run_machine(pool, a.config, image, syms, nblk, bsize, nch, ring)
    finally:
        pool.destroy_all()
    arm = np.frombuffer(lin[0], dtype=np.int8).astype(float)[: nblk * bsize]
    print(f"decode: {cyc:.1f} cycles/sample ({a.config})")
    if nch == 1:
        hi = np.frombuffer(lin[0], dtype=np.int8).astype(int)[: nblk * bsize]
        lo = np.frombuffer(lo[0], dtype=np.uint8).astype(int)[: nblk * bsize]
        assert ((lo >> 3) == (hi & 0x1F)).all(), "hi/lo runs disagree"
        arm11 = (hi * 8 + (lo & 7)).astype(float)
        np.save(os.path.join(rd.BUILD, "check_arm11.npy"), arm11)

    ref = np.concatenate([RefDecoder(data, syms["ulcfile"]).decode_block() for _ in range(nblk)], axis=1)[0] * 128
    w = wave.open(a.cdec_wav)
    c = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels())[:, 0].astype(float) / 256

    def best(x, y):          # align y to x (lag and sign), report correlation and SNR
        n = len(x) - 2 * bsize
        out = None
        for lag in range(-bsize, bsize + 1):
            seg = y[bsize + lag: bsize + lag + n] if bsize + lag >= 0 else None
            if seg is None or len(seg) < n:
                continue
            r = np.corrcoef(x[bsize: bsize + n], seg)[0, 1]
            if out is None or abs(r) > abs(out[1]):
                out = (lag, r)
        lag, r = out
        seg = y[bsize + lag: bsize + lag + n] * np.sign(r)
        xs = x[bsize: bsize + n]
        k = (xs * seg).sum() / (seg * seg).sum()
        snr = 10 * np.log10((xs ** 2).sum() / ((xs - k * seg) ** 2).sum())
        return lag, round(r, 4), round(k, 3), round(snr, 1)

    print(f"ARM vs C decoder : lag, corr, gain, SNR = {best(arm, c)}")
    if nch == 1:   # 11-bit output in 8-bit LSB units, + 1/16 LSB for the bin centre
        print(f"ARM 11-bit vs C  : lag, corr, gain, SNR = {best(arm11 / 8 + 1 / 16, c)}")
    print(f"ARM vs RefDecoder: lag, corr, gain, SNR = {best(arm, ref)}")
    print(f"Ref vs C decoder : lag, corr, gain, SNR = {best(ref, c)}")
    np.save(os.path.join(rd.BUILD, "check_arm.npy"), arm)
    np.save(os.path.join(rd.BUILD, "check_ref.npy"), ref)
    print("block means ARM:", [round(arm[i:i + bsize].mean(), 1) for i in range(0, 10 * bsize, bsize)])


if __name__ == "__main__":
    main()
