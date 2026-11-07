"""Cycle profile of the player while it plays a stream.

Boots a machine, plays the file with the command-line player, lets it reach a
steady state, then runs Arculator's per-instruction cycle profiler over the
whole player image for --seconds of emulated time. Cycles are attributed to the
nearest preceding label (true addresses from symmap.py) and grouped into stages.

Time spent in RISC OS (SWIs, the IRQ dispatcher) is outside the profiled range;
it shows up as the gap between "total CPU cycles" and "profiled".

    python player/profile_player.py FILE [--config a3020-riscos311] [--seconds 4] [--json out.json]
"""
import argparse
import bisect
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "spike"))
sys.path.insert(0, HERE)
import run_bench as rb                                  # noqa: E402
import run_player as rp                                 # noqa: E402
from symmap import symmap                               # noqa: E402

# Stage of each label (first match wins; labels not listed inherit the stage of
# the label before them in the image).
STAGES = [
    ("ULC: unpack + dequantise", ["ulc_DecodeBlock", "hdr_done", "chan_loop", "subblock_loop", "dc_"]),
    ("ULC: IMDCT pre/post twiddle (DCT-IV)", ["Fourier_DCT4_InPlace", "dct4_"]),
    ("ULC: FFT", ["Fourier_FFT_InPlace", "fft"]),
    ("ULC: IMDCT pre/post twiddle (DCT-IV)", ["Fourier_DCT4_InPlace", "dct4_"]),
    ("ULC: window + overlap", ["swap_loop", "ov_", "skip_overlap", "rev_loop"]),
    ("ULC: lapping + VIDC lookup", ["lap_"]),
    ("ULC: M/S stereo", ["ms_"]),
    ("ADPCM decode", ["adpcm_decode_block", "ad_", "ima_"]),
    ("tables", ["vidc_table", "scope_table", "vidc11", "vidc12", "lin11_8", "fft_tw"]),
    ("VIDC log conversion", ["vidc_convert", "vc_"]),
    ("Sound fill (IRQ)", ["fill_", "channel_handler", "overrun_code"]),
    ("Main loop + UI", ["main", "menu", "read_input", "ri_", "idle", "no_cpu", "draw_", "dsc_", "dm_",
                        "print_", "udiv", "ring_free", "decode_one", "d1_", "play_", "prefill",
                        "tap_", "strcpy", "set_mouse_box", "setup_screen", "ssc_", "err_", "quit",
                        "sound_", "ss_", "sst_", "reset_decoder", "rd_"]),
]


