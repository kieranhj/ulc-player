"""Build the !ULCPlayer application and pack it into a Spark archive.

    python player/make_app.py [--rate 128] [--mono]
    python player/make_app.py --adpcm-test

Makes player/build/app/!ULCPlayer (HostFS ,xxx naming) with the four tracks as
ULC stereo (or --mono) at --rate kbps from music/, and player/build/ulcplayer,ddc.

--adpcm-test makes !ADPCMTest (player/build/adpcmtest,ddc) instead: the
Particle tune in stereo ADPCM at 4, 3 and 2 bits with predictor modes, and at
4 and 2 bits without them, to compare by ear.
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

RUN = """| !Run for !{app}
Set {app}$Dir <Obey$Dir>
WimpSlot -min {slot}K -max {slot}K
Run <{app}$Dir>.!RunImage <{app}$Dir>
"""

ADPCM_TEST = {
    "app": "ADPCMTest", "out": "adpcmtest,ddc",
    # leaf, file in music/, title while playing, menu line
    "tracks": [("Bits4", "particle-stereo-adpcm4m.adp", "Particle: 4-bit ADPCM + modes",
                "1   4-bit + modes   167 kbps  {size}"),
               ("Bits4Plain", "particle-stereo-adpcm4.adp", "Particle: 4-bit ADPCM, no modes",
                "2   4-bit, no modes 167 kbps  {size}"),
               ("Bits3", "particle-stereo-adpcm3m.adp", "Particle: 3-bit ADPCM + modes",
                "3   3-bit + modes   125 kbps  {size}"),
               ("Bits2", "particle-stereo-adpcm2m.adp", "Particle: 2-bit ADPCM + modes",
                "4   2-bit + modes    83 kbps  {size}"),
               ("Bits2Plain", "particle-stereo-adpcm2.adp", "Particle: 2-bit ADPCM, no modes",
                "5   2-bit, no modes  83 kbps  {size}")],
    "fmt": "Particle, stereo IMA ADPCM (trellis)",
    "fmt2": "20.8 kHz, decoded live on the ARM",
    "help": """ADPCM bit-width test (work in progress)

The Archie Particle demo tune in stereo IMA ADPCM at 20.8 kHz,
encoded with a trellis search and per-block predictor modes:

  1  4-bit + modes    167 kbps  {size0}  (SNR about 34 dB)
  2  4-bit, no modes  167 kbps  {size1}  (about 33 dB)
  3  3-bit + modes    125 kbps  {size2}  (about 31 dB)
  4  2-bit + modes     83 kbps  {size3}
  5  2-bit, no modes   83 kbps  {size4}

2 and 5 are the same encoder without the modes, to hear what
the modes add at 4 and 2 bits.

Sizes are for the 1:26 tune (K = 1024 bytes).

All five decode in about 11-13% of an ARM2 A3000; this build
loads each tune into memory, so it needs 4 MB (an A3020 or better).

Keys 1-5 or click a track to play it. Any key or click stops.
Q or Escape quits.
""",
}

HELP = """ULC Player (work in progress)

Plays music compressed with ulc-codec (an MDCT codec by Aikku93,
ported from ulcplayer-gba), decoded live on the ARM.

Tracks: {rate} kbps {chans} at 20.8 kHz. Stereo takes about 80% of an
A3020 (ARM250) and 40% of an A5000; mono about 39% and 20%.
An ARM2 A3000 plays mono at about 60% but not stereo, so use an
A3020, A5000 or faster.

Keys 1-5 or click a track to play it. Any key or click stops.
Q or Escape quits.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=int, default=128)
    ap.add_argument("--mono", action="store_true")
    ap.add_argument("--adpcm-test", action="store_true")
    a = ap.parse_args()
    if a.adpcm_test:
        return make_test(ADPCM_TEST)
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
        f.write(RUN.format(app="ULCPlayer", slot=slot))
    with open(os.path.join(app, "!Help,fff"), "w", newline="\n") as f:
        f.write(HELP.format(rate=a.rate, chans="mono" if a.mono else "stereo"))
    out = os.path.join(HERE, "build", "ulcplayer,ddc")
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "spark.py"), app, out], check=True)
    print(f"{out}: {os.path.getsize(out)} bytes, WimpSlot {slot}K")


def make_test(cfg):
    """A comparison app: four files from music/ with their own menu and help."""
    size = [f"{os.path.getsize(os.path.join(ROOT, 'music', name)) // 1024:>4}K"
            for _, name, _, _ in cfg["tracks"]]
    image = run_player.build({"tracks": [(leaf, title, line.format(size=size[i]))
                                         for i, (leaf, _, title, line) in enumerate(cfg["tracks"])],
                              "fmt": cfg["fmt"], "fmt2": cfg["fmt2"]})
    app = os.path.join(HERE, "build", "app", "!" + cfg["app"])
    shutil.rmtree(os.path.dirname(app), ignore_errors=True)
    os.makedirs(os.path.join(app, "Tracks"), exist_ok=True)
    biggest = 0
    for leaf, name, _, _ in cfg["tracks"]:
        src = os.path.join(ROOT, "music", name)
        shutil.copy(src, os.path.join(app, "Tracks", leaf + ",ffd"))
        biggest = max(biggest, os.path.getsize(src))
    slot = (len(image) + 16384 + 4096 + 8192 + 32768 + biggest + 16384) // 1024 + 48
    with open(os.path.join(app, "!RunImage,ff8"), "wb") as f:
        f.write(image)
    with open(os.path.join(app, "!Run,feb"), "w", newline=chr(10)) as f:
        f.write(RUN.format(app=cfg["app"], slot=slot))
    with open(os.path.join(app, "!Help,fff"), "w", newline=chr(10)) as f:
        f.write(cfg["help"].format(**{f"size{i}": s for i, s in enumerate(size)}))
    out = os.path.join(HERE, "build", cfg["out"])
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "spark.py"), app, out], check=True)
    print(f"{out}: {os.path.getsize(out)} bytes, WimpSlot {slot}K")
    run_player.build()                                  # leave the default menu in build/


if __name__ == "__main__":
    main()
