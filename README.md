# ULC player for the Acorn Archimedes

Real-time decoding of [ulc-codec](https://github.com/Aikku93/ulc-codec) (an MDCT audio codec by Aikku93) on ARM2, ARM250 and ARM3 under RISC OS 3.1x. The player also handles IMA ADPCM, so the two codecs can be compared like for like.

The decoder started as a port of the GBA decoder from ulcplayer-gba (public domain), then was optimised for the Archimedes. On the Particle demo track, ULC 128 kbps at 20.8 kHz uses:

| Machine | Mono | Stereo |
|---|---:|---:|
| A3000 (ARM2 8 MHz) | 60% CPU | doesn't fit |
| A3020 (ARM250 12 MHz) | 39% | 80% |
| A5000 (ARM3 25 MHz) | 20% | about 40% |

[Try it in the browser](https://bitshifters.github.io/arclive/#disc=https://bitshifters.github.io/content/wip/arc-ulcplayer-wip,ddc&autoboot=desktop%20filer_run%20HostFS::hostfs.$.!ULCPlayer&preset=a3020&soundfilter=2), or download the `!ULCPlayer` Spark archive: [arc-ulcplayer-wip](https://bitshifters.github.io/content/wip/arc-ulcplayer-wip,ddc).

## ADPCM

The player also decodes IMA-style ADPCM at 4, 3 or 2 bits per sample, for a tenth of ULC's CPU. The encoder (`player/adpcm_encode.py`) adds two things a standard IMA encoder doesn't have, and the stream stays IMA-shaped:
- **Trellis search:** it keeps the 16 best paths through each block, scored by the error as heard through the VIDC's log output.
- **Predictor modes** (`--modes`): each block chooses to predict the next sample as the last one (IMA), the last plus half the slope, or the last plus the whole slope. Each of those is one or two ARM instructions.

Particle demo, stereo at 20.8 kHz. SNR is the mean over four tracks, measured after the VIDC; the VIDC itself limits any codec to 37.5 dB.

| Format | kbps | Size of the 1:26 tune | SNR | Decode, A3000 |
|---|---:|---:|---:|---:|
| 4-bit + modes | 167 | 1759 KB | 34.1 dB | 13% |
| 4-bit, no modes | 167 | 1759 KB | 32.8 dB | 12% |
| 3-bit + modes | 125 | 1323 KB | 30.8 dB | 13% |
| 2-bit + modes | 83 | 886 KB | 25.9 dB (mono) | 12% |
| 2-bit, no modes | 83 | 886 KB | 22.9 dB (mono) | 11% |

Mono is half the size and half the CPU, about 6% of an A3000.

To compare them by ear, the `!ADPCMTest` app has all five on a menu: [try it in the browser](https://bitshifters.github.io/arclive/#disc=https://bitshifters.github.io/content/wip/arc-ulcplayer-adpcmtest-wip,ddc&autoboot=desktop%20filer_run%20HostFS::hostfs.$.!ADPCMTest&preset=a3020&soundfilter=2), or download [arc-ulcplayer-adpcmtest-wip](https://bitshifters.github.io/content/wip/arc-ulcplayer-adpcmtest-wip,ddc). It needs 4 MB, because each tune is loaded into memory whole.

[CODECS.md](CODECS.md) has the measurements and how ULC and ADPCM compare.

## Layout

- **`player/`:** the RISC OS player, the app builds (`make_app.py` for `!ULCPlayer`, `make_app.py --adpcm-test` for `!ADPCMTest`), the ADPCM encoder, the emulator tests and the cycle profiler. See [player/README.md](player/README.md).
- **`tools/adpcm_trellis.c`:** the trellis ADPCM encoder that `adpcm_encode.py` builds and calls.
- **`spike/`:** the decoder (`decoder.asm`, `fourier.asm`), the benchmark harnesses, and the checks against ulc-codec's own C decoder.
- **`music/`:** test tracks encoded as ULC and ADPCM.
- **`tools/spark.py`:** writes !Spark archives.
- **Write-ups:**
  - [FEASIBILITY.md](FEASIBILITY.md): the original study.
  - [PERF.md](PERF.md): the measured performance analysis and the optimisations made.
  - [CODECS.md](CODECS.md): ULC against ADPCM on the Archimedes, the trellis encoder, 3/2-bit ADPCM, predictor modes, the subband prototype, and proposals for codecs suited to ARM2.

## Requirements

- **[vasm](http://sun.hasenbraten.de/vasm/)** (`vasmarm_std`): set `VASM` to its path.
- **Headless build of Arculator:** the tests drive it through `arculator_mcp`. Set `ARCULATOR_ROOT` to its directory.
- **Python 3 with numpy.**
- **[zig](https://ziglang.org/)** (`zig cc`): builds the trellis ADPCM encoder.

## Credits

- **Codec:** [ulc-codec](https://github.com/Aikku93/ulc-codec) by Ruben Nunez (Aikku93). The encoder and decoder tools come from it, and the checks compare against its C decoder.
- **Decoder:** started as a port of the GBA player's decoder in [ulcplayer-gba](https://github.com/Aikku93/ulcplayer-gba) by Ruben Nunez (Aikku93): `source/libulc/ulc_UpdatePlayer.s`, `Fourier_DCT4_InPlace.s` and `Fourier_FFT_InPlace.s`. That port became `spike/decoder.asm` and `spike/fourier.asm`, then was optimised for the Archimedes (see [PERF.md](PERF.md)).
- **Sound handling:** follows the approach of QTM (QTMModule), the Archimedes tracker player. No QTM code is included.

## Licence

The code is released into the public domain under the [Unlicense](LICENSE), the same terms as ulc-codec and ulcplayer-gba.

The music in `music/` (and in the `!ULCPlayer` and `!ADPCMTest` apps) is not covered by this licence; its authors keep all rights to it.
