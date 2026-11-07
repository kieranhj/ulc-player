# ulc decoder spike

Two benchmarks on emulated ARM2, ARM250 and ARM3:

1. **IMDCT only** (`bench.asm`, `run_bench.py`): the ulc-codec inverse transform (DCT-IV + FFT).
   Results in `../FEASIBILITY.md` §8.
2. **Whole decoder** (`bench_dec.asm`, `run_decode.py`): a ulc stream decoded block by block,
   plus the VIDC output pass. Results in `../FEASIBILITY.md` §10.

| File | What it is |
|---|---|
| `fourier.asm` | Straight ARMv2 port of `Fourier_DCT4_InPlace.s` + `Fourier_FFT_InPlace.s` from ulcplayer-gba (GBA defaults, self-modifying oscillators kept) |
| `bench.asm` | RISC OS Absolute harness for the IMDCT. It times N = 256…2048 (each twice) and runs a MUL micro-benchmark. A host breakpoint on `mark` (&8008) reads the cycle counter, and r12 carries the tag. |
| `run_bench.py` | Builds the input and the binary, drives `arculator-headless`, reads cycles and output, and checks the output against a float DCT-IV |
| `decoder.asm` | ARMv2 port of the block decoder from `ulc_UpdatePlayer.s` (unpack, IMDCT call, windowed overlap, lapping, 8-bit clip, M/S). The header comment lists every change. |
| `bench_dec.asm` | Harness for the whole decoder. It decodes `cfg_nblk` blocks of `build/stream.ulc` with a mark (&800C) after each block, then runs the VIDC pass (8-bit linear → VIDC log via a 256-byte table, interleaving stereo). |
| `run_decode.py` | Makes or loads a stream, builds, runs every machine, and checks the ARM output against a reference decoder |
| `ulc_tools.py` | Synthetic test music, a WAV reader, `MiniEncoder` (a deliberately simple ULC2 encoder), `RefDecoder` (reference decoder, see below), and the VIDC table |
| `configs/a3000-memc1a.cfg` | ARM2 + MEMC1a. The MCP's `a3000-riscos311` is MEMC1, which is 2× slower for MUL. |
| `build/results-mode*.json` | Last IMDCT results |
| `build/decode-*.json` | Last whole-decoder results (`run_all.sh` makes them all) |

## Run

```sh
python spike/run_bench.py --mode 13      # IMDCT, all machines; --configs a,b to pick; --mode 255 = leave desktop mode
```

A full IMDCT run on four machines takes about 3 minutes.

```sh
python spike/run_decode.py                       # mono, N=2048, 20.8 kHz, synthetic music via MiniEncoder
python spike/run_decode.py --stereo --coarse 4   # M/S stereo
python spike/run_decode.py --ulc song.ulc        # a stream from the real ulcencodetool (preferred)
python spike/run_decode.py --wav song.wav        # a 16-bit WAV, encoded with MiniEncoder
bash spike/build/run_all.sh                      # the whole matrix in FEASIBILITY.md §10 (~25 min)
```

`MiniEncoder` is not the real encoder. It exists so the decoder can be checked and timed without
it. It makes valid ULC2 streams: sine-window MDCT, a quantiser per band, zero runs, noise fill,
tail noise fill, and a forced window switch every 4th block (cycling through all 12 sub-block
patterns), so every decoder path runs. Its quality is poor, but decode cost depends only on block
size and on nybbles per coefficient, which the run prints. `--coarse` sets the rate: 1 ≈ 1.1,
4 ≈ 0.78 and 5 ≈ 0.2 nybbles/coef. A stream from the real encoder is still the better test.

`RefDecoder` follows `libulc/ulcDecoder.c`. By default (`arm_precision=True`) its coefficient
stage mirrors the ARM decoder **bit for bit**:
- The GBA dequantiser table is indexed by −nybble, so tonal coefficients come out negated and
  noise fill does not. That's inaudible, but it means the ARM output's polarity differs from the C decoder's.
- Values are truncated 1.15 integers; quantisers 2^-(5+18) and finer decode to zero.
- Noise fill is seeded from the reader registers (r6/r7) and uses the ARM's integer amplitudes.

The IMDCT and windowing are then float, so the remaining differences are the ARM's fixed-point
transform and the 8-bit truncation.

Needs:
- vasm at `Archie/Projects/vasm_1.9/vasmarm_std.exe`, with `-m2 -opt-adr`. Use `.long` (vasm's `.word` is 16-bit) and `.short`, not `.hword`.
- The Arculator MCP tree at `Projects/arculator-sarah`. Its Python client is used directly, so the MCP doesn't need to be registered.
- numpy, for `ulc_tools.py`.

## Gotchas

- An Absolute (`,ff8`) file gets no stack from RISC OS. The harnesses set `sp` from `OS_GetEnv`.
- With `-opt-adr`, vasm's listing symbol table can be stale: labels after an expanded `ADR`
  are a few bytes out. `bench_dec.asm` puts its buffer addresses at &8010, and the host reads
  them from the image. Find breakpoint addresses in the image, not the listing.
- `STMIA r4, {r3,r5,r1,r2,r6}` stores in register order, not list order.
- vasm (std syntax) doesn't substitute omitted macro arguments that have defaults, so pass every argument.
- ARMv2 forbids `MUL Rd, Rm, …` with Rd = Rm. The GBA source has one (`MUL ip, ip, ip`).
