# Performance analysis: ULC and ADPCM players (2026-11-07)

This is a measured profile of both players on ARM2, ARM250 and ARM3. Each stage is checked against what the hardware actually charges for it, with a ranked list of optimisations.

**Short answer: neither player is as fast as it could be.**
- **ULC:** the decoder is a faithful port of a GBA design. It is multiply-bound everywhere, makes about four passes over the block, and keeps GBA trade-offs (sine/cosine oscillators instead of tables) that suit ARM3 but not ARM2.
  - The estimated saving is **30–40% of decode time on every machine**.
  - That would bring **stereo 128 kbps at 20.8 kHz within reach of an A3020**.
- **ADPCM:** this player can get about **3× faster** with a table-driven decoder.
- **Buffer fill:** already near-optimal. It's a block copy, far cheaper than QTM's per-sample mixing fills, and there is nothing material left to gain.

## Implementation results (2026-11-07)

Items 1–6 of the recommended order below are done; item 7 was dropped (see below). Every figure is measured in the player with `profile_player.py` (Particle, ULC mono 128 kbps, N=512).

| | A3000 (ARM2) | A3020 (ARM250) | A5000 (ARM3) |
|---|---:|---:|---:|
| Player CPU, ULC mono 128k | 82% → **60%** | 52% → **39%** | 25% → **20%** |
| ULC decode + VIDC, c/s | 314 → **229** | 298 → **225** | 299 → **241** |
| Player CPU, ULC **stereo** 128k | – | over 100% → **80%**, 0 underruns | – |
| ADPCM decode + VIDC, c/s | 60 → **21.8** | → 21.2 | → 19.7 |
| Player CPU, ADPCM mono / stereo | 15% / 30% → **6% / 14%** | 3% | 1% |

Per stage, A3000, c/s:

| Stage | Before | After | What changed |
|---|---:|---:|---|
| FFT | 133.4 | 85.4 | table twiddles with non-negative Rs, .12 twiddles, bit-reversal folded into the 4-point stage, radix-4 |
| DCT-IV pre/post | 60.6 | 54.8 | 3-MUL rotation at .12 (`CMUL3`) |
| Window + overlap | 51.1 | 39.9 | window value as the MUL's Rs, then 3-MUL rotation (`OV_ROT`) |
| Unpack + dequantise | 45.1 | 35.3 | one table load per nybble instead of a compare chain |
| Lapping + VIDC | 23.5 | 13.1 | VIDC lookup fused into lapping (11-bit index), straight into the ring |
| MODE 13 → 9, status 1 Hz | | | about −5% on ARM2 |

DCT-IV alone (`spike/run_bench.py`, MODE 13), c/s at N=512: ARM2 199.5 → 149.9 (−25%), ARM3 193.3 → 158.2 (−18%), with accuracy against a float DCT-IV unchanged at every N from 64 to 2048.

**Quality.**
- The ULC output is now 11-bit into the VIDC log table rather than truncated to 8 bits. Against ulc-codec's C decoder that is 18.9 → 29.7 dB on Particle (the rest is mostly noise-fill, which is random in both decoders).
- The transform changes keep the 11-bit output within 1–2 LSB of the original port:
  - Particle N=512: 29.7 dB against C, unchanged.
  - demoC N=2048: 38.4 → 39.0 dB.
  - demoB N=512: 36.5 → 35.8 dB.
- ADPCM output is a 12-bit VIDC lookup of the IMA predictor, byte-exact against the Python decoder (`player/check_ring.py`).

**What turned out differently from the estimates.**
- **Unpack:** a `LDR pc` jump table would not have paid on ARM2, because the pipeline refill costs as much as the compare chain. A table of coefficients whose special entries hold handler addresses does: 3 instructions on the common path.
- **3-MUL rotations:** they gained less than estimated, because the loops become as much ALU-bound as MUL-bound.
- **Item 7 (merge post-twiddle and window):** dropped. With 3-MUL rotations, the merged rotation needs 6 MULs per output pair against 4.5 for the separate passes. What's left to gain is the two copy passes, `swap_loop` and `rev_loop`: about 11 c/s on ARM2 and 17 on ARM3. Getting it would mean ping-ponging DCT output buffers through the window-switching lapping logic.
- **Not done:** `STM` bursts for unpack stores on ARM3, because there are no free registers in `dc_loop`. Tables in place of the DCT-IV/window oscillators were also skipped, because there is no small table that serves every N.

