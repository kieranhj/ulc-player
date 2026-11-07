"""IMA-style ADPCM encoder (4, 3 or 2 bits) for the Archimedes player, plus a
reference decoder.

File format "ADP4" (little-endian), 32-byte header:
  [0]  'ADP4'
  [4]  sample rate (Hz)
  [8]  channels (1 or 2)
  [12] number of blocks
  [16] samples per channel per block (power of two, 64..2048)
  [20] offset of the first block (32)
  [24] nominal kbps
  [28] bits per code: 4, 3 or 2 (0 = 4, files from before the variants)
Each block, for each channel in turn:
  int16 predictor, uint8 step index, uint8 0, then samples*bits/8 bytes of
  codes as a little-endian bit stream (code k at bit k*bits; for 4 bits that
  is the low nybble first). The header predictor is the state *before* the
  block's first code, so every block decodes on its own.

Decoding a code c of B bits (half = 1 << (B-1), m = c & (half-1)):
  diff = step >> (B-1), plus step >> (B-2-j) for each set bit j of m
  pred = clamp16(pred - diff if c & half else pred + diff)
  idx  = clamp(idx + INDEX[B][m], 0, 88)
For B = 4 this is standard IMA ADPCM.

Encoders: --beam 0 is the original greedy IMA quantiser (pure Python);
--beam K >= 1 is a search keeping the K best paths per sample
(tools/adpcm_trellis.c, built with zig cc), minimising the error of the
predictor (--cost lin) or of what the player outputs through the VIDC log
table (--cost vidc).

Usage:  python player/adpcm_encode.py in.wav out.adp [--block 1024]
            [--bits 4] [--beam 16] [--cost vidc]
"""
import argparse
import os
import struct
import subprocess
import sys
import tempfile
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")

STEPS = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767]
INDEX = {4: [-1, -1, -1, -1, 2, 4, 6, 8], 3: [-1, -1, 2, 4], 2: [-1, 2]}


def code_diff(idx, c, bits):
    step, half = STEPS[idx], 1 << (bits - 1)
    m, d = c & (half - 1), step >> (bits - 1)
    for j in range(bits - 2, -1, -1):
        if m & (1 << j):
            d += step >> (bits - 2 - j)
    return -d if c & half else d


def tables(bits):
    """DIFF[idx][code], NEXT[idx][code] as numpy arrays."""
    n = 1 << bits
    diff = np.array([[code_diff(i, c, bits) for c in range(n)] for i in range(89)])
    nxt = np.array([[min(88, max(0, i + INDEX[bits][c & ((n >> 1) - 1)])) for c in range(n)]
                    for i in range(89)])
    return diff, nxt


def step_decode(nib, pred, idx, bits=4):
    pred = max(-32768, min(32767, pred + code_diff(idx, nib, bits)))
    idx = max(0, min(88, idx + INDEX[bits][nib & ((1 << (bits - 1)) - 1)]))
    return pred, idx


