"""Pack a RISC OS application directory into a !Spark archive (filetype &DDC).

    .venv/Scripts/python tools/spark.py <app directory> <archive>
    .venv/Scripts/python tools/spark.py --list <archive>

The owner publishes the port as a `,ddc` archive for !Spark on a real machine and for
Archimedes Live. This writes the **Spark format** -- what !Spark itself writes -- and NOT the
ArcFS format this tool used to write: nspark and the emulator read ArcFS, but !Spark on a real
machine does not open it (the owner, 2026-09-27, with arc-nemesis-wip,ddc).

The Spark format is ARC's with RISC OS's attributes added (nspark `io.c` read_header). Each
entry is a 41-byte header and its data:

    0   $1A
    1   the method, bit 7 set for a RISC OS entry: $82 stored, $FF compressed; $80 ends a list
    2   the name, 13 bytes, NUL padded
    15  the length of the data here
    19  the date and time, MS-DOS style (two halfwords), from the RISC OS stamp
    23  the CRC-16 of the original bytes
    25  the original length
    29  the load and exec addresses, then the attributes

A directory is an entry of type &DDC whose data is a nested list of entries, itself ended by
$1A $80, as is the archive.

**Measured against an archive !Spark made of the same !Nemesis** (hostfs `nemesis,ddc`):
with that archive's own time stamps, this writes it BYTE FOR BYTE. That took four things
!Spark does that nothing documents, all found by comparing the two:

- a directory's CRC field is always $600B, and its attributes $33 (a file's are 3);
- the compression is 12-bit LZW, UNIX compress's packing, with a maxbits byte in front
  (nspark `compress.c`) -- but it clears the table its own way: an entry beyond the last
  code (4,096) is still made, and at the NEXT input byte the pending code goes out and then
  the clear, with no ratio test; the code width never grows past 12 bits for it;
- the group buffer is never zeroed, so a group flushed part full carries the tail of the
  group before it (the decoder never reads those bits);
- a file that does not get smaller is stored ($82), and still carries its original length.

**Filetypes come from the `,xxx` suffix** HostFS and the Filer use, so `!RunImage,ff8` goes in
as `!RunImage` of type &FF8 (Absolute), `!Run,feb` as Obey, `!Help,fff` as Text. A file
without one is stored as data (&FFD). Time stamps are the files' modification times, in UTC,
as HostFS reports them to RISC OS.
"""

import datetime
import os
import struct
import sys

HEADER = 41
MARK, END = 0x1A, 0x80
STORED, COMPRESSED = 0x82, 0xFF
DIR_TYPE = 0xDDC
DIR_CRC, DIR_ATTR, FILE_ATTR = 0x600B, 0x33, 0x03
MAXBITS = 12
RISCOS_EPOCH = datetime.datetime(1900, 1, 1)


