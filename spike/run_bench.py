"""ulc IMDCT spike: build, run on emulated ARM2 / ARM250 / ARM3, verify, report.

Drives arculator-headless (the Arculator MCP's emulator) through its Python
client. For each machine: boot RISC OS 3.11, run bench,ff8 from the command
line, and at every hit of the `mark` breakpoint read the CPU cycle counter.
The transform output is read back and compared with a float DCT-IV.

Usage:  python spike/run_bench.py [--mode N] [--configs a,b,c]
"""
import argparse
import json
import math
import os
import random
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
ARC_ROOT = os.environ.get("ARCULATOR_ROOT", r"C:\Users\khcon\OneDrive\Projects\arculator-sarah")
VASM = os.environ.get("VASM", r"C:\Users\khcon\OneDrive\Archie\Projects\vasm_1.9\vasmarm_std.exe")

sys.path.insert(0, os.path.join(ARC_ROOT, "mcp"))
from arculator_mcp import keys as keymap            # noqa: E402
from arculator_mcp.client import MachinePool        # noqa: E402

MARK = 0x8008          # `mark` sits right after the branch and cfg_mode word
MAXN = 2048

CONFIGS = {
    "a3000-riscos311":      ("ARM2 8 MHz, MEMC1 (A305-A440 era)", 8.0),
    "a3000-memc1a":         ("ARM2 8 MHz, MEMC1a (A3000)", 8.0),
    "a3020-riscos311":      ("ARM250 12 MHz", 12.0),
    "a5000-arm3-riscos311": ("ARM3 25 MHz, MEMC1A 12 MHz", 25.0),
}


# --- input + reference ------------------------------------------------------

def make_input():
    """Sparse, spectrally decaying coefficients, roughly like decoded MDCT
    data, kept small enough that the 32-bit (non-64-bit) path cannot overflow."""
    rng = random.Random(1234)
    out = []
    for k in range(MAXN):
        if rng.random() < 0.5:
            out.append(0)
            continue
        amp = 2000.0 * math.exp(-k / 600.0) + 40.0
        out.append(int(round(rng.gauss(0.0, 1.0) * amp)))
    return out


def dct4(x):
    n = len(x)
    out = []
    for k in range(n):
        f = math.pi / n * (k + 0.5)
        out.append(sum(x[i] * math.cos(f * (i + 0.5)) for i in range(n) if x[i]))
    return out


def snr_db(ref, got):
    """Best scalar fit of got to ref, then SNR of the residual."""
    num = sum(r * g for r, g in zip(ref, got))
    den = sum(g * g for g in got) or 1
    s = num / den
    err = sum((r - s * g) ** 2 for r, g in zip(ref, got))
    sig = sum(r * r for r in ref)
    return (10 * math.log10(sig / err) if err else 999.0), s


# --- build -----------------------------------------------------------------

def build(coefs):
    os.makedirs(BUILD, exist_ok=True)
    with open(os.path.join(BUILD, "input.bin"), "wb") as f:
        f.write(struct.pack(f"<{MAXN}i", *coefs))
    __import__("ulc_tools").write_fourier_tables(BUILD)
    out = os.path.join(BUILD, "bench,ff8")
    cmd = [VASM, "-m2", "-opt-adr", "-Fbin", "-quiet", "-L", os.path.join(BUILD, "bench.lst"),
           "-o", out, "bench.asm"]
    subprocess.run(cmd, cwd=HERE, check=True)
    with open(out, "rb") as f:
        return f.read()


# --- emulator driving ----------------------------------------------------

def tap(m, scancode, shift=False):
    if shift:
        m.command_text(f"key {keymap.SCANCODES['lshift']} 1")
    m.command_text(f"key {scancode} 1")
    m.command_json("run_us 40000")
    m.command_text(f"key {scancode} 0")
    if shift:
        m.command_text(f"key {keymap.SCANCODES['lshift']} 0")
    m.command_json("run_us 40000")


def type_text(m, text):
    for ch in text:
        sc, sh = keymap.char_to_keystroke(ch)
        tap(m, sc, sh)


