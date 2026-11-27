# ulc-player: start here

Real-time audio player for the Acorn Archimedes (ARM2/ARM250/ARM3, RISC OS 3.1x): decodes ulc-codec (MDCT) and IMA ADPCM live on the ARM. A tool/library plus two demo apps (!ULCPlayer, !ADPCMTest) and write-ups.

| | |
|---|---|
| GitHub | https://github.com/kieranhj/ulc-player (public) |
| Upstream | Decoder ported from Aikku93's ulc-codec / ulcplayer-gba (credited in README) |
| Local path | D:\archie\mine\ulc-player (moved from OneDrive\Archie\Projects\ulc-player in the 2026-11 tidy) |
| Branch | main (only branch) |
| Last commit | 2026-11-14: README: ADPCM section (trellis, predictor modes, sizes, quality, CPU), !ADPCMTest app |

## What's in here
- `player\`: the RISC OS player (`player.asm`), `make_app.py` (builds the apps), ADPCM encoder, emulator tests, profiler. See player\README.md.
- `spike\`: the decoder (`decoder.asm`, `fourier.asm`), benchmark harness (`run_bench.py`), checks against ulc-codec's C decoder.
- `tools\`: `adpcm_trellis.c` (built with `zig cc`), `spark.py` (writes !Spark archives).
- `music\`: test tracks as ULC and ADPCM (music not covered by the Unlicense).
- Write-ups: FEASIBILITY.md, PERF.md, CODECS.md.

## Build and run
- `make`: `py -3 player\make_app.py` -> `player\build\app\!ULCPlayer` and `player\build\ulcplayer,ddc`. Works (2026-11-22).
- `make adpcm-test`: `!ADPCMTest` -> `player\build\adpcmtest,ddc`. `make help` lists targets.
- Tools: Python 3 with numpy; vasm at `D:\tools\archie\vasm_1.9\vasmarm_std.exe` (or `VASM`); headless Arculator + its MCP client at `D:\archie\mine\arculator` (or `ARCULATOR_ROOT`), needed even to import `run_player`; `zig` for the trellis encoder.
- Emulator tests are run by hand: `python player\run_player.py --ulc ... --config a3020-riscos311`, `python player\test_app.py`, `python spike\run_bench.py` (these boot arculator-headless).
- Published WIP: arc-ulcplayer-wip,ddc and arc-ulcplayer-adpcmtest-wip,ddc on bitshifters.github.io/content/wip (links in README).

## Local changes
- 2026-11-22: `spike\run_bench.py` default `ARC_ROOT`/`VASM` changed from the old OneDrive paths to the D: paths above (uncommitted).
- No snapshot branches.

## Status and next steps
- Active as of 2026-11-14. Latest work: trellis ADPCM, 3/2-bit ADPCM, per-block predictor modes, subband ADPCM prototype.
- CODECS.md ends with proposals for codecs suited to ARM2; no separate PLAN/TODO file.

## See also
- README.md, player\README.md, spike\README.md, CODECS.md, PERF.md, FEASIBILITY.md.
- D:\archie\mine\arculator (the headless emulator used by the tests).