def stage_of(label):
    for name, prefixes in STAGES:
        for p in prefixes:
            if label == p or label.startswith(p):
                return name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--config", default="a3020-riscos311")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=0, help="also print the N hottest instructions")
    ap.add_argument("--rom", action="store_true", help="profile the 2 MB ROM instead of the player")
    a = ap.parse_args()

    image = rp.build()
    syms = symmap(os.path.join(rp.BUILD, "player.lst"), image)
    labels = sorted((v, k) for k, v in syms.items())
    addrs = [v for v, _ in labels]
    info = struct.unpack_from("<6I", image, 4)
    track = open(a.file, "rb").read()

    pool = rb.MachinePool(os.path.join(rb.ARC_ROOT, "arculator-headless.exe"),
                          rb.ARC_ROOT, os.path.join(rp.BUILD, "sandbox"))
    try:
        local = os.path.join(HERE, "..", "spike", "configs", a.config + ".cfg")
        mid, m = (pool.create(config_file=local) if os.path.exists(local)
                  else pool.create(config=a.config))
        open(os.path.join(m.hostfs, "player,ff8"), "wb").write(image)
        open(os.path.join(m.hostfs, "track,ffd"), "wb").write(track)
        for _ in range(12):
            m.command_json("run_us 1000000")
        rb.tap(m, rb.keymap.SCANCODES["f12"])
        m.command_json("run_us 500000")
        slot = (len(image) + 16384 + 4096 + 32768 + len(track)) // 1024 + 64
        rb.type_text(m, f"WimpSlot -min {slot}K")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        m.command_json("run_us 300000")
        rb.type_text(m, "HostFS::HostFS.$.player HostFS::HostFS.$.track")
        rb.tap(m, rb.keymap.SCANCODES["return"])
        for _ in range(int(a.settle * 10)):
            m.command_json("run_us 100000")

        def rd(addr):
            return struct.unpack("<I", m.command(f"read {addr:x} 4"))[0]
        blocks0, under0 = rd(info[2]), rd(info[0])
        c0 = m.command_json("regs")["cycles"]
        if a.rom:
            m.command_text("profile start 3800000 200000")
        else:
            m.command_text(f"profile start 8000 {len(image) & ~3:x}")
        for _ in range(int(a.seconds * 10)):
            m.command_json("run_us 100000")
        prof = json.loads(m.command_text("profile read 4096"))
        m.command_text("profile stop")
        c1 = m.command_json("regs")["cycles"]
        blocks, under, cpu = rd(info[2]) - blocks0, rd(info[0]) - under0, rd(info[1])
    finally:
        pool.destroy_all()

    total = c1 - c0
    if a.rom:
        tot_rom = prof["total_cycles"]
        print(f"ROM: {tot_rom} of {total} cycles ({tot_rom / total * 100:.1f}%), "
              f"{prof['total_instructions']} instructions")
        # group hot spots into 256-byte routines, report hits/s of the hottest word in each
        groups = {}
        for e in prof["entries"]:
            g = groups.setdefault(e["addr"] & ~0xFF, [0, 0])
            g[0] += e["cycles"]
            g[1] = max(g[1], e["count"])
        for addr, (cyc, mx) in sorted(groups.items(), key=lambda kv: -kv[1][0])[:a.top or 20]:
            print(f"  {addr:08X}  {cyc / total * 100:5.2f}%  max {mx / a.seconds:8.0f} hits/s")
        return
    per_label, per_stage, label_count, stage_count = {}, {}, {}, {}
    hot = []
    for e in prof["entries"]:
        i = bisect.bisect_right(addrs, e["addr"]) - 1
        lab = labels[i][1] if i >= 0 else "?"
        per_label[lab] = per_label.get(lab, 0) + e["cycles"]
        label_count[lab] = label_count.get(lab, 0) + e["count"]
        hot.append((e["cycles"], e["addr"], lab, e["count"]))
    # stage: walk labels in address order so unknown labels inherit
    cur = "other"
    stage_for_label = {}
    for _, lab in labels:
        s = stage_of(lab)
        cur = s or cur
        stage_for_label[lab] = cur
    for lab, cyc in per_label.items():
        st = stage_for_label.get(lab, "other")
        per_stage[st] = per_stage.get(st, 0) + cyc
        stage_count[st] = stage_count.get(st, 0) + label_count[lab]
    profiled = sum(per_label.values())

    hdr = struct.unpack_from("<IHH", track)
    print(f"{os.path.basename(a.file)} on {a.config}: {a.seconds}s, {total} CPU cycles, "
          f"{blocks} blocks decoded, underruns {under}, player's CPU% {cpu}")
    samples = blocks * (hdr[1] if hdr[0] == 0x32434C55 else struct.unpack_from('<I', track, 16)[0])
    out = {"file": a.file, "config": a.config, "seconds": a.seconds, "total_cycles": total,
           "blocks": blocks, "samples_per_channel": samples, "underruns": under, "stages": {}, "labels": {}}
    for st, cyc in sorted(per_stage.items(), key=lambda kv: -kv[1]):
        cps = cyc / samples if samples else 0
        cpi = cyc / max(stage_count[st], 1)
        print(f"  {st:40s} {cyc:>11d} cyc  {cyc / total * 100:5.1f}%  {cps:6.1f} cyc/sample  CPI {cpi:4.2f}")
        out["stages"][st] = {"cycles": cyc, "instructions": stage_count[st], "pct": round(cyc / total * 100, 2),
                             "cyc_per_sample": round(cps, 2), "cpi": round(cpi, 3)}
    rest = total - profiled
    print(f"  {'RISC OS (SWIs, IRQ dispatch) + unprofiled':40s} {rest:>11d} cyc  {rest / total * 100:5.1f}%")
    out["os_and_other"] = rest
    for lab, cyc in sorted(per_label.items(), key=lambda kv: -kv[1])[:60]:
        out["labels"][lab] = {"cycles": cyc, "instructions": label_count[lab]}
    if a.top:
        print("  hottest instructions:")
        for cyc, addr, lab, cnt in sorted(hot, reverse=True)[:a.top]:
            print(f"    {addr:08X} {lab:24s} {cyc:>10d} cyc {cnt:>9d} hits {cyc / max(cnt, 1):5.1f} cyc/hit")
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
