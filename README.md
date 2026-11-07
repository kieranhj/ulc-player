# ULC player for the Acorn Archimedes

Real-time decoding of [ulc-codec](https://github.com/Aikku93/ulc-codec) (an MDCT audio codec by Aikku93) on ARM2, ARM250 and ARM3 under RISC OS 3.1x. The player also handles IMA ADPCM, so the two codecs can be compared like for like.

The decoder started as a port of the GBA decoder from ulcplayer-gba (public domain), then was optimised for the Archimedes. On the Particle demo track, ULC 128 kbps at 20.8 kHz uses:

| Machine | Mono | Stereo |
|---|---:|---:|
| A3000 (ARM2 8 MHz) | 60% CPU | doesn't fit |
| A3020 (ARM250 12 MHz) | 39% | 80% |
| A5000 (ARM3 25 MHz) | 20% | about 40% |

Try it in the browser, or download the `!ULCPlayer` Spark archive: [arc-ulcplayer-wip](https://bitshifters.github.io/content/wip/arc-ulcplayer-wip,ddc).

## Layout

- **`player/`:** the RISC OS player and the `!ULCPlayer` app build (`make_app.py`), plus emulator tests and the cycle profiler. See [player/README.md](player/README.md).
- **`spike/`:** the decoder (`decoder.asm`, `fourier.asm`), the benchmark harnesses, and the checks against ulc-codec's own C decoder.
- **`music/`:** test tracks encoded as ULC and ADPCM.
- **`tools/spark.py`:** writes !Spark archives.
- **Write-ups:**
  - [FEASIBILITY.md](FEASIBILITY.md): the original study.
  - [PERF.md](PERF.md): the measured performance analysis and the optimisations made.

## Requirements

- **[vasm](http://sun.hasenbraten.de/vasm/)** (`vasmarm_std`): set `VASM` to its path.
- **Headless build of Arculator:** the tests drive it through `arculator_mcp`. Set `ARCULATOR_ROOT` to its directory.
- **Python 3 with numpy.**
