"""Host-side helpers for the ulc decoder spike.

- synth_music():  a deterministic music-like test signal (no WAVs needed).
- read_wav():     16-bit PCM WAV reader (mono/stereo).
- MiniEncoder:    a deliberately simple ULC2 encoder (sine-window MDCT,
                  per-band quantisers, zero runs, noise fill, optional forced
                  window switching). It is NOT the real ulc-codec encoder; it
                  exists so the ARM decoder can be checked and timed on a
                  valid stream when the real encoder is not available.
- RefDecoder:     float reference decoder that follows libulc/ulcDecoder.c.
                  With arm_precision=True (default) the coefficient stage
                  mirrors the GBA/ARM decoder exactly: its dequantiser LUT is
                  indexed by -nybble, so tonal coefficients come out negated
                  (noise fill does not), values are truncated 1.15 integers,
                  quantisers 2^-(5+18) and finer decode to 0, and noise fill is seeded from
                  the reader registers with the ARM's integer amplitudes. Only
                  the IMDCT/windowing then differ (float here, fixed-point there).
- vidc_lin8_to_log(): 256-entry table, signed 8-bit linear -> VIDC1 log byte.
"""
import math
import os
import struct
import wave

import numpy as np

MAGIC = 0x32434C55  # 'ULC2'


# --------------------------------------------------------------------------
# Test audio

def synth_music(rate, seconds, stereo, seed=7):
    """Chords, bass, lead, kick, hi-hat. Peak about 0.5."""
    rng = np.random.default_rng(seed)
    n = int(rate * seconds)
    t = np.arange(n) / rate
    out = np.zeros((2, n))
    bpm = 128.0
    beat = 60.0 / bpm

    def note(freq, start, dur, amp, harm, pan):
        i0, i1 = int(start * rate), min(n, int((start + dur) * rate))
        if i0 >= n:
            return
        tt = t[i0:i1] - start
        env = np.minimum(1.0, tt / 0.01) * np.exp(-tt * 3.0 / dur)
        sig = sum(a * np.sin(2 * np.pi * freq * (k + 1) * tt) for k, a in enumerate(harm))
        out[0, i0:i1] += sig * env * amp * (1 - pan)
        out[1, i0:i1] += sig * env * amp * (1 + pan)

    chords = [(57, 60, 64), (53, 57, 60), (55, 59, 62), (52, 55, 59)]
    mtof = lambda m: 440.0 * 2 ** ((m - 69) / 12)
    bar = 4 * beat
    nbars = int(seconds / bar) + 1
    for b in range(nbars):
        ch = chords[b % 4]
        for i, m in enumerate(ch):
            note(mtof(m), b * bar, bar, 0.08, (1, .5, .3, .2, .1), (i - 1) * 0.4)
        for q in range(8):
            note(mtof(ch[0] - 24), b * bar + q * beat / 2, beat / 2, 0.15, (1, .6, .2), 0)
        for q in range(16):
            if rng.random() < 0.6:
                m = ch[rng.integers(3)] + 12 * rng.integers(1, 3)
                note(mtof(m), b * bar + q * beat / 4, beat / 4, 0.06, (1, .3, .15, .08), rng.uniform(-.6, .6))
        for q in range(4):  # kick
            i0 = int((b * bar + q * beat) * rate)
            if i0 < n:
                tt = t[i0:min(n, i0 + int(0.25 * rate))] - t[i0]
                k = 0.35 * np.sin(2 * np.pi * (50 + 120 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 12)
                out[:, i0:i0 + len(k)] += k
        for q in range(8):  # hat
            i0 = int((b * bar + q * beat / 2 + beat / 4) * rate)
            if i0 < n:
                L = min(n - i0, int(0.05 * rate))
                h = rng.normal(0, 0.05, L) * np.exp(-np.arange(L) / (0.012 * rate))
                h = np.diff(h, prepend=0)
                out[0, i0:i0 + L] += h * 0.7
                out[1, i0:i0 + L] += h
    out *= 0.5 / np.max(np.abs(out))
    return out if stereo else out.mean(axis=0, keepdims=True)


def read_wav(path, rate=None):
    with wave.open(path, "rb") as w:
        assert w.getsampwidth() == 2, "16-bit WAV only"
        nch, sr = w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64) / 32768
    x = x.reshape(-1, nch).T
    if rate and rate != sr:  # crude linear resample
        n = int(x.shape[1] * rate / sr)
        pos = np.arange(n) * sr / rate
        x = np.array([np.interp(pos, np.arange(x.shape[1]), c) for c in x])
    return x, (rate or sr)


