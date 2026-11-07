"""Compare how expensive two apps are to *emulate* (host time per emulated second).

Archimedes Live on a phone can only just keep up with an A3020; an app that
costs more host time per emulated second makes it fall behind and the sound
breaks up. This boots arculator-headless, Filer_Runs an app from HostFS,
optionally types keys, then times `run_us` over the playing period.

    python player/emu_cost.py <app dir> [--keys 4] [--seconds 10]
"""
import argparse
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "spike"))
import run_bench as rb                                  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("app")
    ap.add_argument("--keys", default="")
    ap.add_argument("--config", default="a3020-riscos311")
    ap.add_argument("--settle", type=float, default=6.0)
    ap.add_argument("--seconds", type=float, default=10.0)
    a = ap.parse_args()
    pool = rb.MachinePool(os.path.join(rb.ARC_ROOT, "arculator-headless.exe"),
                          rb.ARC_ROOT, os.path.join(HERE, "build", "sandbox"))
    try:
        mid, m = pool.create(config=a.config)
        name = os.path.basename(os.path.normpath(a.app))
        shutil.copytree(a.app, os.path.join(m.hostfs, name), dirs_exist_ok=True)
        for _ in range(12):
            m.command_json("run_us 1000000")
        idle = time.perf_counter()
        for _ in range(20):
            m.command_json("run_us 100000")
        idle = (time.perf_counter() - idle) / 2.0
        rb.tap(m, rb.keymap.SCANCODES["f12"])
        m.command_json("run_us 500000")
        rb.type_text(m, f"Filer_Run HostFS::HostFS.$.{name}")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        m.command_json("run_us 300000")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        for _ in range(int(a.settle * 10)):
            m.command_json("run_us 100000")
        if a.keys:
            rb.type_text(m, a.keys)
            for _ in range(30):
                m.command_json("run_us 100000")
        t = time.perf_counter()
        for _ in range(int(a.seconds * 10)):
            m.command_json("run_us 100000")
        busy = (time.perf_counter() - t) / a.seconds
        print(f"{name}: {busy*1000:.0f} ms host per emulated s while playing "
              f"(desktop idle {idle*1000:.0f} ms) -> {busy/idle:.2f}x idle")
    finally:
        pool.destroy_all()


if __name__ == "__main__":
    main()
