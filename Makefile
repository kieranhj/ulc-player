# Wrapper so that `make` works from PowerShell / Windows Terminal.
# GNU make runs recipes with cmd.exe here (no sh on PATH).
# Needs Python 3 with numpy; vasm and Arculator paths default to the D: layout
# (override with the VASM and ARCULATOR_ROOT environment variables).
.PHONY: all build adpcm-test help
all: build

build:
	py -3 player\make_app.py

adpcm-test:
	py -3 player\make_app.py --adpcm-test

help:
	@echo make            - build !ULCPlayer: player\build\app and player\build\ulcplayer,ddc
	@echo make adpcm-test - build !ADPCMTest: player\build\adpcmtest,ddc
	@echo make help       - this list
	@echo Emulator tests (run_player.py, test_app.py, spike\run_bench.py) are run by hand: see README.md
