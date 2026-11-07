# ulcplayer → Acorn Archimedes: feasibility study

Source examined: https://github.com/Aikku93/ulcplayer-gba (cloned 2026-11-06, shallow).
Decoder for [ulc-codec](https://github.com/Aikku93/ulc-codec), an MDCT transform codec.

## TL;DR

Updated 2026-11-06 with the **whole decoder measured** (§10). The spike ran the straight ARMv2 port
of the full GBA decode path on cycle-accurate Arculator: unpack, IMDCT, windowing, lapping, M/S
and a VIDC log-output pass. CPU % is measured in MODE 13 with RISC OS running, at ~65 kbps per
channel. The two figures in each cell are for BlockSize 512 / 2048.

| CPU | Machines | Verdict | Straight port, measured |
|---|---|---|---|
| ARM2 8 MHz + MEMC1a | A3000, later A3x0/A4x0 | **Feasible, mono up to ~15.6 kHz** | Mono 12.5 kHz ≈ 46% / 51%; mono 15.6 kHz ≈ 57% / 63%; mono 20.8 kHz ≈ 76% / 84%, too tight without optimisation. |
| ARM2 8 MHz + MEMC1 | Early A305/310/410/440 | **Marginal** | MEMC1 charges MUL internal cycles as N-cycles, so MUL costs 2×. Mono 12.5 kHz ≈ 66% / 75%. |
| ARM250 12 MHz | A3010, A3020, A4000 | **Feasible: mono, or stereo at 12.5 kHz** | Mono 20.8 kHz ≈ 48% / 54%; stereo 12.5 kHz ≈ 60% / 67%; stereo 15.6 kHz ≈ 75% / 84% |
| ARM3 25 MHz | A5000, A4, upgraded machines | **Feasible, stereo** | Stereo 20.8 kHz ≈ 49% / 59%; stereo 31.25 kHz ≈ 74% / 88% |

The decoder is small, entirely hand-written ARM assembly, and as configured it uses almost
no ARMv4 features. The spike confirmed that porting the code is the easy part: the DCT-IV and
FFT ran unchanged apart from syntax. The limit on ARM2/ARM250 is **MUL**, about 58% of IMDCT
time. On ARM3 it is **stores**: the cache is write-through, so every pass over the buffer runs
at memory speed.

§1–§7 are the original desk study. Where its estimates and the measurements in §8 and §10 disagree, the measurements win.

## 1. What the GBA player actually does

* GBA: ARM7TDMI at 16.78 MHz. The hot code and the 24 KiB transform/lapping buffers sit in IWRAM
  (32-bit, zero wait states).
* The README quotes **~65% CPU for 32768 Hz, 128 kbps M/S stereo**, with BlockSize=2048.
* My own count below comes to ~147 ARM7 cycles per channel-sample ≈ 9.6 M cycles/s ≈ 57%
  for the decoder alone. That fits the 65% figure once the UI/visualiser is added, so the model
  is roughly calibrated.

### Pipeline per block (per channel)
1. **Coefficient unpack** (`ulc_UpdatePlayer.s`): a nybble-stream reader, a LUT dequantiser patched
   by self-modifying code (SMC), zero runs, and an xorshift noise fill. Cheap: ~8 cycles/coef at 128 kbps.
2. **Inverse DCT-IV, in place** (`Fourier_DCT4_InPlace.s`): pre-rotate, N/2-point complex FFT, post-rotate.
   The twiddles come from shift-add quadrature oscillators, not tables, and the oscillator shift
   counts are patched by SMC.
3. **FFT** (`Fourier_FFT_InPlace.s`): radix-2, bit-reverse, hand-written 4-point and 8-point stages,
   then general stages with a 4-MUL complex multiply per butterfly.
4. **Windowed overlap-add**, lapping-buffer shuffles, then shift/clip to 8-bit, plus a mid/side→L/R conversion.

### Multiply count (N = 2048)
| Stage | MULs per block |
|---|---|
| Pre-rotation | 2N |
| Post-rotation | 2N |
| FFT general stages (log2N − 4 = 7 of them) | 7N |
| FFT 8-point stage | N/4 |
| Overlap window (worst case) | 2N |
| **Total** | **≈13.25 MULs per output sample per channel** |

At 32768 Hz stereo that comes to ~870k MULs/s.

## 2. Instruction-set compatibility

`ulc_Specs.h` defaults: `ULC_USE_64BIT_MATH 0`, `ULC_USE_INPLACE_XFM 1`, `ULC_PRECISE_DCT 0`.
With those settings, the ARMv4-only instructions are:

| Feature | Where | ARM2/250/3 fix | Cost |
|---|---|---|---|
| `SMULL/SMLAL` | Only inside `#if ULC_USE_64BIT_MATH` | Leave disabled | none |
| `UMULL` | `.LDecodeCoefs_Stop_NoiseFill` (decay envelope, high word only) | 16×16 MUL with a 16-bit decay, or a short shift-add | trivial (cold path) |
| `LDRH/STRH`, `LDRMIH` | Header fields, `ulc_State`, SMC patch of `.LDecodeCoefs_Normal_Shifter` | Build the full 32-bit opcode and `STR` it; `LDR`+mask for fields | trivial |
| `MRS/MSR` (IRQ lock) | `ulc_UpdatePlayer` | `TEQP pc,#…` / `ORR pc` 26-bit idiom, or `SWI OS_IntOff/On` | trivial |
| `BX` | Every return | `MOV pc,lr` / `LDMFD …,{…,pc}` (decide on PSR preservation with `^`) | trivial |
| Thumb | `ulc_StartPlayer.s`, parts of `ulcplayer.s` | Rewrite in ARM (all setup code) | small |
| `__aeabi_uidiv` | Rate→timer period | Not needed: VIDC takes a period in µs | none |
| GBA hardware (DMA FIFO, timers, VBlank, BG/OBJ graphics) | StartPlayer/StopPlayer/Sync, `ulcplayer.s` UI | Replace with VIDC sound DMA plus a new (simpler) UI | the real work |

**Self-modifying code:** fine on ARM2/ARM250 (no cache). On ARM3 the 4 KB cache is unified and
write-through, so data writes should keep the instruction view coherent. **Verify this on real
ARM3 hardware.** (StrongARM would need cache flushes, but it is out of scope here.)

**Assembler:** the source is GNU-as syntax run through `cpp` (`#if`, `.macro \arg`). Either keep
`arm-none-eabi-gcc -E` as a preprocessor and assemble with `-mcpu=arm2 -mno-thumb-interwork`,
or convert to vasm's GAS-compatible `std` syntax (`vasmarm_std -m2 / -m250 / -m3`).

**Memory:** ~3 KB code plus 24 KB buffers (N=2048 stereo) plus 8 KB output buffers. Trivial on any Archimedes.

## 3. The bottleneck: MUL on ARM2/ARM250

| | ARM7TDMI (GBA) | ARM2 / ARM250 |
|---|---|---|
| MUL, Rs = 16-bit positive | 1S + 2I ≈ 3 cyc | 1S + ~8I ≈ 9 cyc |
| MUL, Rs negative | 1S + 2I ≈ 3 cyc (early-out on all-1s) | **1S + 16I = 17 cyc** (early-out only on all-0s, I believe; verify) |
| LDR / STR | 3 / 2 | ~4 / 4 (MEMC N-cycle = 2 ticks) |
| LDM/STM ×4 | 5 | ~6 |
| Branch | 3 | ~4 |

In every `MUL` here, `Rs` is the twiddle/window value. In the FFT, the oscillator sweeps 0..π, so
**the cosine term is negative half the time**, and that is the worst case on ARM2. A straight
port averages about 12 cycles per MUL.

Video DMA also takes memory bandwidth on ARM2/ARM250. I've assumed ~15% lost, assuming a modest
screen mode during playback.

### Cycle model (per channel-sample)
| | Non-MUL | MUL | Total |
|---|---|---|---|
| GBA (reference) | ~101 | 13.25 × 3.5 ≈ 46 | ~147 |
| ARM2/250 straight port | ~120 | 13.25 × 12 ≈ 160 | **~280** |
| ARM2/250 optimised (see §4) | ~115 | ~10 × 7 ≈ 70 | **~190** |
| ARM3 straight (core cycles, incl. ~25% cache-miss penalty) | | | **~300** |
| ARM3 optimised | | | **~200** |

### Throughput (channel-samples/s at a 70% CPU budget)
| CPU | Usable cycles/s | Straight | Optimised |
|---|---|---|---|
| ARM2 8 MHz | ~6.8 M | ~17k (mono 15.6 kHz) | ~25k (mono 20.8 kHz, stereo ~12 kHz) |
| ARM250 12 MHz | ~10.2 M | ~25k (mono 20.8 kHz) | ~37k (mono 31 kHz / stereo 15.6 kHz) |
| ARM3 25 MHz | ~25 M (core) | ~58k (stereo 20.8 kHz+) | ~87k (stereo ~31 kHz) |

ARM3 caveat: with BlockSize=2048 the transform buffer alone is 8 KB, twice the cache, so the FFT
stages will thrash. **Encode with BlockSize 512–1024 for ARM3.** That shrinks the working set to
2–4 KB at a small cost in coding gain, and cuts the FFT's log₂N factor slightly.

## 4. Optimisations for the Archimedes

In order of payoff:

1. **Keep `Rs` positive and short.** Fold the FFT twiddles into the first octant/quadrant using
   the usual symmetries (w, −j·w̄, …), so cos and sin are always ≥ 0. Then cut twiddle precision
   from Q15 to ~Q11–12, which drops ARM2 MUL to ~6–7 cycles. Expect roughly −30–40% decode time
   on ARM2/250. The cost to quality is negligible against an 8-bit log output.
2. **Radix-4 or split-radix FFT:** about 25–33% fewer complex multiplies, and fewer passes over
   memory, which also helps ARM3's cache.
3. **Smaller BlockSize** (512/1024): this affects the encoder only, and it is essential on ARM3.
4. **Sample rates that suit VIDC:** 15625 Hz (64 µs) and 20833 Hz (48 µs, the RISC OS default).
   VIDC's period is in whole µs, so 32768 Hz can't be hit exactly. Encode at the playback rate.
5. **Output stage:** VIDC1 is 8-bit logarithmic (sign + 7-bit log), not linear. Replace
   `SCALE_AND_CLIP` with clip-to-~12 bits plus a linear→log LUT (one LDRB per sample). Do the
   mid/side→L/R step on the 32-bit samples *before* the conversion, not on 8-bit as the GBA code
   does. This should sound better than the GBA's 8-bit linear output.
6. **Low-bandwidth screen mode** during playback on ARM2/250 to reduce video DMA stalls.

## 5. Platform integration

* **Sound output:** two VIDC channels with stereo positions hard L/R, interleaved bytes. Either:
  (a) install a channel handler via the SoundChannels/SoundDMA interface (the right choice for a
  desktop-friendly player on RISC OS 2/3.1), or (b) take over the SoundDMA buffer-fill
  interrupt directly (demo style, fewest cycles). Keep the GBA's double-buffer and `nBufProc`
  handshake, and decode in the foreground.
* **Block timing:** a 2048-sample block at 20.8 kHz is ~98 ms, far bigger than the RISC OS sound
  buffer (typically 208 bytes/channel). Use a ring buffer of ≥2 decoded blocks that the sound
  handler drains, which is the GBA design with a different consumer.
* **Data source:** at 128 kbps, a 4 MB machine holds ~3.5 min in RAM; at 64 kbps (sensible for
  mono/20.8 kHz) ~7 min in 3.5 MB. Streaming from IDE/ST506 hard disc at 8–16 KB/s is easy. From
  floppy it's possible at 64 kbps, but a 2048-sample block decode spans several VSyncs, so plan
  the buffering.
* **Encoder:** use the upstream ulc-codec PC tool. Check that the bitstream version matches the
  decoder (`ULC2` magic). No encoder changes are needed, just its parameters (rate, BlockSize,
  bitrate/quality, mono).

## 6. Suggested plan

* **Phase 0 — done 2026-11-06, see §8.** Remaining: confirm on real hardware. Original plan: port `Fourier_FFT_InPlace` and `Fourier_DCT4_InPlace` as-is
  (they need only the `BX` fix) into a bench harness. Time IMDCT of N = 256/512/1024/2048 with
  the IOC timer on Arculator (A3000/ARM2, A3020/ARM250, A5000/ARM3), and ideally on real
  hardware. Also confirm ARM2's negative-Rs MUL timing and SMC behaviour on ARM3. This replaces
  every estimate above with a number.
* **Phase 1 (straight port): decoder done 2026-11-06, see §10.** It decodes and verifies on all
  three CPUs. Remaining: a real player around it (sound DMA buffer handler, RAM-resident file,
  ARM3 first, then ARM250), and timing a stream from the real encoder.
* **Phase 2 (optimise):** positive/short twiddles, then radix-4; swap the window MUL operands
  and fold the VIDC table into the clip (§10). Then retest the ARM2 targets.
* **Phase 3 (player):** disc streaming, track list, simple visualiser (the GBA's waveform view
  maps well onto a mode 13 or mode 9 screen).

## 7. Risks / unknowns

* The cycle model is static. Real numbers depend on how often zero-run and noise-fill codes
  occur (bitrate) and on video mode. **Settled (§10):** bitrate moves the cost by only
  ~29 cycles/sample per nybble/coefficient. The test streams come from a simple stand-in encoder;
  a stream from the real encoder is still to be timed.
* ARM2 MUL early-termination with negative Rs: confirmed in the emulator (§8), which follows
  Furber's Booth model. Still to check on real silicon.
* ARM3 cache coherence for the self-modifying oscillators: works in the emulator (§8), where the
  output is identical to ARM2. Still to check on a real ARM3.
* VIDC log output plus low rates (15–21 kHz) cap fidelity regardless of the codec. Quality will
  be "very good for an Archimedes", not CD.
* Licence: the player is public domain (Unlicense), so porting and redistribution are fine. The
  GBA demo's music is third-party, so don't ship it; use your own test material.

## 8. Spike results (measured, 2026-11-06)

Code and driver: `spike/` (see `spike/README.md`). `fourier.asm` is a straight ARMv2 port of
`Fourier_DCT4_InPlace` + `Fourier_FFT_InPlace` with the GBA defaults (in-place, approximate DCT,
32-bit MULs). The only changes were syntax, `BX`→`LDM …pc`, and named labels. The self-modifying
oscillators were kept, and they work on all three CPUs, including ARM3.

Method: RISC OS 3.11 booted in `arculator-headless` (the Arculator MCP's emulator), with the
harness run from the command line. A breakpoint on a marker routine reads the emulator's CPU
cycle counter before and after each transform. The figures therefore include real-world overhead:
RISC OS interrupts, DRAM refresh and video DMA. Each size runs twice and the second (warm)
run is reported. Input is sparse, decaying random coefficients. The output is checked against a
float DCT-IV.

**Correctness:** 39.7–44.8 dB SNR against float at every size, on every machine (bit-identical
across machines). That fits the approximate DCT and 15-bit precision. It is above the ~38 dB
of 8-bit linear output and around what VIDC's 8-bit log format resolves.

### IMDCT core, cycles per output sample (MODE 13, 320×256×256, the worst common case)

| N | ARM2 + MEMC1 | ARM2 + MEMC1a | ARM250 (12 MHz cyc) | ARM3 (25 MHz cyc) |
|---|---|---|---|---|
| 256 | 274 | 175 | 179 | 170 |
| 512 | 307 | 195 | 186 | 194 |
| 1024 | 343 | 217 | 210 | 222 |
| 2048 | 376 | 239 | 231 | 261 |
| **N=2048 time per block** | 96 ms | 61 ms | 39 ms | 21 ms |

In MODE 0 (20 KB screen) ARM2/ARM250 run about 4–7% faster, and ARM3 is unchanged.

### MUL micro-benchmark (cycles per MUL, Rs value as given)

| | Rs = +0x7FF6 | Rs = −0x7FF6 | Rs = +0x7F |
|---|---|---|---|
| ARM2 + MEMC1 | 20.5 | 40.9 | 11.4 |
| ARM2 + MEMC1a | 10.1 | 17.4 | 5.5 |
| ARM250 | 9.5 | 17.5 | 5.4 |
| ARM3 (cold cache) | 12.0 | 19.9 | 7.9 |

This confirms the §3 assumption: **ARM2-family MUL only terminates early when Rs is positive**.
A negative twiddle costs the full 16 Booth steps. The FFT's cos term is negative for half of
each stage.

### What the measurements changed

* The IMDCT is about 15% cheaper than the desk model on ARM2/ARM250, and the straight port
  already reaches what §3 expected only after optimisation. **ARM2 + MEMC1a does mono 20.8 kHz
  without optimisation** at BlockSize 512.
* **MEMC1 is a separate, much slower tier.** It doubles MUL cost and makes the IMDCT 1.6× slower,
  so early A305/A310/A440 machines without the MEMC1a upgrade are marginal.
* **ARM3 gains less than its clock suggests**: about 2.8–3× ARM2, not 3×+, and its cost per
  sample *rises* with N (the cache thrashes at N ≥ 1024). It is bound by stores (write-through
  at 12 MHz MCLK), not MUL. That favours BlockSize 512 and radix-4 (fewer passes) over MUL tricks.

### Optimisation headroom (estimated from the measured split)

MULs are ~11.25 per sample at N=2048, about 140 of ARM2's 239 cycles. Positive Q12 twiddles
(~7.8 cycles/MUL, interpolated from the 0x7F and 0x7FF6 points) would save about 50 cycles/sample,
and radix-4 about another 15–25. Expected optimised IMDCT: **~165–175 cycles/sample on
ARM2/ARM250**, which would make ARM250 stereo 20.8 kHz (~70%) and ARM2 stereo ~12.5 kHz realistic.

### Not measured yet

Superseded by §10: the rest of the decoder is now ported and measured, and it costs more than the
~60 cycles/sample estimated here. Still not measured: sound DMA running during decode, and real hardware.

## 9. Comparison with a 4-bit ADPCM player

A standard IMA 4-bit ADPCM decoder costs about **50 ARM2 cycles per sample, plus ~11 for the VIDC
pass**. That's measured (2026-11-06) with the IMA decoder built into `player/`, over the same output
path as ULC: 16% CPU for mono and 33% for stereo at 20.8 kHz on an A3000 (MEMC1a), and 9% for mono
on the ARM250. The earlier desk estimate of 30–35 cycles was too low. (I haven't seen the existing
Archimedes ADPCM player's source; a hand-tuned table-driven decoder could beat this.) That is ~5× cheaper than ULC's measured ~290–325 (§10).

| | 4-bit ADPCM | ULC |
|---|---|---|
| Method | Per-sample prediction plus a 4-bit residual | Frequency-domain (MDCT) with a perceptual bit allocation |
| Size | Fixed 4 bits/sample (2:1 against 8-bit log) | Variable, typically 1–2 bits/sample (4–8:1) |
| ARM2, mono 20.8 kHz | **16% CPU** (measured) | **~76–84% CPU** (measured, §10) |
| Stereo 20.8 kHz | 33% on ARM2 | ARM3 ~49–59% (ARM250 too slow) |
| Data rate, mono 20.8 kHz | 83 kbps (10.4 KB/s): ~6 min per 4 MB | ~32–48 kbps (4–6 KB/s): ~11–16 min |
| Data rate, stereo 20.8 kHz | 167 kbps (21 KB/s): ~3 min per 4 MB | ~64–96 kbps (8–12 KB/s): ~6–8 min |
| Artefacts | Steady hiss, dull/gritty transients and cymbals | At low rates: pre-echo before sharp attacks, swirly highs |
| Latency / seeking | Per-sample, instant | Blocks of 256–2048 samples, ~100 ms of buffering |

* **At equal bitrate ULC wins easily.** ADPCM can't go below 4 bits per sample without sounding
  much worse.
* **At ADPCM's own bitrate the gap mostly disappears.** VIDC's 8-bit log output and the
  ~20 kHz rate are the real ceiling, and they hide much of ADPCM's hiss. ULC's gain on Archimedes
  is mostly **2–4× more playing time per MB**, not better sound.
* **Use ADPCM** when audio must run alongside a game or demo effect (it leaves ~84% of an ARM2
  free), or when you need low latency or to jump to any point.
* **Use ULC** when storage or bandwidth is the limit: long tracks in RAM, streaming from floppy
  (21 KB/s stereo ADPCM is marginal there), stereo on ARM3, or as a technical showpiece.

## 10. Whole-decoder results (measured, 2026-11-06)

Code: `spike/decoder.asm` (decoder), `spike/bench_dec.asm` (harness), `spike/run_decode.py` (driver).
See `spike/README.md`.

**What was ported.** The GBA decode path from `ulc_UpdatePlayer.s`, run once per block:
- nybble reader
- self-modifying dequantiser
- zero runs, noise fill and tail noise fill
- window switching (all 16 sub-block patterns)
- IMDCT (the §8 code)
- windowed overlap with the oscillator patching
- lapping buffer
- 8-bit clip
- M/S → L/R

Changes for ARMv2 were mechanical:
- halfword loads and stores became word ones
- `MUL ip,ip,ip` (illegal on ARMv2) goes through a spare register
- the tail noise fill's `UMULL` became two 16×16 MULs, which is bit-exact for its 15-bit amplitude

The DMA and pause bookkeeping was dropped: a player decides when to call the decoder.

A separate **VIDC pass** converts 8-bit linear output to VIDC log bytes through a 256-byte table, and interleaves stereo.

**Test streams.** The main tables use `MiniEncoder`; the real encoder's results, which agree, follow later in this section. The streams came from `MiniEncoder`, a deliberately simple ULC2 encoder in
`spike/ulc_tools.py`, because the real encoder couldn't be run in this session. Its streams:
- are valid ULC2 and exercise every bitstream code
- force a window switch every 4th block, cycling through all 12 sub-block patterns
- encode 3 s of synthetic music

Their quality is poor, but **decode cost depends only on block size and nybbles per coefficient**,
which are reported. The headline rate is 0.78 nybbles/coef, i.e. ~65 kbps per channel at 20.8 kHz.

**Correctness.**
- The **coefficient stage is bit-exact** against a reference decoder that models the ARM's
  integer maths. I checked every sub-block at a breakpoint before the IMDCT, on a noise-heavy stream.
- The **final output** agrees with the reference (float IMDCT, then the ARM's 8-bit truncation):
  - mono: 89–93% of samples identical, max error 3–4 LSB, about 30 dB SNR
  - stereo: 88–92% identical, because the GBA does M/S on already-truncated 8-bit values
- The **VIDC pass** is exact.
- Output is **identical on all four machines**.

Two quirks inherited from the GBA code, both inaudible:
- Output polarity is inverted relative to the C reference decoder: the dequantiser table is
  indexed by −nybble, and noise fill is not negated.
- The tail noise fill's amplitude is a truncated integer, so quiet tails die away faster than the spec says.

### Decode + VIDC pass, cycles per sample frame (MODE 13, ~65 kbps/channel)

Each cell is decode + VIDC pass = total. Stereo figures are per stereo frame (two channel-samples).
The worst block is 5–8% above the mean.

| Stream | ARM2 + MEMC1 | ARM2 + MEMC1a | ARM250 | ARM3 25 MHz |
|---|---|---|---|---|
| Mono, N=2048 | 470 + 13 = **483** | 313 + 11 = **324** | 299 + 11 = **310** | 335 + 10 = **345** |
| Mono, N=512 | 409 + 13 = **422** | 280 + 11 = **292** | 267 + 11 = **278** | 277 + 9 = **286** |
| Stereo, N=2048 | 963 + 24 = **987** | 652 + 21 = **672** | 622 + 19 = **641** | 691 + 16 = **707** |
| Stereo, N=512 | 842 + 24 = **866** | 586 + 21 = **606** | 558 + 19 = **577** | 575 + 16 = **591** |

The N=512 streams ran at ~1.15 nybbles/coef rather than 0.78, which makes them about 10 cycles/sample pessimistic.

### CPU % (total from the table above; N = 512 / 2048)

| Machine | 12.5 kHz | 15.6 kHz | 20.8 kHz | 31.25 kHz |
|---|---|---|---|---|
| ARM2 + MEMC1, mono | 66 / 75 | 82 / 94 | — | — |
| ARM2 + MEMC1a, mono | 46 / 51 | 57 / 63 | 76 / 84 | — |
| ARM2 + MEMC1a, stereo | 95 / 105 | — | — | — |
| ARM250, mono | 29 / 32 | 36 / 40 | 48 / 54 | 72 / 81 |
| ARM250, stereo | 60 / 67 | 75 / 84 | 100 / 111 | — |
| ARM3, stereo | 30 / 35 | 37 / 44 | 49 / 59 | 74 / 88 |

### What the measurements changed

- **The non-IMDCT part costs ~70–95 cycles/sample including the VIDC pass**, not the ~60 estimated
  in §8. For example, on the ARM250 at N=2048 it's 299 − 231 = 68 for decode plus 11 for the VIDC pass. So
  every verdict moves down by about one sample-rate step:
  - ARM2 + MEMC1a: mono 20.8 kHz (76–84%) now needs Phase 2; mono 15.6 kHz is comfortable.
  - ARM250: stereo is 12.5 kHz (or 15.6 kHz at N=512).
  - ARM3: stereo 31.25 kHz only at N=512.
- **Bitrate hardly matters.** On ARM2 + MEMC1a, going from 18 to 65 to 93 kbps costs 300 → 313 → 327
  cycles/sample: about 29 cycles/sample per nybble/coefficient. The IMDCT dominates.
- **Window switching makes blocks cheaper, not dearer.** Short sub-blocks have cheaper IMDCTs, so
  transient blocks run ~8% below long ones. The worst case is a long block at a high rate.
- **Prefer N=512** on every CPU: it's 10–17% cheaper per sample than N=2048, even at a higher bitrate, and most of all on ARM3, where the cache thrashes at larger N.
- **Stereo costs 2× mono plus ~20–25 cycles/frame** for M/S and interleaving.

### New optimisation targets found in the rest of the decoder

- **Window overlap MULs** (2 per sample) use the data as Rs, which is mixed-sign, so they're slow on ARM2.
  Swapping the operands so the positive window coefficient is Rs should save ~5–10 cycles/sample.
- **Fold the VIDC table into the clip** for mono: one `LDRB` per sample instead of a second pass,
  saving ~8 cycles/sample. For stereo, doing M/S at 1.15 precision before the table would fix
  the double truncation too.
- With the §8 IMDCT savings (~50–75 cycles/sample), an optimised decoder should come to roughly
  **215–240 cycles/sample on ARM2 + MEMC1a / ARM250**. That would bring mono 20.8 kHz on an A3000 (~56–63%)
  and stereo 15.6 kHz on ARM250 (~56–63%) within reach. That's an estimate, not a measurement.

### Real encoder, real music (added 2026-11-06)

The real `ulcencodetool` (built from Aikku93/ulc-codec with `zig cc`) encoded these, resampled to 20833 Hz:
- 3 s excerpts of three of your tracks (ArchieShaderToy demos B, C and D)
- the whole 86 s *Archie Particle demo*

They're at 64 kbps mono and 128 kbps stereo (~0.76 nybbles/coef). The output ring in `bench_dec.asm`
lets a stream of any length run: every block is timed, and the last ~3 s are verified.

Cycles per sample frame, decode + VIDC pass (MODE 13). The figures are means; *worst* is the worst
block over the whole 86 s track.

| Stream | ARM2 + MEMC1 | ARM2 + MEMC1a | ARM250 | ARM3 25 MHz |
|---|---|---|---|---|
| Mono, N=512 (86 s track) | 442 (worst 470) | 300 (worst 314) | 286 (worst 300) | 294 (worst 314) |
| Mono, N=2048 (demo C) | 468 | 318 | 303 | 335 |
| Stereo, N=512 (86 s track) | 912 (worst 960) | 624 (worst 653) | 596 (worst 622) | 610 (worst 639) |
| Stereo, N=2048 (demo C) | 972 | 667 | 635 | 696 |

- **The stand-in encoder's numbers hold.** Real streams come within ~1–3%, so the CPU % table
  above stands.
- **The worst block is only ~5% above the mean** over a whole track at N=512, and ~10% at N=2048.
  A player can budget on the mean plus 5–10%.
- **Correctness on real streams:** the coefficient stage matches as before. The output is 82–92%
  identical to the reference after 8-bit truncation, and the VIDC pass is exact.
- **Very loud masters clip.** Demo B (rms −10 dBFS) clips on 0.25% of samples. There the
  GBA's approximate clip (it only catches overflow of up to 2×) differs from a hard clip by up to
  20 LSB. Normalise to about −1 dBFS before encoding, or replace the clip with a full one (+2 instructions per sample).

### Still not measured

- **Sound DMA** running during decode.
- **Real hardware.**

### Measured sizes and listening files (added 2026-11-06)

The 86 s *Archie Particle demo* at 20833 Hz:

| Format | Mono | Stereo |
|---|---|---|
| IMA ADPCM 4-bit (83 / 167 kbps) | 901 KB | 1.80 MB |
| ULC 64 / 128 kbps | 682 KB (0.76×) | 1.37 MB |
| ULC 48 / 96 kbps | 511 KB (0.57×) | 1.02 MB |
| ULC 32 / 64 kbps | 340 KB (0.38×) | 682 KB |

Waveform SNR against the source is 29 dB for ADPCM and 18–20 dB for ULC. That's expected: ULC swaps
high-frequency detail for shaped noise, which counts as error but is meant to sound right, so
judge by ear.

Recordings of 20 s of the Archimedes playing each version (emulated ARM3, through VIDC) are in
`player/build/listen/`. All the files are in `spike/build/real/`: `*.ulc`, `*-adpcm.adp`, and the 20833 Hz WAV sources.
