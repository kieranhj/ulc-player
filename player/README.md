# ULC player v0 (RISC OS 3.1x, ARM2 / ARM250 / ARM3)

Plays a `.ulc` file (ULC2, mono or M/S stereo, block size 64–2048) through VIDC. It also plays IMA
ADPCM (`.adp`, made with `adpcm_encode.py`), which it detects from the header. Both go through the
identical output path, so they can be compared like for like.

```
*WimpSlot -min 1600K          (file size + ~90 KB)
*player <file>
```

Escape quits. The track loops.

## How it works

- **Decoding:** the foreground loop decodes a block whenever the 32 KB ring buffer has room for one.
  It uses `spike/decoder.asm` and `spike/fourier.asm`. Both decoders write VIDC log bytes straight
  into the ring (stereo interleaved) through high-resolution tables:
  - ULC mono looks each 11-bit sample up in a 2 KB table. ULC stereo does M/S at 8 bits, then a
    256-byte table.
  - ADPCM is table-driven (one load per nybble) and looks up the 16-bit predictor at 12 bits.
  See `PERF.md` for the optimisations and their measurements.
- **Sound:** our own SoundDMA channel handler, installed with `Sound_Configure`. Its fill routine
  runs in the sound interrupt and only copies bytes from the ring (or outputs silence and counts
  an underrun). Sound handling follows QTM (`Repos/qtm-vasm/src/shared/qtm_dma.asm`):
  - always pass the sample period in r2 to `Sound_Configure` (RISC OS 3 bug)
  - read back the DMA size actually granted
  - "twang" `Sound_Configure` after `Sound_Stereo` so the positions take effect
  - on exit, output silence first, restore the old configuration, then clear the old channels' control blocks
- **Screen:** MODE 9 with track info, CPU % (decode time over wall time, 2 s windows), ring fill,
  underruns, blocks played (redrawn once a second), and a scope of channel 1 (5 times a second).
  MODE 9's 4 bpp halves MODE 13's video DMA, which cost ARM2 about 9%.

## Test in the emulator

```sh
python player/run_player.py --ulc spike/build/real/particle-mono-512-64k.ulc --config a3020-riscos311 --seconds 10
```

This boots RISC OS 3.11 in `arculator-headless`, runs the player, and records the machine's audio
to `player/build/<track>-<config>.wav`. It also saves a screenshot, presses Escape, and screenshots the exit.

`python player/check_ring.py FILE` checks the bytes the player writes to its ring against the
expected decode (ADPCM exactly; ULC against a saved 11-bit decode).

Results on 2026-11-06, before the optimisations, for the 86 s *Archie Particle demo*, 10 s of playback each, all with 0 underruns:

| Machine | Stream | CPU shown | Benchmark predicted | Capture vs reference decode |
|---|---|---|---|---|
| ARM250 (A3020) | mono 64 kbps, N=512 | 50% | 49.8% | correlation 0.986 |
| ARM2 + MEMC1a (A3000) | mono 64 kbps, N=512 | 78% | 78% | — |
| ARM3 25 MHz (A5000) | stereo 128 kbps, N=512 | 50% | 51% | L 0.982 / R 0.986, no crosstalk |

## The !ULCPlayer app

`python player/make_app.py` builds `build/app/!ULCPlayer` and packs it to `build/ulcplayer,ddc`. It contains the four
tracks from `music/` as ULC mono at 128 kbps: 39% CPU on an A3020 (ARM250), 60% on an A3000,
0 underruns. Since the 2026-11-07 optimisations, stereo 128 kbps also fits an A3020, at 80% CPU.
`!Run` runs `!RunImage <app dir>`. Given a directory, the player shows a menu: keys 1–4 or a click
play a track, any key or click goes back to the menu, and Q/Escape quits.
Archimedes Live on a phone moves the pointer to a tap but never shows a click. The browser sends
mousedown and mouseup together, between two of the emulator's 10 ms polls. So the player also treats
a single pointer jump, with stillness before and after, as a tap. A real mouse moves in a stream of
small steps and doesn't trigger it.
`python player/test_app.py` Filer_Runs it from the desktop in the emulator, the way Archimedes Live
does, and screenshots each step. It's published as `arc-ulcplayer-wip,ddc` on bitshifters.github.io/content/wip.

## Making .adp files

```
python player/adpcm_encode.py in.wav out.adp [--bits 4|3|2]   # 16-bit WAV at the target rate, mono or stereo
```

- **Encoder:** by default, a trellis (beam 16) that minimises the error as heard through the VIDC. It needs zig to build `tools/adpcm_trellis.c`.
- **Original encoder:** `--beam 0` gives the original greedy 4-bit encoder.
- **Bit widths:**
  - 4-bit is 83 kbps mono at 20.8 kHz.
  - 3-bit is 62 kbps.
  - 2-bit is 42 kbps.
- **Decode cost:** all three cost the same, about 22 cycles per sample: 6% CPU on an A3000 for mono, 11–12% for stereo, against 60% for mono ULC.

- **Predictor modes:** `--modes` lets each block pick from three cheap predictors, for +1.4 to +3.0 dB. Decoding costs about 23–24 cycles per sample instead of 22.

See `../CODECS.md` for quality measurements, `listen_grid.py` for listening clips, and `subband.py` for the two-band (proposal 3) prototype.

## Making .ulc files

Build ulc-codec's `ulcencodetool` (https://github.com/Aikku93/ulc-codec), then:

```
ulcencodetool in.wav out.ulc 64 -blocksize:512     # mono: resample to 20833 Hz first
ulcencodetool in.wav out.ulc 128 -blocksize:512    # stereo (M/S)
```

Use 20833 Hz (a 48 µs VIDC period) or 15625 Hz (64 µs), and N=512. Normalise hot masters to about −1 dBFS.

## Not done yet

- Streaming from disc. The whole file is loaded into RAM: about 8 KB/s mono at 64 kbps, 16 KB/s stereo at 128 kbps.
- A desktop front end or `!App`, and an error handler. An error mid-play would leave the handler installed.
- Phase 2 decoder optimisations (`FEASIBILITY.md` §8, §10).