def crc16(data):
    """The CRC ARC and Spark use (nspark crc.c: reflected, polynomial $A001, from zero)."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


def compress(data, maxbits=MAXBITS):
    """!Spark's compression, method $FF: see the module docstring for where it is not UNIX's."""
    init_bits, clear, first = 9, 256, 257
    maxmaxcode = 1 << maxbits
    out = bytearray([maxbits])
    group = bytearray(maxbits + 1)          # never cleared: !Spark's padding is its old bytes
    n_bits, maxcode, offset, free_ent, clear_flg = init_bits, (1 << init_bits) - 1, 0, first, False

    def output(code):
        nonlocal n_bits, maxcode, offset, clear_flg
        if code < 0:                        # the end: what is left of the group
            if offset:
                out.extend(group[:(offset + 7) >> 3])
            return
        r, i = offset & 7, offset >> 3
        group[i] = (group[i] & ((1 << r) - 1)) | ((code << r) & 0xFF)
        v, bits, i = code >> (8 - r), n_bits - (8 - r), i + 1
        while bits > 0:
            group[i] = v & 0xFF
            v, bits, i = v >> 8, bits - 8, i + 1
        offset += n_bits
        if offset == n_bits << 3:
            out.extend(group[:n_bits])
            offset = 0
        if (free_ent > maxcode and n_bits < maxbits) or clear_flg:
            if offset:
                out.extend(group[:n_bits])
                offset = 0
            if clear_flg:
                n_bits, maxcode, clear_flg = init_bits, (1 << init_bits) - 1, False
            else:
                n_bits += 1
                maxcode = maxmaxcode if n_bits == maxbits else (1 << n_bits) - 1

    if not data:
        output(-1)
        return bytes(out)
    table = {}
    ent = data[0]
    for c in data[1:]:
        if free_ent > maxmaxcode:           # full last time: the pending code, then clear
            output(ent)
            ent = c
            table.clear()
            free_ent, clear_flg = first, True
            output(clear)
            continue
        key = (ent, c)
        if key in table:
            ent = table[key]
            continue
        output(ent)
        ent = c
        if free_ent <= maxmaxcode:          # one entry past the last code, never used
            table[key] = free_ent
            free_ent += 1
    output(ent)
    output(-1)
    return bytes(out)


def uncompress(body):
    """nspark's decoder, for checking what compress() wrote."""
    maxbits, data = body[0], body[1:]
    maxmaxcode = 1 << maxbits
    n_bits, maxcode, free_ent, clear_flg = 9, 511, 257, False
    pos = offset = size = 0
    buf = b""
    prefix, suffix = {}, {}

    def getcode():
        nonlocal n_bits, maxcode, clear_flg, pos, offset, size, buf
        if clear_flg or offset >= size or free_ent > maxcode:
            if free_ent > maxcode:
                n_bits += 1
                maxcode = maxmaxcode if n_bits == maxbits else (1 << n_bits) - 1
            if clear_flg:
                n_bits, maxcode, clear_flg = 9, 511, False
            if pos >= len(data):
                return -1
            buf = data[pos:pos + n_bits]
            pos += len(buf)
            offset, size = 0, (len(buf) << 3) - (n_bits - 1)
        code = (int.from_bytes(buf, "little") >> offset) & ((1 << n_bits) - 1)
        offset += n_bits
        return code

    def expand(code):
        s = bytearray()
        while code >= 256:
            s.append(suffix[code])
            code = prefix[code]
        s.append(code)
        return s[::-1]

    out = bytearray()
    old = getcode()
    if old < 0:
        return bytes(out)
    out.append(old)
    fin = old
    while (code := getcode()) >= 0:
        if code == 256:
            clear_flg, free_ent = True, 256
            if (code := getcode()) < 0:
                break
        s = expand(old) + bytes([fin]) if code >= free_ent else expand(code)
        fin = s[0]
        out += s
        if free_ent < maxmaxcode:
            prefix[free_ent], suffix[free_ent] = old, fin
            free_ent += 1
        old = code
    return bytes(out)


