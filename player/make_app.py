"""Build the !ULCPlayer application and pack it into a Spark archive.

    python player/make_app.py [--rate 128] [--mono]

Makes player/build/app/!ULCPlayer (HostFS ,xxx naming) with the four tracks as
ULC stereo (or --mono) at --rate kbps from music/, and player/build/ulcplayer,ddc.
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, HERE)
import run_player                                        # noqa: E402

TRACKS = [("DemoB", "shadertoyB"), ("DemoC", "shadertoyC"),
          ("DemoD", "shadertoyD"), ("Particle", "particle")]
# Diagnostic entries 5-8: (leaf, file in music/)
TESTS = []  # diagnostic entries, e.g. ("ToneULC", "sine440-mono-128k.ulc"); add to track_table too

RUN = """| !Run for !ULCPlayer
Set ULCPlayer$Dir <Obey$Dir>
WimpSlot -min {slot}K -max {slot}K
Run <ULCPlayer$Dir>.!RunImage <ULCPlayer$Dir>
"""

HELP = """ULC Player (work in progress)

Plays music compressed with ulc-codec (an MDCT codec by Aikku93,
ported from ulcplayer-gba), decoded live on the ARM.

Tracks: {rate} kbps {chans} at 20.8 kHz. Stereo takes about 80% of an
A3020 (ARM250) and 40% of an A5000; mono about 39% and 20%.
An ARM2 A3000 plays mono at about 60% but not stereo, so use an
A3020, A5000 or faster.

Keys 1-4 or click a track to play it. Any key or click stops.
Q or Escape quits.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=int, default=128)
    ap.add_argument("--mono", action="store_true")
    a = ap.parse_args()
    image = run_player.build()
    app = os.path.join(HERE, "build", "app", "!ULCPlayer")
    shutil.rmtree(os.path.dirname(app), ignore_errors=True)
    os.makedirs(os.path.join(app, "Tracks"), exist_ok=True)
    biggest = 0
    for leaf, stem in TRACKS:
        src = os.path.join(ROOT, "music", f"{stem}-{'mono' if a.mono else 'stereo'}-{a.rate}k.ulc")
        shutil.copy(src, os.path.join(app, "Tracks", leaf + ",ffd"))
        biggest = max(biggest, os.path.getsize(src))
    for leaf, name in TESTS:
        src = os.path.join(ROOT, "music", name)
        shutil.copy(src, os.path.join(app, "Tracks", leaf + ",ffd"))
        biggest = max(biggest, os.path.getsize(src))
    slot = (len(image) + 16384 + 4096 + 8192 + 32768 + biggest + 16384) // 1024 + 48
    with open(os.path.join(app, "!RunImage,ff8"), "wb") as f:
        f.write(image)
    with open(os.path.join(app, "!Run,feb"), "w", newline="\n") as f:
        f.write(RUN.format(slot=slot))
    with open(os.path.join(app, "!Help,fff"), "w", newline="\n") as f:
        f.write(HELP.format(rate=a.rate, chans="mono" if a.mono else "stereo"))
    out = os.path.join(HERE, "build", "ulcplayer,ddc")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "spark.py"), app, out], check=True)
    print(f"{out}: {os.path.getsize(out)} bytes, WimpSlot {slot}K")


if __name__ == "__main__":
    main()
