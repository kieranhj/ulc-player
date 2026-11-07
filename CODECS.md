# Codecs for the Archimedes: assessment and proposals (2026-11-07)

This report covers three questions:
- How do the two codecs we have, ULC and IMA ADPCM, compare on an Archimedes?
- What could a codec designed for ARM2-class decoding look like?
- What can the VIDC's log format do for us?

Proposals 1 and 2 are implemented and measured. Proposals 3–6 are designs with estimates.

## The VIDC sets the ceiling

VIDC1 plays 8-bit logarithmic samples:
- bit 0 is the sign;
- bits 1–4 are the step within a chord ("point");
- bits 5–7 are the chord, and each chord doubles the amplitude.

That is about 5 bits of precision at any level, over 12 bits of range. Sending the source itself through the 12-bit table gives **37.4 dB SNR on every track**, whatever the level. That is the best any codec can sound on this hardware, before the DAC and filters.

Two consequences follow:
- Precision beyond about 9–10 bits in a decoder is wasted.
- Noise only needs to sit below the VIDC's own, which is proportional to the level.

## What we have now

The figures below are waveform SNR against the 20833 Hz source, after the player's VIDC tables. Each is the mean over the same 20 s excerpt of the four tracks; `player/listen_grid.py` writes the clips and `snr.tsv`. Decode cost is measured in the player on an A3000 (ARM2 8 MHz).

| Format | Bits/sample/channel | kbps mono | SNR mono | SNR stereo | Decode, A3000 |
|---|---:|---:|---:|---:|---|
| Raw VIDC bytes (the ceiling) | 8.0 | 166 | 37.5 | 37.6 | 0 |
| IMA ADPCM 4-bit, original greedy encoder | 4.0 | 83 | 30.6 | – | 22 c/s, 6% |
| **ADPCM 4-bit, trellis encoder** | 4.0 | 83 | **32.5** | **32.8** | 22 c/s, 6% (stereo 12%) |
| **ADPCM 3-bit, trellis** | 3.0 | 62 | **28.4** | **28.3** | 21.5 c/s, 6% (stereo 11%) |
| **ADPCM 2-bit, trellis** | 2.0 | 42 | **22.9** | – | 21.3 c/s, 6% (stereo 11%) |
| ULC 32k | 1.5 | 32 | 17.4 | – | about 229 c/s, 60% |
| ULC 64k | 3.1 | 64 | 19.1 | 17.6 (64k stereo, 1.5 bps) | about 229 c/s, 60% |
| ULC 128k | 6.1 | 128 | 19.5 | 19.1 (128k stereo, 3.1 bps) | 60%; stereo doesn't fit an A3000, 80% of an A3020 |

### ULC's waveform accuracy stops at about 20 dB, by design

ULC's SNR rises from 17 to about 19.5 dB between 32k and 96k, then stops. 128k mono, and the encoder's maximum of about 174 kbps, are no better.
- **Not frequency-dependent:** the error is the same fraction of the signal in every band.
- **Sine test:** a 440 Hz sine comes back with its envelope rippling by 35%.
- **Cause:** coefficients are quantised to companded nybbles (±1, 4, 9 … 49 on a squared scale) with a shared per-band scale. Extra bits buy bandwidth and more coded coefficients, not finer amplitudes.

That is a perceptual codec doing its job, so waveform SNR understates how it sounds, and a listening test is the real judge. Still, against the VIDC's 37 dB ceiling:
- **High rates:** at 3 bps and above, trellis ADPCM is 9–13 dB more accurate, at a tenth of the CPU.
- **ULC's real territory is 1.5–2.3 bps.** That means 32–48k mono, or 64–96k stereo, where ADPCM can only reach 2 bps (42 kbps), at 22.9 dB with more audible noise.
- **The !App's old configuration was a poor fit:** ULC mono 128k spent 6.1 bps.

### Strengths and weaknesses

