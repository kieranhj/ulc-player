"""IMA ADPCM (4-bit) encoder for the Archimedes player, plus a reference decoder.

File format "ADP4" (little-endian), 32-byte header:
  [0]  'ADP4'
  [4]  sample rate (Hz)
  [8]  channels (1 or 2)
  [12] number of blocks
  [16] samples per channel per block (power of two, <= 2048)
  [20] offset of the first block (32)
  [24] nominal kbps
  [28] reserved (0)
Each block, for each channel in turn:
  int16 predictor, uint8 step index, uint8 0, then samples/2 bytes of
  nybbles (low nybble first). The header predictor is the state *before*
  the block's first nybble, so every block decodes on its own.

Usage:  python player/adpcm_encode.py in.wav out.adp [--block 1024]
"""
import argparse
import struct
import wave

import numpy as np

STEPS = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767]
INDEX = [-1, -1, -1, -1, 2, 4, 6, 8]


def step_decode(nib, pred, idx):
    step = STEPS[idx]
    diff = step >> 3
    if nib & 4:
        diff += step
    if nib & 2:
        diff += step >> 1
    if nib & 1:
        diff += step >> 2
    pred = pred - diff if nib & 8 else pred + diff
    pred = max(-32768, min(32767, pred))
    idx = max(0, min(88, idx + INDEX[nib & 7]))
    return pred, idx


def encode_channel_block(x, pred, idx):
    """Greedy IMA encode of one block. Returns (bytes, pred, idx) at block end."""
    nibs = []
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
        nibs.append(nib)
    data = bytes(nibs[i] | nibs[i + 1] << 4 for i in range(0, len(nibs), 2))
    return data, pred, idx


def encode(pcm, rate, block=1024):
    """pcm: (nch, n) int16-range ints. Returns the .adp file bytes."""
    nch, n = pcm.shape
    nblk = (n + block - 1) // block
    x = np.zeros((nch, nblk * block), dtype=np.int64)
    x[:, :n] = pcm
    state = [(0, 0)] * nch
    body = bytearray()
    for b in range(nblk):
        for c in range(nch):
            pred, idx = state[c]
            body += struct.pack("<hBB", pred, idx, 0)
            data, pred, idx = encode_channel_block(x[c, b * block:(b + 1) * block], pred, idx)
            body += data
            state[c] = (pred, idx)
    kbps = round(rate * 4 * nch / 1000)
    hdr = struct.pack("<4sIIIIIII", b"ADP4", rate, nch, nblk, block, 32, kbps, 0)
    return hdr + bytes(body)


def decode(data):
    """Reference decoder -> (nch, n) int16-range array (16-bit, before the 8-bit cut)."""
    _, rate, nch, nblk, block, offs, _, _ = struct.unpack_from("<4sIIIIIII", data)
    out = np.zeros((nch, nblk * block), dtype=np.int64)
    p = offs
    for b in range(nblk):
        for c in range(nch):
            pred, idx, _ = struct.unpack_from("<hBB", data, p)
            p += 4
            for i in range(block // 2):
                byte = data[p + i]
                for k, nib in enumerate((byte & 15, byte >> 4)):
                    pred, idx = step_decode(nib, pred, idx)
                    out[c, b * block + 2 * i + k] = pred
            p += block // 2
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
    a = ap.parse_args()
    pcm, rate = read_wav16(a.wav)
    data = encode(pcm, rate, a.block)
    with open(a.out, "wb") as f:
        f.write(data)
    print(f"{a.out}: {len(data)} bytes, {pcm.shape[0]} ch, {rate} Hz")


if __name__ == "__main__":
    main()