def run_one(pool, config, image, mode, coefs, refs):
    local = os.path.join(HERE, "configs", config + ".cfg")
    if os.path.exists(local):
        mid, m = pool.create(config_file=local)
    else:
        mid, m = pool.create(config=config)
    try:
        img = bytearray(image)
        struct.pack_into("<I", img, 4, mode)
        with open(os.path.join(m.hostfs, "bench,ff8"), "wb") as f:
            f.write(img)

        for _ in range(12):                      # boot to the desktop
            m.command_json("run_us 1000000")
        tap(m, keymap.SCANCODES["f12"])
        m.command_json("run_us 500000")
        type_text(m, "HostFS::HostFS.$.bench")
        m.command_text(f"bp add {MARK:x}")
        tap(m, keymap.SCANCODES["return"])

        hits = []
        last = None
        for _ in range(4000):                    # 20 s of emulated time, max
            res = m.command_json("run_us 5000")
            if res["reason"] == "budget":
                continue
            regs = m.command_json("regs")
            tag, cyc = regs["r"][12], regs["cycles"]
            if last is not None and tag == 0:
                # a stale hit of the same address before our image loaded
                hits = []
            hits.append((tag, cyc))
            if tag & 0x10000 and tag < 0x20000:
                n = tag & 0xFFFF
                buf = regs["r"][0]               # the DCT-IV returns its output buffer
                raw = m.command(f"read {buf:x} {n * 4:x}")
                hits[-1] = (tag, cyc, list(struct.unpack(f"<{n}i", raw)))
            if tag == 0x20004:
                break
            last = tag
        else:
            raise RuntimeError("benchmark did not finish")

        video = m.command_json("dump vidc_json")
        return hits, video
    finally:
        pool.destroy(mid)


_BUF = None


def subprocess_safe_buf(m):
    """Address of `buf`, from the vasm listing's symbol table (cached)."""
    global _BUF
    if _BUF is None:
        import re
        with open(os.path.join(BUILD, "bench.lst")) as f:
            hit = re.search(r"^buf\s+[AS]:([0-9A-Fa-f]+)", f.read(), re.M)
        if not hit:
            raise RuntimeError("buf not found in listing")
        _BUF = int(hit.group(1), 16)
    return _BUF


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", type=int, default=13)
    ap.add_argument("--configs", default=",".join(CONFIGS))
    ap.add_argument("--out", default=os.path.join(BUILD, "results.json"))
    args = ap.parse_args()

    coefs = make_input()
    image = build(coefs)
    print(f"built bench,ff8: {len(image)} bytes")
    refs = {n: dct4(coefs[:n]) for n in (64, 128, 256, 512, 1024, 2048)}

    pool = MachinePool(os.path.join(ARC_ROOT, "arculator-headless.exe"),
                       ARC_ROOT, os.path.join(BUILD, "sandbox"))
    results = {}
    try:
        for cfg in args.configs.split(","):
            name, mhz = CONFIGS[cfg]
            print(f"\n== {cfg}: {name}, MODE {args.mode}")
            hits, video = run_one(pool, cfg, image, args.mode, coefs, refs)
            r = {"name": name, "mhz": mhz, "mode": args.mode,
                 "video": {k: video.get(k) for k in ("width", "height", "bpp")
                           if isinstance(video, dict)},
                 "transforms": [], "mul": {}}
            prev = None
            seen = {}
            for h in hits:
                tag, cyc = h[0], h[1]
                if tag & 0x10000 and tag < 0x20000:
                    n = tag & 0xFFFF
                    c = cyc - prev
                    seen[n] = seen.get(n, 0) + 1
                    snr, scale = snr_db(refs[n], h[2])
                    entry = {"n": n, "run": seen[n], "cycles": c,
                             "cycles_per_sample": round(c / n, 1),
                             "us": round(c / mhz, 1),
                             "snr_db": round(snr, 1), "scale": scale}
                    r["transforms"].append(entry)
                    print(f"  N={n:4d} run {seen[n]}: {c:8d} cyc "
                          f"({c / n:6.1f}/sample, {c / mhz / 1000:6.2f} ms)"
                          f"  SNR vs float {snr:5.1f} dB")
                prev = cyc
            marks = {h[0]: h[1] for h in hits if h[0] >= 0x20000}
            nop = (marks[0x20004] - marks[0x20003]) / 64
            for key, a, b in (("rs_pos_7ff6", 0x20000, 0x20001),
                              ("rs_neg_7ff6", 0x20001, 0x20002),
                              ("rs_small_7f", 0x20002, 0x20003)):
                per = (marks[b] - marks[a]) / 64
                r["mul"][key] = round(per, 2)
            r["mul"]["nop"] = round(nop, 2)
            print("  MUL cycles each: " + ", ".join(
                f"{k}={v}" for k, v in r["mul"].items()))
            results[cfg] = r
    finally:
        pool.destroy_all()

    with open(args.out, "w") as f:
        json.dump(results, f, indent=1)
    print(f"\nresults -> {args.out}")


if __name__ == "__main__":
    main()