| | Strengths | Weaknesses |
|---|---|---|
| ADPCM | About 22 cycles per sample: 6% of an A3000 mono, 12% stereo. Within 2–5 dB of the VIDC ceiling at 4 bits. Every block decodes on its own. One table load per sample. | Fixed rate. Below 3 bps the noise becomes audible. The step size adapts slowly, so transients smear. No perceptual shaping, so its noise is white rather than following the signal. |
| ULC | A real perceptual codec: noise-fill, window switching, M/S stereo. Usable at 1.5–2.3 bps, where ADPCM can't go. | About 229 cycles per sample: 60% of an A3000, and stereo needs an A3020 or better. A complex bitstream. Waveform accuracy stops at about 20 dB, so above about 96 kbps the extra bits buy nothing measurable. |

## Proposals

### 1. Measure, then pick rates (done)

- **Listening grid:** `python player/listen_grid.py` writes 64 clips to `player/build/listen-grid/`, plus `snr.tsv`:
  - all four tracks, mono and stereo;
  - the source through VIDC, ADPCM 4/3/2-bit (greedy and trellis), and ULC from 32k to 128k;
  - each rendered as the Archimedes outputs it.

  Listening to these decides which rate each codec should be used at.
- **The ULC plateau:** explained above. It's the codec's design, not a bug in our tools or files.
- **"Corrupt" files:** five of the `.ulc` files in `music/` looked corrupt. The real cause is a bug in ulc-codec's `ulcDecodeTool.c`: it slides its stream buffer with `memcpy` over overlapping memory, which is undefined behaviour.
  - Built with zig/clang it reports "Corrupted stream" on some files, and it silently changes the output of the others.
  - With `memmove` all 33 files decode cleanly.
  - Our ARM decoder was never affected.
  - **Corrected comparison:** against the corrected C decoder, our ARM decoder's 11-bit output matches to 38.8 dB on Particle and 39.1 dB on demoC (N=2048). The earlier 29.7 dB in PERF.md was measured against the buggy reference.
  - Worth reporting upstream.

### 2. Better ADPCM from the encoder alone, plus 3- and 2-bit variants (done)

- **Trellis encoder (`tools/adpcm_trellis.c`, used by `player/adpcm_encode.py --beam 16 --cost vidc`).**
  - **Search:** it keeps the 16 best (predictor, step) paths through each block instead of choosing each code greedily.
  - **Cost:** it minimises the error *as heard*, through the player's VIDC table, plus a quarter of the predictor's own error. That second term stops the predictor drifting to a rail on hot, clipped masters, where many predictor values sound alike.
  - **Gain:** +1.6 to +2.2 dB at 4 bits, about +1.9 dB on average, with the decoder unchanged.
  - **Search width:** beam 64 adds only another 0.1–0.5 dB.
- **3-bit and 2-bit codes.**
  - **Code layout:** the header's spare word holds the bit width, and 0 still means 4. Codes are a little-endian bit stream, and 3-bit codes straddle word boundaries.
  - **Index-adaptation tables:** the standard [−1,−1,2,4] and [−1,2]. Five alternatives were within ±0.1 dB.
  - **Decoding:** `player/gen_adpcm.py` generates the player's unrolled loops (4/3/2-bit, mono and stereo) and the step tables. It is still one `LDR` per code.
  - **Cost:** 21–22 cycles per sample at every width.
  - **Checks:** the player's ring output is byte-exact against the Python decoder for all six variants, and for old 4-bit files (`player/check_ring.py`).
- **Result:** 3-bit trellis ADPCM at 62 kbps (28.4 dB) is about 2 dB below the old 4-bit encoder at 83 kbps (30.6 dB), using three-quarters of the bits and the same CPU.

### 3. Subband ADPCM, with no multiplies (proposed)

A G.722-style codec; G.722 is the ITU two-band wideband speech codec.
- **Split:** a LeGall 5/3 lifting wavelet divides the signal into 0–5 kHz and 5–10 kHz at half rate each. Synthesis is about 6 adds and shifts per output pair.
- **Low band:** 4-bit trellis ADPCM.
- **High band:** either 2-bit ADPCM, or a per-block envelope plus noise. The noise option is noise substitution, the idea behind SBR, PNS and ULC's own noise-fill. Noise costs about 5 cycles per sample (xorshift plus a table).
- **Estimates:**
  - about 2–3 bps (42–62 kbps);
  - quality close to 4-bit ADPCM where the ear is most sensitive;
  - about 25–30 cycles per sample, about 8% of an A3000.
- **Risk:** the 5/3 filter has soft band edges, so a coarse high band leaks aliasing.
- **Plan:** prototype in Python and score it with the same grid before writing any ARM code.