**Test tools added.**
- `spike/check_vs_cdec.py`: now reads the decoder's full 11-bit output, in two runs.
- `spike/vs_golden.py`: compares that output with a saved run.
- `player/check_ring.py`: checks the player's sound ring byte for byte.
- `spike/run_bench.py`: now covers N=64 and 128.

## How it was measured

- **Tool:** `player/profile_player.py` runs Arculator's per-instruction profiler over the player image while it plays a track. The profiler records execution counts and cycle costs, including DRAM page misses, video DMA contention and ARM3 cache behaviour.
- **Attribution:** cycles go to labels using true addresses recovered by `player/symmap.py`. vasm's `-opt-adr` listing is stale.
- **ROM profile:** `--rom` profiles the 2 MB RISC OS ROM instead.
- **Stream:** the !App's Particle track, ULC mono 128 kbps, N=512, 20833 Hz. ADPCM is the same track as IMA 4-bit mono.
- **Screen:** the player's MODE 13 display, 4 s of steady playback, 0 underruns.
- **Machines:**

| Config | Machine | CPU clock |
|---|---|---|
| A3000 | ARM2 + MEMC1a | 8 MHz |
| A3020 | ARM250 | 12 MHz |
| A5000 | ARM3 25 MHz, cache on (`*Cache` → "Cache on") | 25 MHz CPU, 12 MHz memory |

Cycles are CPU clocks of each machine.

## Where the time goes: ULC mono 128 kbps

Cycles per output sample (c/s), share of all CPU cycles, and cycles per instruction (CPI).

| Stage | A3000 c/s | % | CPI | A3020 c/s | % | A5000 c/s | % | CPI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| FFT (radix-2 stages) | 133.4 | 34.6 | 3.93 | 126.3 | 21.9 | 128.1 | 10.6 | 3.79 |
| DCT-IV pre/post twiddle | 60.6 | 15.7 | 4.61 | 58.7 | 10.2 | 61.4 | 5.1 | 4.69 |
| Window + overlap | 51.1 | 13.2 | 4.92 | 48.6 | 8.5 | 55.0 | 4.6 | 5.35 |
| Unpack + dequantise | 45.1 | 11.7 | 2.17 | 42.0 | 7.3 | 35.4 | 2.9 | 1.70 |
| Lapping + clip to 8-bit | 12.2 | 3.2 | 2.21 | 11.6 | 2.0 | 9.9 | 0.8 | 1.79 |
| VIDC log conversion pass | 11.3 | 2.9 | 3.22 | 10.6 | 1.8 | 9.2 | 0.8 | 2.63 |
| Sound fill (IRQ) | 2.0 | 0.5 | 4.4 | 1.8 | 0.3 | 3.2 | 0.3 | 7.2 |
| **Decode + VIDC total** | **314** | **82** | | **298** | **52** | **299** | **25** | |

The totals match the player's own CPU readout (82%, 52%, 25%).

The rest of each machine's time:

| | A3000 | A3020 | A5000 |
|---|---|---|---|
| Status text + scope | about 4% | about 4% | about 2% |
| RISC OS interrupt servicing | a few % | a few % | a few % |
| Idle | the rest | the rest | the rest |

- **Status text:** VDU and `OS_ConvertCardinal4` calls in ROM, five times a second.
- **Scope:** `dsc_loop` takes 0.7–1.2%.
- **Idle:** the main loop polling SWIs while it waits for ring space. That's harmless, but it pads the "RISC OS" row of the raw profile.

## What the hardware charges

These are per-instruction costs measured in the hot loops. For `MUL`, the cost depends on the multiplier (Rs, the third operand). The `MUL` figures come from the IMDCT spike's micro-benchmark (`spike/build/results-mode*.json`).

| | ARM2/ARM250 | ARM3 (in cache) |
|---|---:|---:|
| ALU op | 1 (2–3 at DRAM page/video DMA stalls) | 1.0 |
| `LDM` 4 words | 6.5 | 6.6 |
| `STR` | about 4 | **7.9–8.6** (write-through, no write buffer) |
| `STM` 4 words | 6.9–8.7 | **14.5** |
| taken branch | about 5 | 3 |
| `MUL`, Rs = +15-bit | 9.4 | 12.1 (8.9 in the FFT) |
| `MUL`, Rs = −15-bit | **17.5** | **19.8** (13.3 in the FFT, about half the values negative) |
| `MUL`, Rs = 7-bit | 5.5 | 7.8 |