def pack_codes(codes, bits):
    """Little-endian bit stream, code k at bit k*bits."""
    v = 0
    for k, c in enumerate(codes):
        v |= int(c) << (k * bits)
    return v.to_bytes(len(codes) * bits // 8, "little")


def unpack_codes(data, n, bits):
    v = int.from_bytes(data, "little")
    mask = (1 << bits) - 1
    return [(v >> (k * bits)) & mask for k in range(n)]


def greedy_block(x, pred, idx):
    """The original greedy IMA (4-bit) quantiser."""
    codes = []
    for s in x:
        step = STEPS[idx]
        d = int(s) - pred
        nib = 8 if d < 0 else 0
        d = abs(d)
        if d >= step:
            nib |= 4
            d -= step
        if d >= step >> 1:
            nib |= 2
            d -= step >> 1
        if d >= step >> 2:
            nib |= 1
        pred, idx = step_decode(nib, pred, idx)
        codes.append(nib)
    return codes, pred, idx


def output_table():
    """Linear 16-bit value the player outputs for each 12-bit predictor bin."""
    sys.path.insert(0, os.path.join(ROOT, "spike"))
    from ulc_tools import vidc_lin_to_log, vidc_log_to_lin
    t = np.frombuffer(vidc_lin_to_log(12), np.uint8)
    lin = np.array([vidc_log_to_lin(c) for c in range(256)], float) * 32768 / 3952
    return np.clip(np.round(lin[t]), -32768, 32767).astype("<i2")


def trellis_tool():
    exe = os.path.join(HERE, "build", "adpcm_trellis.exe")
    src = os.path.join(ROOT, "tools", "adpcm_trellis.c")
    if not os.path.exists(exe) or os.path.getmtime(exe) < os.path.getmtime(src):
        os.makedirs(os.path.dirname(exe), exist_ok=True)
        subprocess.run(["zig", "cc", "-O2", "-o", exe, src], check=True)
    return exe


def trellis_channel(x, block, bits, beam, cost):
    """-> list of (pred, idx, codes) per block."""
    exe = trellis_tool()
    with tempfile.TemporaryDirectory() as d:
        fin, fout, ftab = (os.path.join(d, n) for n in ("in.raw", "out.bin", "tab.bin"))
        x.astype("<i2").tofile(fin)
        output_table().tofile(ftab)
        subprocess.run([exe, fin, fout, str(bits), str(beam), str(block),
                        "1" if cost == "vidc" else "0", ftab], check=True)
        raw = open(fout, "rb").read()
    out, step = [], 4 + block
    for p in range(0, len(raw), step):
        pred, idx, _ = struct.unpack_from("<hBB", raw, p)
        out.append((pred, idx, list(raw[p + 4:p + step])))
    return out


def encode(pcm, rate, block=1024, bits=4, beam=0, cost="lin"):
    """pcm: (nch, n) int16-range ints. Returns the .adp file bytes."""
    nch, n = pcm.shape
    nblk = (n + block - 1) // block
    x = np.zeros((nch, nblk * block), dtype=np.int64)
    x[:, :n] = pcm
    if beam:
        chans = [trellis_channel(x[c], block, bits, beam, cost) for c in range(nch)]
    else:
        assert bits == 4, "the greedy encoder is 4-bit only; use --beam"
        chans = []
        for c in range(nch):
            pred, idx, blocks = 0, 0, []
            for b in range(nblk):
                codes, p2, i2 = greedy_block(x[c, b * block:(b + 1) * block], pred, idx)
                blocks.append((pred, idx, codes))
                pred, idx = p2, i2
            chans.append(blocks)
    body = bytearray()
    for b in range(nblk):
        for c in range(nch):
            pred, idx, codes = chans[c][b]
            body += struct.pack("<hBB", pred, idx, 0) + pack_codes(codes, bits)
    kbps = round(rate * bits * nch / 1000)
    hdr = struct.pack("<4sIIIIIII", b"ADP4", rate, nch, nblk, block, 32, kbps, bits)
    return hdr + bytes(body)


def decode(data):
    """Reference decoder -> ((nch, n) int16-range array, rate)."""
    _, rate, nch, nblk, block, offs, _, bits = struct.unpack_from("<4sIIIIIII", data)
    bits = bits or 4
    diff, nxt = tables(bits)
    out = np.zeros((nch, nblk * block), dtype=np.int64)
    p = offs
    nbytes = block * bits // 8
    for b in range(nblk):
        for c in range(nch):
            pred, idx, _ = struct.unpack_from("<hBB", data, p)
            p += 4
            for k, code in enumerate(unpack_codes(data[p:p + nbytes], block, bits)):
                pred = max(-32768, min(32767, pred + int(diff[idx, code])))
                idx = int(nxt[idx, code])
                out[c, b * block + k] = pred
            p += nbytes
    return out, rate


def read_wav16(path):
    with wave.open(path, "rb") as w:
        assert w.getsampwidth() == 2
        nch, rate = w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.int64)
    return x.reshape(-1, nch).T, rate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("out")
    ap.add_argument("--block", type=int, default=1024)
    ap.add_argument("--bits", type=int, default=4, choices=(2, 3, 4))
    ap.add_argument("--beam", type=int, default=16, help="0 = original greedy encoder")
    ap.add_argument("--cost", default="vidc", choices=("lin", "vidc"))
    a = ap.parse_args()
    pcm, rate = read_wav16(a.wav)
    data = encode(pcm, rate, a.block, a.bits, a.beam, a.cost)
    with open(a.out, "wb") as f:
        f.write(data)
    print(f"{a.out}: {len(data)} bytes, {pcm.shape[0]} ch, {rate} Hz, {a.bits}-bit")


if __name__ == "__main__":
    main()