### 4. "ULC-lite": a cheap-to-decode ULC profile (proposed)

Same codec family, but an encoder profile and a decoder specialised for it:
- **Fixed N=256:** no window-switching sub-blocks.
- **Low-overlap windows (CELT/LC3 style):** the window pass falls from about 40 to about 10 cycles per sample, and low overlap also handles transients.
- **Fully unrolled, generated transform:** with N fixed, every twiddle is a constant. The VIDC needs only about 9–10 bits, so each MUL (8–12 cycles) becomes 2–3 shift-adds through the barrel shifter.
- **Two 16-bit values per 32-bit register** for the butterfly adds.

Estimated at about 130 cycles per sample: A3000 mono about 35%, A3020 stereo about 45%. It needs a fork of the ulc-codec encoder. The unrolled transform doesn't suit ARM3's 4 KB cache, so ARM3 keeps the looped version.

### 5. Log-domain tricks (proposed; they work with any codec)

In VIDC bytes, multiplying the amplitude becomes adding to the chord field: adding `0x20` doubles a sample, and adding `0x20202020` doubles four samples in one ALU op.
- **Gain, fades and envelopes:** one ADD per four samples. The encoder has to stay clear of chord 7 at the top and code 0 at the bottom.
- **Intensity stereo:** decode one channel, then derive L and R with per-block gain offsets. Stereo then costs mono plus about 2 cycles per sample. Panned sources keep their place; wide mixes collapse towards the centre.
- **Gain-shape vector quantisation directly in VIDC bytes:**
  - **Codebook:** per track, of 4-sample shapes stored as VIDC words.
  - **Gain:** applied as an octave offset added to each word.
  - **Cost and rate:** about 3–4 cycles per sample (about 1% of an A3000) at 2–2.5 bps.
  - **Quality:** clearly lowest. A mode for demos that need music almost for free.
- **What the log domain can't do:** addition. Overlap-add, mixing and filtering have to stay linear, so the log domain covers only the last stage, gain and stereo, which the player already does through its tables.

### 6. Considered and rejected

- **Prediction (DPCM) on log values:** waveforms cross zero constantly, and prediction falls apart in the log domain.
- **QOA ("Quite OK Audio"):**
  - **Format:** a 4-tap sign-sign LMS predictor at 3.2 bps.
  - **Cost:** 4 MULs per sample plus weight updates, an estimated 60 cycles per sample on ARM2, three times IMA. Its quality gain doesn't pay for that here.
  - **Worth borrowing:** its small per-slice scale factor, for proposal 3.
- **Acorn Replay:** its audio was 4-bit (IMA) ADPCM, which is what we already have.

## Next steps

1. **Listen to the grid** and decide: ULC at 32/48k mono or 64/96k stereo, against trellis ADPCM at 4 or 3 bits.
2. **Prototype proposal 3 in Python** and add it to the grid.
3. **Proposal 4**, if stereo has to fit an A3000.
4. **Report the `memmove` fix** to ulc-codec.

## Sources

- [QOA specification draft 0.3](https://qoaformat.org/qoa-specification-draft-03.pdf)
- [QOA reference implementation, qoa.h](https://github.com/phoboslab/qoa/blob/master/qoa.h)
- [Acorn Replay compression overview, 1992](https://chrisacorns.computinghistory.org.uk/docs/Acorn/Tech/Acorn_ReplayCompressionProcessOverviewRevCSep92.pdf)
- [Acorn 16-bit Audio Card user guide](https://www.chrisacorns.computinghistory.org.uk/docs/Acorn/Manuals/Acorn_16bitAudioCard_UG.pdf)
- [ARMovie format, multimedia.cx wiki](https://wiki.multimedia.cx/index.php/ARMovie)
- [FFmpeg: Acorn Replay IMA ADPCM decoder](https://ffmpeg.org/pipermail/ffmpeg-devel/2019-July/246212.html)
- [FFmpeg: trellis quantisation for SWF ADPCM](https://lists.mplayerhq.hu/pipermail/ffmpeg-devel/2007-August/023524.html)
- [FFmpeg ticket #3701: IMA QT trellis](https://trac.ffmpeg.org/ticket/3701)