**Screen mode** (decode benchmark, same stream):

| Machine | MODE 13 | MODE 0 | MODE 13 penalty |
|---|---|---|---|
| ARM2 | 304 c/s | 278 c/s | 9% |
| ARM3 | 296 c/s | 293 c/s | 1% |

ARM2 loses 9% because MODE 13's 8 bpp video DMA steals memory cycles. ARM3 hardly notices, because it runs from cache.

## Findings, stage by stage

### FFT (35% of an A3000, the biggest single cost)

One pass of the radix-2 inner loop (`fftn_loop`, two butterflies) costs 171 cycles on the A3000 and 165 on the A5000:

| Part | A3000 cycles | Share |
|---|---:|---:|
| 8 `MUL`/`MLA` | about 88 | 51% |
| Two Vicanek oscillator steps (9 ALU ops each) | about 30 | 18% (9 cycles on ARM3) |
| 2 × `LDM`4 and 2 × `STM`4 | about 29 | 17% (ARM3: 42, mostly the stores) |
| Butterfly add/sub, loop | about 25 | 14% |

1. **Multiplies by negative cosines cost almost twice as much.** The twiddle cosine runs from +1 to −1 across each stage, so half the `MUL r, x, cos` take the 17-cycle path. Profile: 12.3–13.5 cycles against 9.0 for the sine multiplies.
   - Fix: keep Rs non-negative. Split each stage at k = M/4 and use w(k+M/4) = −i·w(k), which costs a register swap and a sign change, not a multiply.
   - Saves about 16 cycles per pass, about **9% of the FFT**.
2. **The oscillators suit GBA and ARM3, not ARM2.** Each twiddle costs 9 instruction fetches, about 15 cycles on ARM2, against an `LDMIA tw!, {c,s}` at 5–6.
   - Twiddle tables for N=512 take 128 entries × 8 B = 1 KB. N=2048 needs 4 KB, subsampled for smaller sizes.
   - Saves about 20 cycles per pass on ARM2 (**−12%**). On ARM3 it saves about 8 cycles, but the table competes for cache, so keep the oscillators there, or test both.
3. **Radix-2 does 25% more multiplies and twice the passes of radix-4.**
   - A radix-4 butterfly does 3 complex multiplies per 4 points instead of 4, and touches every point half as often.
   - On ARM3 the second point matters as much as the first: every pass rewrites the 2 KB buffer at 3.6 cycles per word.
   - Register pressure is tight, but feasible: 8 data words plus 2–3 twiddles plus pointers in 13 registers, using `lr` as the GBA code already does.
   - **Estimated FFT after 1–3: about 70 c/s on ARM2 (from 133) and about 75 on ARM3 (from 128).** This is the item to prototype first.
4. **Optional: lower twiddle precision.** Q15 to Q12 saves about 1.5 cycles per `MUL` (Booth multiplies 2 bits per cycle). The output is 8-bit, so this is probably inaudible, but it has to be checked against ulc-codec's C decoder with `spike/check_vs_cdec.py` before adopting it.

### DCT-IV pre/post twiddle (16%) and window + overlap (13%)

Both are per-pair rotations with an oscillator plus 4 multiplies per point. The window loop (`ov_loop`) runs 8 `MUL`s at about 12.4 cycles, out of about 165 cycles per pass. Its two `STM`2 stores cost 10.3 cycles each on ARM3.

The same three remedies apply:
- **Non-negative Rs:** the window and twiddle values can all be kept in [0, 1) with signs folded into add/sub.
- **Tables on ARM2.**
- **Optionally fewer bits.**

Estimates: DCT-IV from 61 to about 40 c/s, window from 51 to about 35 c/s.

**Stretch goal:** merge the DCT-IV post-twiddle with the windowing rotation, removing one whole rotation pass (about 40 c/s more). It's awkward because of sub-block decimation (window switching), so only worth it after the rest.

### Unpack + dequantise (12%)

This is about 20 instructions per coefficient at CPI 2.1, so ALU-bound. The nybble dispatch is a chain of `SUBS`/`BVS`/`BEQ`/`CMP`/`BEQ`/`CMN`/`BEQ`.

