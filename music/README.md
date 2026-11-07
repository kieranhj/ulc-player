# Test music (20833 Hz)

Full-length versions of tracks from `OneDrive/Trackers/Rhino`, resampled to 20833 Hz (a 48 µs VIDC period).
ULC files use N=512 and come from the real `ulcencodetool`; `.adp` files are IMA ADPCM from `player/adpcm_encode.py`.
The 20833 Hz `.wav` sources they were encoded from are not in the repository (too large).

Play: `*WimpSlot -min <size + 100>K` then `*player <file>` (filetype Data).

| Track | Length | ADPCM mono | ULC 64k | ULC 48k | ULC 32k | ADPCM stereo | ULC 128k | ULC 96k | ULC 64k |
|---|---|---|---|---|---|---|---|---|---|
| ArchieShaderToy demo B | 32.6 s | 335 KB | 254 KB | 190 KB | 126 KB | 670 KB | 510 KB | 381 KB | 254 KB |
| ArchieShaderToy demo C | 50.8 s | 521 KB | 395 KB | 296 KB | 197 KB | 1,042 KB | 793 KB | 593 KB | 394 KB |
| ArchieShaderToy demo D | 39.0 s | 400 KB | 303 KB | 227 KB | 151 KB | 799 KB | 608 KB | 455 KB | 303 KB |
| Archie Particle demo (Ableton ALPHA) | 85.8 s | 880 KB | 666 KB | 499 KB | 332 KB | 1,760 KB | 1,338 KB | 1,000 KB | 666 KB |

ADPCM is 83 kbps mono / 167 kbps stereo at this rate.
On an A3000, ADPCM uses 16% CPU (mono) and ULC 78% (mono only). See `../FEASIBILITY.md` §9–§10.
Demo B is mastered hot: it clips on ~0.25% of samples in the decoder's 8-bit output.
