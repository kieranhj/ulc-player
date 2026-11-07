"""Proposal 3 prototype: two-band (G.722-style) ADPCM with no multiplies.

The input is split by an integer lifting wavelet into a low band (0-5 kHz) and
a high band (5-10 kHz), each at half rate. The low band is coded with trellis
IMA ADPCM; the high band with ADPCM at fewer bits, with a per-block envelope
driving a noise generator (noise substitution), or not at all. The decoder
(simulated here bit-exactly in integers) runs the band decoders and the
inverse lifting, then the 12-bit VIDC table as the ADPCM player does.

Filters (both integer, shifts and adds only):
  53    LeGall 5/3:  d = xo - (xe0 + xe1) >> 1;  s = xe + (d0 + d1 + 2) >> 2
  137   Deslauriers-Dubuc 13/7: 4-tap predict (-1, 9, 9, -1)/16 and update
        (-1, 9, 9, -1)/32; 9x is one ADD with a shifted operand on ARM.

    python player/subband.py [--start 10] [--seconds 20]   # scores every variant

Result (CODECS.md, proposal 3): the low band at half rate is less predictable,
so 4-bit ADPCM on it tops out at 27-33 dB; the split only wins at 2 bps, and
full-band ADPCM with per-block predictor modes beats it there too.
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import adpcm_encode as ae                               # noqa: E402

NOISE_BLOCK = 64          # high-band samples per envelope value (6 ms)
GAIN_STEPS = 4            # envelope resolution: steps per octave (1.5 dB)


def _ext(a, i):
    """Index with whole-sample symmetric extension (a[-1] = a[1])."""
    n = len(a)
    i = np.abs(i)
    i = np.where(i >= n, 2 * (n - 1) - i, i)
    return a[i]


def analyse(x, filt):
    x = np.asarray(x, np.int64)
    if len(x) & 1:
        x = np.append(x, x[-1])
    e, o = x[0::2], x[1::2]
    i = np.arange(len(o))
    if filt == "53":
        d = o - ((e + _ext(e, i + 1)) >> 1)
        s = e + ((_ext(d, i - 1) + d + 2) >> 2)
    else:
        d = o - ((9 * (e + _ext(e, i + 1)) - (_ext(e, i - 1) + _ext(e, i + 2)) + 8) >> 4)
        s = e + ((9 * (_ext(d, i - 1) + d) - (_ext(d, i - 2) + _ext(d, i + 1)) + 16) >> 5)
    return s, d


def synthesise(s, d, filt):
    s, d = np.asarray(s, np.int64), np.asarray(d, np.int64)
    i = np.arange(len(d))
    if filt == "53":
        e = s - ((_ext(d, i - 1) + d + 2) >> 2)
        o = d + ((e + _ext(e, i + 1)) >> 1)
    else:
        e = s - ((9 * (_ext(d, i - 1) + d) - (_ext(d, i - 2) + _ext(d, i + 1)) + 16) >> 5)
        o = d + ((9 * (e + _ext(e, i + 1)) - (_ext(e, i - 1) + _ext(e, i + 2)) + 8) >> 4)
    x = np.empty(2 * len(s), np.int64)
    x[0::2], x[1::2] = e, o
    return x


def adpcm_band(x, bits, beam=16):
    """Trellis-code one band (linear cost) and return what the decoder rebuilds."""
    x = np.clip(x, -32768, 32767)
    n = len(x)
    data = ae.encode(x[None, :], 10416, 1024, bits, beam, "lin")
    return ae.decode(data)[0][0, :n], (len(data) - 32) * 8


def noise_band(d, seed=1):
    """Envelope + noise: per block, log2 RMS in 1/GAIN_STEPS octave steps (6 bits)."""
    n = len(d)
    nb = (n + NOISE_BLOCK - 1) // NOISE_BLOCK
    pad = np.zeros(nb * NOISE_BLOCK)
    pad[:n] = d
    rms = np.sqrt((pad.reshape(nb, NOISE_BLOCK) ** 2).mean(1))
    g = np.clip(np.round(np.log2(np.maximum(rms, 1)) * GAIN_STEPS), 0, 63).astype(int)
    amp = 2.0 ** (g / GAIN_STEPS) * np.sqrt(3)          # uniform noise with that RMS
    amp[g == 0] = 0
    # The decoder: 16-bit xorshift, then a gain table indexed by (g, rand byte).
    r, state = np.empty(nb * NOISE_BLOCK), seed
    for k in range(len(r)):
        state ^= (state << 7) & 0xFFFF
        state ^= state >> 9
        state ^= (state << 8) & 0xFFFF
        r[k] = ((state & 0xFF) - 127.5) / 127.5
    y = (r.reshape(nb, NOISE_BLOCK) * amp[:, None]).reshape(-1)[:n]
    return np.round(y).astype(np.int64), nb * 6


APCM_BLOCK = 32           # high-band samples per APCM scale (4 bits, 1.5 dB steps)
APCM_LEVELS = {1: [0.798], 2: [0.4528, 1.510]}      # Lloyd-Max, unit Gaussian


def apcm_band(d, bits):
    """Adaptive PCM, no prediction (the high band is close to white): per block
    a 6-bit log scale, then a sign + magnitude code per sample. The decoder is
    one LDR from a [scale][code] table, as the ADPCM loops are."""
    lv = np.array(APCM_LEVELS[bits])
    lv = np.concatenate([lv, -lv])                  # code -> level
    n = len(d)
    nb = (n + APCM_BLOCK - 1) // APCM_BLOCK
    x = np.zeros(nb * APCM_BLOCK)
    x[:n] = d
    x = x.reshape(nb, APCM_BLOCK)
    out = np.zeros_like(x)
    gains = 2.0 ** (np.arange(64) / GAIN_STEPS)
    for b in range(nb):
        rms = np.sqrt((x[b] ** 2).mean())
        g0 = int(np.clip(np.round(np.log2(max(rms, 1)) * GAIN_STEPS), 0, 63))
        best = None
        for g in range(max(0, g0 - 6), min(64, g0 + 4)):       # pick the scale by search
            q = gains[g] * lv
            y = q[np.abs(x[b][:, None] - q[None, :]).argmin(1)]
            e = ((x[b] - y) ** 2).sum()
            if best is None or e < best[0]:
                best = (e, y)
        out[b] = best[1]
    return np.round(out.reshape(-1)[:n]).astype(np.int64), nb * 6 + n * bits


VARIANTS = {
    # name: (filter, low bits, high: bits | "noise" | 0)
    "sb53-4+0": ("53", 4, 0),
    "sb53-4+noise": ("53", 4, "noise"),
    "sb53-3+2": ("53", 3, 2),
    "sb53-4+2": ("53", 4, 2),
    "sb137-4+0": ("137", 4, 0),
    "sb137-4+noise": ("137", 4, "noise"),
    "sb137-3+2": ("137", 3, 2),
    "sb137-4+2": ("137", 4, 2),
    "sb137-4+a1": ("137", 4, "a1"),
    "sb137-4+a2": ("137", 4, "a2"),
    "sb137-3+a2": ("137", 3, "a2"),
}


def code(x, variant):
    """x: one channel, int16 range. -> (decoded linear int64, bits used)."""
    filt, lb, hb = VARIANTS[variant]
    s, d = analyse(x, filt)
    sq, nbits = adpcm_band(s, lb)
    if hb == "noise":
        dq, hbits = noise_band(d)
    elif isinstance(hb, str):
        dq, hbits = apcm_band(d, int(hb[1:]))
    elif hb:
        dq, hbits = adpcm_band(d, hb)
    else:
        dq, hbits = np.zeros_like(d), 0
    return synthesise(sq, dq, filt)[:len(x)], nbits + hbits


def main():
    import listen_grid as lg
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=10)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--out", default=os.path.join(HERE, "build", "listen-grid"))
    ap.add_argument("--variants", default=",".join(VARIANTS))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    s0, n = int(a.start * lg.RATE), int(a.seconds * lg.RATE)
    names = a.variants.split(",")
    rows, totals = [], {v: [] for v in names}
    for t in lg.TRACKS:
        pcm, _ = ae.read_wav16(os.path.join(lg.ROOT, "music", f"{t}-mono.wav"))
        ex = pcm[0, s0:s0 + n].astype(np.int64)
        ref = ex.astype(float)
        # Losslessness check of the filters.
        for f in ("53", "137"):
            assert np.array_equal(synthesise(*analyse(ex, f), f)[:n], ex), f
        for v in names:
            y, nbits = code(ex, v)
            out = lg.vidc12(y.astype(float))
            bps = nbits / n
            snr = lg.snr(ref, out)
            totals[v].append((bps, snr))
            lg.write(os.path.join(a.out, f"{t}-mono-30-{v}.wav"), out)
            rows.append((t, "mono", v, f"{bps:.2f}", snr))
            print(f"{t:11s} {v:14s} {bps:4.2f} bits/sample  SNR {snr:5.1f} dB", flush=True)
    print("\nmean over tracks:")
    for v in names:
        b, s = np.mean(totals[v], 0)
        print(f"  {v:14s} {b:4.2f} bits/sample  SNR {s:5.1f} dB")
    with open(os.path.join(a.out, "subband.tsv"), "w") as f:
        f.write("track\tchannels\tformat\tbits_per_sample_per_channel\tsnr_db\n")
        for r in rows:
            f.write("\t".join(map(str, r[:4])) + f"\t{r[4]:.1f}\n")


if __name__ == "__main__":
    main()
