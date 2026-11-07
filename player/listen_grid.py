"""Listening grid: the same excerpt of each track in every candidate format,
rendered as the Archimedes plays it (through the player's VIDC log tables) and
written back to 16-bit WAV, plus an SNR table against the source.

    python player/listen_grid.py [--start 10] [--seconds 20] [--out player/build/listen-grid]

Needs ulc-codec's ulcdecodetool (ULCDECODE env, or on PATH). NB: build it with
the stream-buffer slide as memmove: the upstream memcpy of overlapping
regions silently corrupts decodes with some compilers.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "spike"))
import adpcm_encode as ae                               # noqa: E402
from ulc_tools import vidc_lin_to_log, vidc_lin8_to_log, vidc_log_to_lin   # noqa: E402

RATE = 20833
TRACKS = ["particle", "shadertoyB", "shadertoyC", "shadertoyD"]
LOG2LIN = np.array([vidc_log_to_lin(c) for c in range(256)], float) * 32768 / 3952
T11 = np.frombuffer(vidc_lin_to_log(11), np.uint8)
T12 = np.frombuffer(vidc_lin_to_log(12), np.uint8)
T8 = np.frombuffer(vidc_lin8_to_log(), np.uint8)


def vidc11(x):        # ULC mono: the 11-bit lapping output
    return LOG2LIN[T11[np.clip(np.floor(x / 32).astype(int), -1024, 1023) + 1024]]


def vidc12(x):        # ADPCM: the 12-bit predictor
    return LOG2LIN[T12[np.clip(np.floor(x / 16).astype(int), -2048, 2047) + 2048]]


def vidc8(x):         # ULC stereo: 8-bit linear after M/S
    return LOG2LIN[T8[np.clip(np.floor(x / 256).astype(int), -128, 127) & 0xFF]]


def read(path):
    with wave.open(path) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels())
    return x.T.astype(float)


def write(path, x):
    x = np.atleast_2d(x)
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[0]); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(np.clip(np.round(x.T), -32768, 32767).astype("<i2").tobytes())


def snr(ref, y):
    e = ref - y
    return 10 * np.log10((ref ** 2).sum() / max((e ** 2).sum(), 1e-9))


def ulc_decode(path, tmp):
    exe = os.environ.get("ULCDECODE") or shutil.which("ulcdecodetool")
    out = os.path.join(tmp, "u.wav")
    subprocess.run([exe, path, out], check=True, capture_output=True)
    return read(out)


def align(ref, y):
    a = ref[0, : RATE * 4]
    c = np.correlate(y[0, : RATE * 4 + 4096], a, "valid")
    lag = int(np.abs(c[:4097]).argmax())
    return y[:, lag:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=10)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--out", default=os.path.join(HERE, "build", "listen-grid"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    s0, n = int(a.start * RATE), int(a.seconds * RATE)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for t in TRACKS:
            for chans in ("mono", "stereo"):
                pcm, _ = ae.read_wav16(os.path.join(ROOT, "music", f"{t}-{chans}.wav"))
                src = pcm.astype(float)
                ex = pcm[:, s0:s0 + n]
                ref = ex.astype(float)
                cands = [("00-source-vidc", vidc12(ref), "8.0")]
                if chans == "mono":
                    cands.append(("01-adpcm4-greedy", vidc12(ae.decode(ae.encode(ex, RATE, 1024, 4, 0))[0].astype(float)[:, :n]), "4.0"))
                for b in ((4, 3, 2) if chans == "mono" else (4, 3)):
                    y = ae.decode(ae.encode(ex, RATE, 1024, b, 16, "vidc"))[0].astype(float)[:, :n]
                    cands.append((f"0{6 - b}-adpcm{b}-trellis", vidc12(y), f"{b}.0"))
                    y = ae.decode(ae.encode(ex, RATE, 1024, b, 16, "vidc", True))[0].astype(float)[:, :n]
                    cands.append((f"0{6 - b}m-adpcm{b}-modes", vidc12(y), f"{b}.0"))
                for k in ((32, 48, 64, 96, 128) if chans == "mono" else (64, 96, 128)):
                    f = os.path.join(ROOT, "music", f"{t}-{chans}-{k}k.ulc")
                    y = align(src, ulc_decode(f, tmp))[:, s0:s0 + n]
                    out = vidc11(y) if chans == "mono" else vidc8(y)
                    bps = os.path.getsize(f) * 8 / (src.shape[1] * src.shape[0])
                    cands.append((f"{10 + k // 16:02d}-ulc{k}k", out, f"{bps:.1f}"))
                for name, y, bps in cands:
                    write(os.path.join(a.out, f"{t}-{chans}-{name}.wav"), y)
                    rows.append((t, chans, name.split("-", 1)[1], bps, snr(ref, y)))
                    print(f"{t:11s} {chans:6s} {name.split('-', 1)[1]:16s} {bps:>4s} bits/sample/ch  SNR {rows[-1][4]:5.1f} dB",
                          flush=True)
    with open(os.path.join(a.out, "snr.tsv"), "w") as f:
        f.write("track\tchannels\tformat\tbits_per_sample_per_channel\tsnr_db\n")
        for r in rows:
            f.write("\t".join(map(str, r[:4])) + f"\t{r[4]:.1f}\n")


if __name__ == "__main__":
    main()