def stamp(seconds):
    """(RISC OS centiseconds since 1900, MS-DOS date, MS-DOS time) for a Unix time, in UTC."""
    when = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=seconds)
    cs = int((when - RISCOS_EPOCH).total_seconds() * 100)
    date = ((when.year - 1980) << 9) | (when.month << 5) | when.day
    time = (when.hour << 11) | (when.minute << 5) | (when.second // 2)
    return cs, date, time


def riscos_name(name):
    """(the name as RISC OS sees it, its filetype) from a `,xxx` suffix."""
    if len(name) > 5 and name[-4] == "," and all(c in "0123456789abcdefABCDEF" for c in name[-3:]):
        return name[:-4], int(name[-3:], 16)
    return name, 0xFFD


def header(method, name, data_len, date, time, crc, orig_len, filetype, cs, attr):
    load = 0xFFF00000 | (filetype << 8) | ((cs >> 32) & 0xFF)
    return struct.pack("<BB13sIHHHIIII", MARK, method, name.encode("latin-1"), data_len, date,
                       time, crc, orig_len, load, cs & 0xFFFFFFFF, attr)


def entries(path, stamps=None):
    """The archive's bytes for everything in `path`, the Filer's order, ended by $1A $80.
    `stamps` maps a RISC OS name to (centiseconds, date, time), in place of the file's own."""
    out = bytearray()
    for entry in sorted(os.listdir(path), key=str.lower):
        full = os.path.join(path, entry)
        if os.path.isdir(full):
            cs, date, time = (stamps or {}).get(entry) or stamp(os.path.getmtime(full))
            body = entries(full, stamps)
            out += header(STORED, entry, len(body), date, time, DIR_CRC, len(body), DIR_TYPE,
                          cs, DIR_ATTR) + body
            continue
        name, filetype = riscos_name(entry)
        cs, date, time = (stamps or {}).get(name) or stamp(os.path.getmtime(full))
        with open(full, "rb") as f:
            raw = f.read()
        packed = compress(raw)
        assert uncompress(packed) == raw, f"{name}: compress does not round-trip"
        method, body = (COMPRESSED, packed) if len(packed) < len(raw) else (STORED, raw)
        out += header(method, name, len(body), date, time, crc16(raw), len(raw), filetype, cs,
                      FILE_ATTR) + body
    return out + bytes([MARK, END])


def pack(app, out, stamps=None):
    """`app` (a directory) into `out`, the directory itself the archive's one top-level entry."""
    app = app.rstrip("\\/")
    name = os.path.basename(app)
    cs, date, time = (stamps or {}).get(name) or stamp(os.path.getmtime(app))
    body = entries(app, stamps)
    archive = header(STORED, name, len(body), date, time, DIR_CRC, len(body), DIR_TYPE, cs,
                     DIR_ATTR) + body + bytes([MARK, END])
    with open(out, "wb") as f:
        f.write(archive)
    return archive


def listing(data, at=0, path=""):
    """What an unpacker sees, read back and checked: (path, kind, method, filetype, cs,
    date, time, bytes) for each entry."""
    out = []
    while True:
        assert data[at] == MARK, f"no $1A at {at:#x}"
        method = data[at + 1]
        if method & 0x7F == 0:
            return out, at + 2
        name = data[at + 2:at + 15].split(b"\0")[0].decode("latin-1")
        length, date, time, crc, orig, load, exec_, attr = struct.unpack_from(
            "<IHHHIIII", data, at + 15)
        body = data[at + HEADER:at + HEADER + length]
        filetype, cs = (load >> 8) & 0xFFF, ((load & 0xFF) << 32) | exec_
        if filetype == DIR_TYPE and method == STORED and crc == DIR_CRC:
            inner, end = listing(data, at + HEADER, path + name + "/")
            assert end == at + HEADER + length, f"{name}: its list ends at {end:#x}"
            out.append((path + name, "dir", method, filetype, cs, date, time, None))
            out += inner
        else:
            raw = uncompress(body) if method == COMPRESSED else body
            assert len(raw) == orig, f"{name}: {len(raw)} bytes of {orig}"
            assert crc16(raw) == crc, f"{name}: CRC"
            out.append((path + name, "file", method, filetype, cs, date, time, raw))
        at += HEADER + length


def show(data):
    entries_, end = listing(data)
    assert end == len(data), f"{len(data) - end} bytes after the end"
    for path, kind, method, filetype, cs, date, time, raw in entries_:
        if kind == "dir":
            print(f"  {path + '/':24} directory")
        else:
            how = "compressed" if method == COMPRESSED else "stored"
            print(f"  {path:24} type {filetype:03X}  {len(raw):>9,} bytes, {how}")
    return entries_


def main(argv):
    if argv[:1] == ["--list"]:
        with open(argv[1], "rb") as f:
            show(f.read())
        return 0
    if len(argv) != 2:
        raise SystemExit(__doc__)
    app, out = argv
    archive = pack(app, out)
    print(f"{out}: {len(archive):,} bytes")
    show(archive)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