# --------------------------------------------------------------------------
# Transforms

_dct4_cache = {}


def dct4(x):
    n = len(x)
    m = _dct4_cache.get(n)
    if m is None:
        k = np.arange(n) + 0.5
        m = np.cos(np.pi / n * np.outer(k, k))
        _dct4_cache[n] = m
    return m @ x


def sine_window(n2):
    return np.sin(np.pi * (np.arange(n2) + 0.5) / n2)


# exact table from the decoder (built programmatically to avoid typos)
DECIMATION = [
    (0 + 8),
    0,
    (1 + 8) | 1 << 4,
    1 | (1 + 8) << 4,
    (2 + 8) | 2 << 4 | 1 << 8,
    2 | (2 + 8) << 4 | 1 << 8,
    1 | (2 + 8) << 4 | 2 << 8,
    1 | 2 << 4 | (2 + 8) << 8,
    (3 + 8) | 3 << 4 | 2 << 8 | 1 << 12,
    3 | (3 + 8) << 4 | 2 << 8 | 1 << 12,
    2 | (3 + 8) << 4 | 3 << 8 | 1 << 12,
    2 | 3 << 4 | (3 + 8) << 8 | 1 << 12,
    1 | (3 + 8) << 4 | 3 << 8 | 2 << 12,
    1 | 3 << 4 | (3 + 8) << 8 | 2 << 12,
    1 | 2 << 4 | (3 + 8) << 8 | 3 << 12,
    1 | 2 << 4 | 3 << 8 | (3 + 8) << 12,
]


def subblocks(pattern):
    """[(shift, is_transient), ...] for a decimation pattern word."""
    out = []
    while True:
        out.append((pattern & 7, bool(pattern & 8)))
        pattern >>= 4
        if not pattern:
            return out


# --------------------------------------------------------------------------
# Nybble writer

class Nybbles:
    def __init__(self):
        self.n = []

    def put(self, *vals):
        for v in vals:
            assert 0 <= v <= 15, v
            self.n.append(v)

    def align(self):
        if len(self.n) & 1:
            self.n.append(0)

    def tobytes(self):
        self.align()
        return bytes(self.n[i] | self.n[i + 1] << 4 for i in range(0, len(self.n), 2))


# --------------------------------------------------------------------------
# Mini encoder