- **Dispatch:** replacing the chain with a jump table (`LDR pc, [pc, r5, LSR #26]`, 16 entries) saves about 4 cycles per coefficient.
- **ARM3:** writing coefficients with `STM`4 bursts instead of one `STR` each (7.7 cycles) saves about another 4.
- The self-modifying dequantiser (`dc_shifter`) is fine: it isn't in the prefetch window, and it costs 1 cycle.
- Estimate: 45 to about 38 c/s on ARM2, 35 to about 27 on ARM3.

### Lapping + clip + VIDC pass (6%)

`lap_out_buf` already clips and packs 4 bytes per word. The separate `vidc_convert` pass then reloads the same bytes, looks each one up and stores again.

- **Fold the lookup into `lap_out_buf`:** one `LDRB` per sample. Write the VIDC log bytes straight into the ring and drop the 512-byte `outbuf` round trip.
  - Saves about 9 c/s on ARM2 and about 8 on ARM3.
  - It also removes one `STR` per 4 samples on ARM3.
- **Quality bonus at the same cost:** index the log table with 10–11 bits of the decoder output instead of the 8-bit truncation.
  - VIDC's log format has about 12 bits of dynamic range, and today we throw away everything below 8-bit linear before converting.
  - Quiet passages would gain the most.

### Projected ULC totals (decode + VIDC, c/s)

| | Now | After FFT + DCT-IV + window + unpack + output fusion | CPU at 20.8 kHz mono | Stereo (2× transforms) |
|---|---:|---:|---:|---:|
| ARM2 A3000 (MODE 13) | 314 | about 200 | 82% → about 52% | about 400 c/s: still too much at 20.8 kHz; fine at 12.5 kHz |
| ARM250 A3020 | 298 | about 190 | 52% → about 33% | **about 66%: stereo 20.8 kHz becomes feasible** |
| ARM3 A5000 | 299 | about 200 | 25% → about 17% | about 33% |

These are estimates built from the measured instruction costs above. The FFT figure carries the most uncertainty, so it should be prototyped first.

## ADPCM

Measured on the A3000: **48.5 c/s decode + 11.2 c/s VIDC pass = 60 c/s**, about 15% CPU mono and 30% stereo.

The decoder does the IMA arithmetic step by step for each nybble:
- 3 conditional adds
- a clamp
- two table loads (step and index)
- a byte store

That's 22 instructions at CPI 2.1.

A table-driven decoder needs about 12 instructions per sample:
- **One table word per (index, nybble):** precompute the signed `diff` and the next index's table offset into a single word, 89 × 16 × 4 B = 5.7 KB. One `LDR` then gives both.
- **Add and clamp:** add to the predictor, then clamp with the existing 3-instruction `TEQ`/`EORNE` trick.
- **Direct VIDC output:** look up the VIDC byte straight from the 16-bit predictor (2–4 KB log table) and pack 4 per `STR`. This drops the separate pass and gives better low-level resolution than the 8-bit truncation.

**Estimate: about 18–20 c/s in total, about 3× faster.** A3000 mono would drop to about 5% CPU and stereo to about 10%. This is the cheapest certain win in this document.

## Sound fill, DMA and QTM

| | Ours | QTM |
|---|---|---|
| Fill routine | `LDM`/`STM` 4-word copy from a ring of ready VIDC bytes | Per-sample `LDRB`/`STRB` mixing with volume tables (`a2_fullvol_loop` and friends) |
| Cost | 1.8–2.0 c/s on ARM2/ARM250, 3.2 on ARM3, 0.3–0.5% of CPU | An order of magnitude more |
| DMA buffer | 256 B (one channel), 81 interrupts/s | 208 B per channel (`release/qtm_offsets.h.asm`), similar rate |
| ARM2/ARM3 handling | One routine for both | Unrolls fills at runtime for ARM2 (`unroll_code`), keeps compact `a3_*` loops for ARM3's cache |

Our handler follows QTM's install, twang, read-back and restore sequence.

Remaining micro-gains:
- **8-register bursts** instead of 4, and one ring-wrap check per call instead of per 16 bytes: about 2.0 → 1.2 c/s, **about 0.2%**.
- **Bigger DMA buffer** (512 B, 40 interrupts/s): about −0.3% interrupt overhead.
- **Zero-copy:** pointing MEMC's sound DMA straight at the ring would mean taking over the sound interrupt from RISC OS. Not worth it for under 1%.

