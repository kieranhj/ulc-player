"""True label addresses for a vasm -opt-adr build.

With -opt-adr, vasm's listing symbol table (and the addresses printed on later
lines) can be stale by a few words: an ADR that needed expanding moves
everything after it. The listing still prints each instruction's encoding, so
we walk the listing and the binary together and re-derive every label's real
address from where its next instruction actually landed.

    syms = symmap(listing_path, image_bytes, origin=0x8000)   # {label: addr}
"""
import re
import struct

LINE = re.compile(r"^\d\d:([0-9A-F]{8}) ([0-9A-F]+)\s")
LABEL = re.compile(r"^\s+\d+:\s+([A-Za-z_.][\w.]*):")


def symmap(listing_path, image, origin=0x8000):
    lines = open(listing_path, encoding="latin-1").read().splitlines()
    words = len(image)
    syms = {}
    pending = []          # labels waiting for their first instruction
    delta = 0             # real address - listed address
    for ln in lines:
        m = LABEL.match(ln)
        if m and not LINE.match(ln):
            pending.append(m.group(1))
            continue
        m = LINE.match(ln)
        if not m:
            continue
        addr, hexb = int(m.group(1), 16), m.group(2)
        if len(hexb) < 8:
            continue
        want = bytes.fromhex(hexb[:8])
        guess = addr + delta - origin
        found = None
        for d in range(0, 64, 4):                # the image is only ever *ahead*
            for g in (guess + d, guess - d):
                if 0 <= g <= words - 4 and image[g:g + 4] == want:
                    found = g
                    break
            if found is not None:
                break
        if found is None:
            continue                            # data that changed (e.g. patched) - keep delta
        delta = found + origin - addr
        for name in pending:
            syms[name] = found + origin
        pending = []
    return syms


if __name__ == "__main__":
    import sys
    s = symmap(sys.argv[1], open(sys.argv[2], "rb").read())
    for k, v in sorted(s.items(), key=lambda kv: kv[1]):
        print(f"{v:08X} {k}")
