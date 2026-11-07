"""Test the !ULCPlayer app in the emulator the way Archimedes Live runs it.

Boots to the desktop, Filer_Runs !ULCPlayer, screenshots the menu, plays a
track by key, records audio, stops with a mouse click, plays another, stops
with a key, then quits with Q and screenshots the desktop.

    python player/test_app.py [--config a3020-riscos311] [--track 4] [--seconds 10]
"""
import argparse
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPIKE = os.path.join(HERE, "..", "spike")
OUT = os.path.join(HERE, "build", "apptest")
sys.path.insert(0, SPIKE)
import run_bench as rb                                  # noqa: E402
from arculator_mcp import png as arcpng                 # noqa: E402


def shot(m, name):
    with open(os.path.join(OUT, name + ".png"), "wb") as f:
        f.write(arcpng.arfb_to_png(m.command("screenshot"), scale=1))


def run(m, seconds):
    for _ in range(int(seconds * 10)):
        m.command_json("run_us 100000")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="a3020-riscos311")
    ap.add_argument("--track", default="4")
    ap.add_argument("--seconds", type=float, default=10.0)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    app = os.path.join(HERE, "build", "app", "!ULCPlayer")
    with open(os.path.join(app, "!RunImage,ff8"), "rb") as f:
        info = struct.unpack_from("<6I", f.read(), 4)

    pool = rb.MachinePool(os.path.join(rb.ARC_ROOT, "arculator-headless.exe"),
                          rb.ARC_ROOT, os.path.join(HERE, "build", "sandbox"))
    try:
        mid, m = pool.create(config=a.config)
        shutil.copytree(app, os.path.join(m.hostfs, "!ULCPlayer"), dirs_exist_ok=True)
        run(m, 12)
        rb.tap(m, rb.keymap.SCANCODES["f12"])
        m.command_json("run_us 500000")
        rb.type_text(m, "Filer_Run HostFS::HostFS.$.!ULCPlayer")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        m.command_json("run_us 300000")
        rb.tap(m, rb.keymap.SCANCODES["return"])        # back to the desktop; Filer runs it
        run(m, 3)
        shot(m, "1-menu")
        rb.type_text(m, a.track)
        run(m, 2)
        m.command_text("sound start")
        run(m, a.seconds)
        m.command_text("sound stop")
        with open(os.path.join(OUT, "play.wav"), "wb") as f:
            f.write(arcpng.arsn_to_wav(m.command("sound fetch")))
        shot(m, "2-playing")
        vals = [struct.unpack("<I", m.command(f"read {x:x} 4"))[0] for x in info]
        names = ("underruns", "cpu_pct", "blocks_played", "ring_rd", "ring_wr", "dma_samples")
        print("  " + ", ".join(f"{n}={v}" for n, v in zip(names, vals)))
        m.command_text("mouse 0 0 1")                    # click: stop
        m.command_json("run_us 100000")
        m.command_text("mouse 0 0 0")
        run(m, 1)
        shot(m, "3-menu-after-click")
        rb.type_text(m, "1")
        run(m, 4)
        shot(m, "4-playing-1")
        rb.tap(m, rb.keymap.SCANCODES["space"])          # key: stop
        run(m, 1)
        shot(m, "5-menu-after-key")
        rb.type_text(m, "q")
        run(m, 2)
        shot(m, "6-after-quit")
        print(f"  wrote {OUT}")
    finally:
        pool.destroy_all()


if __name__ == "__main__":
    main()
