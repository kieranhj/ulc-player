"""Build the ULC player and run it on an emulated Archimedes.

Boots RISC OS 3.11 in arculator-headless, grows the application slot, runs
`player <file>`, records the machine's audio output for --seconds, saves a
WAV and a screenshot, then presses Escape and checks that the player exits.

Usage:  python player/run_player.py --ulc FILE [--config a3020-riscos311]
                                    [--seconds 10] [--build-only]
"""
import argparse
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPIKE = os.path.join(HERE, "..", "spike")
BUILD = os.path.join(HERE, "build")
sys.path.insert(0, SPIKE)
import run_bench as rb                                  # noqa: E402
from ulc_tools import (vidc_lin8_to_log, vidc_lin_to_log, lin11_to_lin8,  # noqa: E402
                       ima_table, vidc_to_scope)
from arculator_mcp import png as arcpng                 # noqa: E402


def build():
    os.makedirs(BUILD, exist_ok=True)
    for name, data in (("vidc_table.bin", vidc_lin8_to_log()), ("vidc11.bin", vidc_lin_to_log(11)),
                       ("vidc12.bin", vidc_lin_to_log(12)), ("lin11_8.bin", lin11_to_lin8()),
                       ("ima_table.bin", ima_table()), ("scope_table.bin", vidc_to_scope())):
        with open(os.path.join(BUILD, name), "wb") as f:
            f.write(data)
    __import__("ulc_tools").write_fourier_tables(BUILD)
    out = os.path.join(BUILD, "player,ff8")
    subprocess.run([rb.VASM, "-m2", "-opt-adr", "-Fbin", "-quiet", "-I", SPIKE,
                    "-L", os.path.join(BUILD, "player.lst"), "-o", out, "player.asm"],
                   cwd=HERE, check=True)
    with open(out, "rb") as f:
        return f.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ulc")
    ap.add_argument("--config", default="a3020-riscos311")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--build-only", action="store_true")
    args = ap.parse_args()

    image = build()
    print(f"built player,ff8: {len(image)} bytes")
    if args.build_only:
        return
    info = struct.unpack_from("<6I", image, 4)     # addresses of the player's counters
    with open(args.ulc, "rb") as f:
        track = f.read()
    slot_k = (len(image) + 16 * 1024 + 4 * 4096 + 2 * 2048 + 32768 + len(track)) // 1024 + 64

    pool = rb.MachinePool(os.path.join(rb.ARC_ROOT, "arculator-headless.exe"),
                          rb.ARC_ROOT, os.path.join(BUILD, "sandbox"))
    try:
        local = os.path.join(SPIKE, "configs", args.config + ".cfg")
        mid, m = (pool.create(config_file=local) if os.path.exists(local)
                  else pool.create(config=args.config))
        with open(os.path.join(m.hostfs, "player,ff8"), "wb") as f:
            f.write(image)
        with open(os.path.join(m.hostfs, "track,ffd"), "wb") as f:
            f.write(track)
        for _ in range(12):
            m.command_json("run_us 1000000")
        rb.tap(m, rb.keymap.SCANCODES["f12"])
        m.command_json("run_us 500000")
        rb.type_text(m, f"WimpSlot -min {slot_k}K")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        m.command_json("run_us 300000")
        rb.type_text(m, "HostFS::HostFS.$.player HostFS::HostFS.$.track")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        m.command_json("run_us 1500000")             # load + prefill
        m.command_text("sound start")
        for _ in range(int(args.seconds * 10)):
            m.command_json("run_us 100000")
        m.command_text("sound stop")
        wav = arcpng.arsn_to_wav(m.command("sound fetch"))
        stem = os.path.splitext(os.path.basename(args.ulc))[0] + "-" + args.config
        with open(os.path.join(BUILD, stem + ".wav"), "wb") as f:
            f.write(wav)
        with open(os.path.join(BUILD, stem + ".png"), "wb") as f:
            f.write(arcpng.arfb_to_png(m.command("screenshot"), scale=2))
        vals = [struct.unpack("<I", m.command(f"read {a:x} 4"))[0] for a in info]
        names = ("underruns", "cpu_pct", "blocks_played", "ring_rd", "ring_wr", "dma_samples")
        print("  " + ", ".join(f"{n}={v}" for n, v in zip(names, vals)))
        rb.tap(m, rb.keymap.SCANCODES["escape"])
        m.command_json("run_us 500000")
        with open(os.path.join(BUILD, stem + "-exit.png"), "wb") as f:
            f.write(arcpng.arfb_to_png(m.command("screenshot"), scale=1))
        print(f"  wrote build/{stem}.wav, build/{stem}.png, build/{stem}-exit.png")
    finally:
        pool.destroy_all()


if __name__ == "__main__":
    main()