class MiniEncoder:
    """Simple ULC2 encoder: MDCT, per-16-band quantiser, zero runs, noise fill.

    `coarse` shifts every band quantiser up (bigger steps, fewer nybbles).
    `transient_every` forces a window-switched block every k blocks so the
    decoder's sub-block paths get exercised.
    """

    def __init__(self, block_size, nchan, coarse=2.0, transient_every=0, noise=True):
        self.N = block_size
        self.nchan = nchan
        self.coarse = coarse
        self.transient_every = transient_every
        self.noise = noise

    @staticmethod
    def quant_code(q):
        return [q] if q <= 13 else [0xE, q - 14]

    def mdct(self, frame):
        """Coefs such that the ULC IMDCT (no scaling, inverted phase) restores it."""
        n2 = len(frame)
        n = n2 // 2
        x = frame * sine_window(n2)
        a, b, c, d = x[:n // 2], x[n // 2:n], x[n:3 * n // 2], x[3 * n // 2:]
        u = np.concatenate([-c[::-1] - d, a - b[::-1]])
        return -dct4(u) * (2.0 / n)

    def encode_coefs(self, ns, c):
        n = len(c)
        band = max(4, n // 128)
        nb = n // band
        qs = []
        for b in range(nb):
            amax = np.max(np.abs(c[b * band:(b + 1) * band]))
            if amax <= 0:
                qs.append(None)
                continue
            q = int(math.floor(-math.log2(amax / 49.0) - 5 - self.coarse
                               + 1.5 * b / nb))  # coarser towards HF
            qs.append(max(0, min(26, q)))
        # quantise
        vals = np.zeros(n, dtype=int)
        qcoef = [None] * n
        for b in range(nb):
            if qs[b] is None:
                continue
            Q = 2.0 ** -(5 + qs[b])
            for i in range(b * band, (b + 1) * band):
                v = math.sqrt(abs(c[i]) / Q)
                v = int(round(v))
                if v < 2:
                    v = 2 if v == 1 and abs(c[i]) / Q > 2.5 else 0
                v = min(v, 7)
                vals[i] = v if c[i] >= 0 else -v
                qcoef[i] = qs[b]
        last = n
        while last > 0 and vals[last - 1] == 0:
            last -= 1
        if last == 0:
            ns.put(0xE, 0xF)                  # silent channel: [Fh,]Eh,Fh
            return
        first_q = next(qcoef[i] for i in range(n) if vals[i])
        ns.put(*self.quant_code(first_q))
        cur = first_q
        i = 0
        while i < last:
            if vals[i] == 0:
                j = i
                while j < last and vals[j] == 0:
                    j += 1
                run = j - i
                if self.noise and run >= 16 and i > n // 4:
                    rms = float(np.sqrt(np.mean(c[i:j] ** 2)))
                    Q = 2.0 ** -(5 + cur)
                    lvl = rms / (Q / 4)
                    v = int(round(math.sqrt(max(lvl, 0)))) - 1
                    if 0 <= v <= 7:
                        r = min(run, 527) - 16
                        ns.put(0x8, (r >> 5) & 15, (r >> 1) & 15, (v << 1) | (r & 1))
                        i += r + 16
                        continue
                while run > 0:
                    if run >= 33:
                        k = min(run, 288)
                        ns.put(0x1, (k - 33) >> 4, (k - 33) & 15)
                    else:
                        k = min(run, 16)
                        ns.put(0x0, k - 1)
                    run -= k
                    i += k
                continue
            if qcoef[i] != cur:
                cur = qcoef[i]
                ns.put(0xF, *self.quant_code(cur))
            v = vals[i]
            ns.put(v & 15)
            i += 1
        if last < n:
            tail = c[last:]
            Q = 2.0 ** -(5 + cur)
            amp = float(np.sqrt(np.mean(tail ** 2))) if self.noise else 0.0
            z = int(round(math.sqrt(amp / (Q / 16)))) - 1 if amp > 0 else -1
            if self.noise and 0 <= z <= 15 and (n - last) >= 16:
                ns.put(0xF, 0xF, z, 0x1, 0x8)  # decay r = 0x18
            else:
                ns.put(0xF, 0xE, 0xF)

    def encode(self, pcm):
        """pcm: (nchan, nsamples) float. Returns the .ulc file bytes."""
        N = self.N
        nchan = self.nchan
        if nchan == 2:  # M/S (decoder: L = M+S, R = M-S)
            pcm = np.array([(pcm[0] + pcm[1]) / 2, (pcm[0] - pcm[1]) / 2])
        nblk = pcm.shape[1] // N + 1
        x = np.zeros((nchan, (nblk + 2) * N))
        x[:, N:N + pcm.shape[1]] = pcm
        body = bytearray()
        maxb = 0
        for blk in range(nblk):
            ns = Nybbles()
            trans = self.transient_every and blk % self.transient_every == self.transient_every - 1
            if trans:
                sel = 4 + (blk // self.transient_every) % 12   # cycle 0100b..1111b
                ns.put(0x8 | 1, sel)                          # overlap scale 1
                pat = DECIMATION[sel]
            else:
                ns.put(0)
                pat = DECIMATION[0]
            for ch in range(nchan):
                pos = blk * N
                for shift, _ in subblocks(pat):
                    m = N >> shift
                    frame = x[ch, pos + N // 2 - m // 2: pos + N // 2 + m + m // 2]
                    self.encode_coefs(ns, self.mdct(frame))
                    pos += m
            ns.align()
            b = ns.tobytes()
            maxb = max(maxb, len(b))
            body += b
        hdr = struct.pack("<IHHIIHHI", MAGIC, N, maxb, nblk, 0, nchan, 0, 24)
        return hdr + bytes(body)


def set_header_rate(data, rate, kbps):
    d = bytearray(data)
    struct.pack_into("<I", d, 12, rate)
    struct.pack_into("<H", d, 18, int(round(kbps)))
    return bytes(d)


# --------------------------------------------------------------------------
# Reference decoder

def _u32(x):
    return x & 0xFFFFFFFF


def _ror(x, n):
    return _u32((x >> n) | (x << (32 - n)))


def _xorshift(s):
    s = _u32(s ^ (s << 13))
    s ^= s >> 17
    s = _u32(s ^ (s << 5))
    return s


class Reader:
    """Nybble reader that also models the ARM decoder's r6/r7 (for seeds)."""

    def __init__(self, data, offset, base_addr):
        self.d = data
        self.p = offset * 2           # nybble position in data
        self.base = base_addr         # address of data[0] in the emulated machine

    def get(self):
        b = self.d[self.p >> 1]
        v = (b >> 4) if (self.p & 1) else (b & 15)
        self.p += 1
        return v

    def arm_seed(self):
        addr = self.base + (self.p >> 1)
        wa = addr & ~3
        k = (addr - wa) * 2 + (self.p & 1)
        off = wa - self.base
        word = int.from_bytes(bytes(self.d[off + i] if 0 <= off + i < len(self.d) else 0
                                    for i in range(4)), "little")
        r6 = wa | (k << 29)
        r7 = word >> (4 * k)
        return _u32(r6 ^ _ror(r7, 23))

    def byte_align(self):
        self.p = (self.p + 1) & ~1


class RefDecoder:
    def __init__(self, data, base_addr, arm_precision=True):
        self.arm = arm_precision      # mirror the ARM coefficient stage exactly
        magic, self.N, _, self.nblk, self.rate, self.nchan, _, offs = struct.unpack_from("<IHHIIHHI", data)
        assert magic == MAGIC
        self.rd = Reader(data, offs, base_addr)
        self.lap = np.zeros((self.nchan, self.N // 2))
        self.last_sub = 0

    def _coefs(self, n):
        rd = self.rd
        out = np.zeros(n)
        i = 0

        def quant():
            q = rd.get()
            if q == 0xF:
                return "noise"
            if q == 0xE:
                q += rd.get()
                if q == 0xE + 0xF:
                    return "stop"
            return q

        q = quant()
        if q == "stop":
            return out
        Q = 2.0 ** -(5 + q)
        log2q = q
        while i < n:
            v = rd.get()
            if v not in (0, 1, 8, 15):
                s = v - 16 if v & 8 else v
                if not self.arm:
                    out[i] = s * abs(s) * Q
                elif log2q < 18:                    # LUT holds -s|s|<<24 (sic), ASR #(14+Q); Q>=18 -> LSR #32
                    out[i] = ((-s * abs(s)) << 24 >> (14 + log2q)) / 32768.0
                i += 1
            elif v == 0:
                i += rd.get() + 1
            elif v == 1:
                k = rd.get() << 4
                k |= rd.get()
                i += k + 33
            elif v == 8:
                k = rd.get() << 4
                k |= rd.get()
                x = rd.get()
                k = ((k << 1) | (x & 1)) + 16
                lvl = ((x >> 1) + 1) ** 2 * Q / 4
                fx = ((x >> 1) + 1) ** 2 << 8            # ARM: (v+1)^2<<8 >> Log2Q, rounded
                if log2q >= 32 or ((fx >> log2q) + ((fx >> (log2q - 1)) & 1 if log2q else 0)) == 0:
                    i += k                               # rounds to zero -> zeros run
                    continue
                if self.arm:                            # rounded 1.15 level
                    lvl = ((fx >> log2q) + ((fx >> (log2q - 1)) & 1 if log2q else 0)) / 32768.0
                seed = rd.arm_seed()
                for j in range(k):
                    seed = _xorshift(seed)
                    if seed & 0x80000000:
                        lvl = -lvl
                    out[i + j] = lvl
                i += k
            else:
                q = quant()
                if q == "stop":
                    break
                if q == "noise":
                    z = rd.get() + 1
                    r = rd.get() << 4
                    r |= rd.get()
                    amp = z * z * Q / 16
                    dec = 1.0 - r * r * 2.0 ** -19 if r else 1.0
                    seed = rd.arm_seed()
                    if self.arm:                        # integer Scale, truncated each step
                        S = z * z << 6
                        D = ((1 << 32) - (r * r << 13)) & 0xFFFFFFFF if r else 0xFFFFFFFF
                        while i < n:
                            seed = _xorshift(seed)
                            v = (S >> log2q) / 32768.0
                            out[i] = -v if seed & 0x80000000 else v
                            S = (S * D) >> 32
                            i += 1
                        break
                    while i < n:
                        seed = _xorshift(seed)
                        out[i] = -amp if seed & 0x80000000 else amp
                        amp *= dec
                        i += 1
                    break
                Q = 2.0 ** -(5 + q)
                log2q = q
        return out

    def decode_block(self):
        rd = self.rd
        N = self.N
        wc = rd.get()
        wc |= (rd.get() << 4) if wc & 8 else (1 << 4)
        pat = DECIMATION[wc >> 4]
        dst = np.zeros((self.nchan, N))
        last_sub = self.last_sub
        for ch in range(self.nchan):
            last_sub = self.last_sub
            lap = self.lap[ch]
            pos = 0
            for shift, trans in subblocks(pat):
                m = N >> shift
                c = self._coefs(m)
                ov = m >> (wc & 7) if trans else m
                ov = min(ov, last_sub)
                last_sub = m
                tmp = dct4(c)
                # Fourier_IMDCT_Template, transcribed
                out = np.zeros(m)
                lp = lap[:m // 2]
                Lrev = lp[::-1]
                nc = (m - ov) // 2
                out[:nc] = Lrev[:nc]
                out[m - nc:] = tmp[m // 2:m // 2 + nc][::-1]
                if ov:
                    k = np.arange(m // 2 - nc)
                    ang = (k + 0.5) / ov * (np.pi / 2)
                    cc, ss = np.cos(ang), np.sin(ang)
                    a, b = Lrev[nc:], tmp[m // 2 + nc:m]
                    out[nc:m // 2] = cc * a - ss * b
                    out[m // 2:m - nc] = (ss * a + cc * b)[::-1]
                newlap = tmp[:m // 2].copy()
                if m == N:
                    dst[ch] = out
                    lap[:] = newlap
                    break
                # sub-block lapping (ulcDecoder.c)
                lap[:m // 2] = newlap
                navail = (N - m) // 2
                full = lap  # length N/2; LapDst/LapSrc start at Lap + N/2
                # emulate with explicit pointer arithmetic on the full lap array
                # (the C code writes BufLap[i]=BufTmp[i] for i<m/2 first)
                dec = out
                o = []
                src = N // 2
                dsti = N // 2
                if m <= navail:
                    for _ in range(m):
                        src -= 1
                        o.append(full[src])
                    for _ in range(navail - m):
                        src -= 1
                        dsti -= 1
                        full[dsti] = full[src]
                    for j in range(m):
                        dsti -= 1
                        full[dsti] = dec[j]
                else:
                    for _ in range(navail):
                        src -= 1
                        o.append(full[src])
                    o.extend(dec[:m - navail])
                    for j in range(navail):
                        dsti -= 1
                        full[dsti] = dec[m - navail + j]
                dst[ch, pos:pos + m] = o
                pos += m
        self.last_sub = last_sub
        if self.nchan == 2:
            a, b = dst[0].copy(), dst[1].copy()
            dst[0], dst[1] = a + b, a - b
        rd.byte_align()
        return dst


# --------------------------------------------------------------------------
# VIDC

def vidc_log_to_lin(v):
    sign, point, chord = v & 1, (v >> 1) & 15, v >> 5
    t = (1 << (chord + 4)) - 16 + (point << chord)
    return -t if sign else t


def vidc_lin8_to_log():
    """Index = signed 8-bit sample as an unsigned byte."""
    lut = [(vidc_log_to_lin(c), c) for c in range(256)]
    out = bytearray(256)
    for i in range(256):
        s = i - 256 if i >= 128 else i
        target = s * 3952 / 128.0
        out[i] = min(lut, key=lambda e: (abs(e[0] - target), e[1]))[1]
    return bytes(out)


def vidc_lin_to_log(bits):
    """VIDC log byte for each signed `bits`-bit linear value, nearest to the
    bin centre (i + 0.5) so the table adds no truncation bias. Index =
    value + 2**(bits-1), so asm can index from the table's centre."""
    lut = sorted(((vidc_log_to_lin(c), c) for c in range(256)))
    vals = np.array([v for v, _ in lut], dtype=float)
    codes = [c for _, c in lut]
    half = 1 << (bits - 1)
    out = bytearray(2 * half)
    for i in range(-half, half):
        target = (i + 0.5) * 3952.0 / half
        j = int(np.abs(vals - target).argmin())
        out[i + half] = codes[j]
    return bytes(out)


def lin11_to_lin8():
    """11-bit index -> the 8-bit truncation the decoder made before the VIDC
    lookup was fused into it (value >> 3). Index = value + 1024."""
    return bytes(((i - 1024) >> 3) & 0xFF for i in range(2048))


def lin11_low8():
    """11-bit index -> its low 8 bits: with lin11_to_lin8 this recovers the
    decoder's full 11-bit output in two runs (test harness only)."""
    return bytes((i - 1024) & 0xFF for i in range(2048))


IMA_STEPS = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
             50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
             253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
             1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
             3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
             12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767]
IMA_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8]


def ima_diff(idx, n):
    step = IMA_STEPS[idx]
    d = step >> 3
    if n & 4:
        d += step
    if n & 2:
        d += step >> 1
    if n & 1:
        d += step >> 2
    return -d if n & 8 else d


def ima_table():
    """89 rows x 16 words: diff << 13 | next row's byte offset (row * 64)."""
    out = bytearray()
    for idx in range(89):
        for n in range(16):
            nxt = min(88, max(0, idx + IMA_INDEX[n & 7]))
            out += struct.pack("<I", ((ima_diff(idx, n) << 13) | (nxt * 64)) & 0xFFFFFFFF)
    return bytes(out)


def vidc_to_scope():
    """VIDC log byte -> signed scope offset (+-64 at full scale)."""
    return bytes(int(round(vidc_log_to_lin(c) * 64 / 3952)) & 0xFF for c in range(256))


FFT_TWQ = int(os.environ.get("FFT_TWQ", "12"))


def fourier_tables():
    """Data files fourier.asm includes, by name. Every build writes them into
    its own build/ directory (write_fourier_tables)."""
    tw = bytearray()
    m = 16
    while m <= 1024:                  # FFT stage twiddles: per stage M, k < M/4
        for k in range(m // 4):
            a = 2 * math.pi * k / m
            tw += struct.pack("<ii", round(math.cos(a) * (1 << FFT_TWQ)), round(math.sin(a) * (1 << FFT_TWQ)))
        m *= 2
    def bitrev(j, bits):
        return int(format(j, f"0{bits}b")[::-1], 2) if bits else 0
    index, tabs = [0] * 11, bytearray()
    for lg in range(4, 11):                     # FFT N = 16..1024: N/4 entries each
        index[lg] = 44 + len(tabs)
        for j in range((1 << lg) // 4):
            tabs += struct.pack("<I", 8 * bitrev(j, lg - 2))
    br = struct.pack("<11I", *index) + bytes(tabs)
    r4 = bytearray()
    q = 1 << FFT_TWQ
    for L in (8, 32, 128):                      # radix-4 stages L -> 4L
        for k in range(L):
            t = 2 * math.pi * k / (4 * L)
            a2, a3 = (2 * t, 3 * t) if k < L // 2 else (2 * t - math.pi / 2, 3 * t - math.pi)
            for a in (a2, t, a3):               # w2, w1, w3: order of use
                r4 += struct.pack("<ii", round(math.cos(a) * q), round(math.sin(a) * q))
    return {"fft_tw.bin": bytes(tw), "fft_br.bin": br, "fft_r4tw.bin": bytes(r4), "fourier_cfg.inc": (".equ FFT_TWQ, %d" % FFT_TWQ + chr(10)).encode()}


def write_fourier_tables(build_dir):
    for name, data in fourier_tables().items():
        with open(os.path.join(build_dir, name), "wb") as f:
            f.write(data)
