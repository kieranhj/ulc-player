"""Does the !App survive a sound system left in a multi-voice, spread-stereo state?

Sets *Voices 4 and alternating *Stereo positions (as a previous music player
might leave them), Filer_Runs !ULCPlayer, plays track 4, records stereo audio
and reports how similar the left and right channels are. Mono output should
be identical in both channels.

    python player/test_stereo_state.py [--clean]   # --clean skips the setup
"""
import argparse
import os
import shutil
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "spike"))
import run_bench as rb                                  # noqa: E402
from arculator_mcp import png as arcpng                 # noqa: E402
import io, wave                                         # noqa: E402


def cmd(m, text):
    rb.type_text(m, text)
    rb.tap(m, rb.keymap.SCANCODES["return"])
    m.command_json("run_us 200000")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--key", default="4")
    ap.add_argument("--app", default=os.path.join(HERE, "build", "app", "!ULCPlayer"))
    a = ap.parse_args()
    pool = rb.MachinePool(os.path.join(rb.ARC_ROOT, "arculator-headless.exe"),
                          rb.ARC_ROOT, os.path.join(HERE, "build", "sandbox"))
    try:
        mid, m = pool.create(config="a3020-riscos311")
        shutil.copytree(a.app, os.path.join(m.hostfs, "!ULCPlayer"), dirs_exist_ok=True)
        for _ in range(12):
            m.command_json("run_us 1000000")
        rb.tap(m, rb.keymap.SCANCODES["f12"])
        m.command_json("run_us 500000")
        if not a.clean:
            cmd(m, "Voices 4")
            for ch, pos in ((1, -127), (2, 127), (3, -64), (4, 64)):
                cmd(m, f"Stereo {ch} {pos}")
        rb.type_text(m, "Filer_Run HostFS::HostFS.$.!ULCPlayer")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        m.command_json("run_us 300000")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        for _ in range(30):
            m.command_json("run_us 100000")
        rb.type_text(m, a.key)
        for _ in range(30):
            m.command_json("run_us 100000")
        m.command_text("sound start")
        for _ in range(50):
            m.command_json("run_us 100000")
        m.command_text("sound stop")
        data = arcpng.arsn_to_wav(m.command("sound fetch"))
        tag = ("clean" if a.clean else "voices4") + "-" + a.key
        out = os.path.join(HERE, "build", "apptest", f"stereo-{tag}.wav")
        with open(out, "wb") as f:
            f.write(data)
        w = wave.open(io.BytesIO(data))
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, w.getnchannels()).astype(float)
        x = x[w.getframerate():]
        l, r = x[:, 0], x[:, 1]
        for name, s in (("L", l), ("R", r)):
            s = s - s.mean()
            sp = np.abs(np.fft.rfft(s[:2**16])) ** 2
            f = np.fft.rfftfreq(2**16, 1 / w.getframerate())
            print(f"  {tag} {name}: rms {s.std():.0f}  energy>5kHz {sp[f > 5000].sum() / sp.sum():.3f}")
        print(f"  {tag} L/R correlation {np.corrcoef(l, r)[0, 1]:.3f}  -> {out}")
    finally:
        pool.destroy_all()


if __name__ == "__main__":
    main()