**Verdict: the fill is not a bottleneck, and needs no change.** The ARM2-unroll/ARM3-compact split QTM uses matters for QTM because its fills are per-sample loops. Ours is a burst copy, small enough for ARM3's cache either way.

## Display and UI

- **Status text:** about 3–4% of an A3000 goes on updating four numbers five times a second, through VDU and `OS_ConvertCardinal4` in ROM. Update once a second, and only changed digits, for about 3% back.
- **Scope:** 0.7–1.2%. Halve it with every other frame or 160 columns, or drop it while CPU is above 70%.
- **Screen mode:**
  - MODE 9 (4 bpp, 40 KB) halves the 9% video DMA penalty on ARM2/ARM250 and keeps colour.
  - MODE 0/4 (1 bpp) recovers about 8%.
  - Changing mode means redrawing the scope at 4 bpp.

## ARM2 unrolling and ARM3 cache footprint

- **Code size is not the ARM3 problem.**
  - The decoder is 1,840 B and the transforms 1,360 B including twiddle data.
  - The hottest loops (`dc_loop`, `fftn_loop`, `ov_loop`) are 100–250 B each.
  - The data working set per block is a 2 KB transform buffer, 1 KB lapping buffer and the output.
  - Unified 4 KB cache: each stage's code plus data fits, and in-cache ALU ops measure 1.0 CPI.
- **ARM3's real costs are multiplies (unchanged from ARM2) and stores.** Write-through with no write buffer: `STR` costs 7.9–8.6 cycles and `STM`4 14.5.
  - So for ARM3, the priorities are fewer passes over the buffers (radix-4, output fusion) and burst stores, not smaller code.
  - Keep the oscillators on ARM3: 9 single-cycle ALU ops beat a cache-resident table load only slightly, and they cost no data cache.
- **Unrolling on ARM2 is a small win.**
  - Loop overhead (`ADDS` + taken `BCC`) is about 8–9 cycles out of 165–171 per FFT or window pass (about 5%).
  - Unrolling ×2 recovers about half of that.
  - It's worth doing in the rewritten loops, but it's not where the time is.
  - If unrolling grows a hot loop past about 1 KB, build the ARM3 variant without it, as QTM does.

## Recommended order

| # | Change | Effort | Estimated gain | Status |
|---|---|---|---|---|
| 1 | ADPCM table decoder + direct VIDC output | small | ADPCM about 3× faster (A3000: 15% → about 5%) | done: 60 → 21.8 c/s, 6% |
| 2 | Fuse ULC output stage: VIDC lookup in `lap_out_buf`, write ring directly; optionally a 10–11-bit log index | small | −3% ARM2; better quiet passages | done, 11-bit: −10 c/s |
| 3 | Status text 1 Hz, cheaper scope; optional MODE 9 | small | −3 to −8% ARM2/ARM250 | done, MODE 9: about −5% |
| 4 | FFT: non-negative Rs, table twiddles (ARM2 build), then radix-4 | large | FFT about −45%; the big one. Prototype first, measure with `profile_player.py`, check with `check_vs_cdec.py` | done, one build for all: −36% |
| 5 | DCT-IV + window: same treatment | medium | about −35 c/s | done: −17 c/s |
| 6 | Unpack jump-table dispatch, `STM` bursts | medium | about −7 c/s ARM2, about −8 ARM3 | table dispatch done: −10 c/s; no `STM` bursts |
| 7 | (Stretch) merge DCT-IV post-twiddle with window rotation | large | about −40 c/s | dropped, see Implementation results |

Every change must keep `spike/check_vs_cdec.py` passing against ulc-codec's own decoder, not just against `RefDecoder`. That gap is how the flags-after-`BL` bug slipped through.

## Tools added

- `player/profile_player.py FILE [--config ...] [--top N] [--rom] [--json out]`: stage and label cycle profile, CPI, hottest instructions.
- `player/symmap.py`: true label addresses for `-opt-adr` builds.
- `spike/check_vs_cdec.py`: compares the ARM decoder with ulc-codec's C decoder.
- `player/dump_ring.py`: dumps and analyses the player's output ring.
- `player/emu_cost.py`: host cost of emulating a given app.
