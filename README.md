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
  - [CODECS.md](CODECS.md): ULC against ADPCM on the Archimedes, the trellis and 3/2-bit ADPCM, and proposals for codecs suited to ARM2.

## Requirements

- **[vasm](http://sun.hasenbraten.de/vasm/)** (`vasmarm_std`): set `VASM` to its path.
- **Headless build of Arculator:** the tests drive it through `arculator_mcp`. Set `ARCULATOR_ROOT` to its directory.
- **Python 3 with numpy.**

## Credits

- **Codec:** [ulc-codec](https://github.com/Aikku93/ulc-codec) by Ruben Nunez (Aikku93). The encoder and decoder tools come from it, and the checks compare against its C decoder.
- **Decoder:** started as a port of the GBA player's decoder in [ulcplayer-gba](https://github.com/Aikku93/ulcplayer-gba) by Ruben Nunez (Aikku93): `source/libulc/ulc_UpdatePlayer.s`, `Fourier_DCT4_InPlace.s` and `Fourier_FFT_InPlace.s`. That port became `spike/decoder.asm` and `spike/fourier.asm`, then was optimised for the Archimedes (see [PERF.md](PERF.md)).
- **Sound handling:** follows the approach of QTM (QTMModule), the Archimedes tracker player. No QTM code is included.

## Licence

The code is released into the public domain under the [Unlicense](LICENSE), the same terms as ulc-codec and ulcplayer-gba.

The music in `music/` (and in the `!ULCPlayer` app) is not covered by this licence; its authors keep all rights to it.
