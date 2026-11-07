"""Compare the last check_vs_cdec.py 11-bit output with a saved one.

    python spike/vs_golden.py [spike/build/golden_arm11.npy]
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
g = np.load(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "build", "golden_arm11.npy"))
a = np.load(os.path.join(HERE, "build", "check_arm11.npy"))
n = min(len(a), len(g))
a, g = a[:n], g[:n]
d = a - g
snr = 10 * np.log10((g ** 2).sum() / max((d ** 2).sum(), 1e-9))
print(f"vs golden: {n} samples, {(d == 0).mean() * 100:.2f}% identical, max |diff| {np.abs(d).max():.0f} "
      f"(11-bit LSB), SNR {snr:.1f} dB")
