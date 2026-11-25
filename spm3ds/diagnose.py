"""Beginner-friendly diagnostic report for a main.dol file.

    python3 -m spm3ds.diagnose path/to/main.dol [--symbols FILE] [--out report.md]

The report is Markdown and is meant to be shared when you ask for help. It contains file
addresses, sizes, counts and a SHA-1 fingerprint. It does NOT contain game bytes, instruction
words, strings or data. Do not share the .dol itself.
"""
import argparse
import hashlib
import os
import sys

from .analysis import analyze, sweep
from .dol import DolError, parse_dol
from .hle import external_from_symbols, parse_symbols
from .jumpdetect import detect as detect_jump_tables
from .recomp import auto_jump_tables

KIB = 1024
MIB = 1024 * KIB
PAGE = 64 * KIB
WII_MEM1 = 24 * MIB
WII_MEM2 = 64 * MIB
# New 3DS application memory is NOT fixed. Verified sources give FCRAM 256 MiB with APPLICATION,
# SYSTEM and BASE regions, and the size depends on configuration. The report prints this as a
# check to run on the device, not as a number.
NEW3DS_NOTE = ("The New 3DS application region size is set by the system configuration. Query it on "
               "the device with osGetMemRegionSize(MEMREGION_APPLICATION) before deciding a budget.")


def _mib(n):
    return f"{n / MIB:.2f} MiB"


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_report(dol_path, symbols_path=None):
    size = os.path.getsize(dol_path)
    with open(dol_path, "rb") as f:
        dol = parse_dol(f.read())
    names, seeds = {}, []
    if symbols_path:
        with open(symbols_path, encoding="utf-8") as f:
            text = f.read()
        names = external_from_symbols(text)[0]
        seeds = [a for a, _ in parse_symbols(text)]
    image = dol.text_words()
    auto, notes = auto_jump_tables(dol)
    result = analyze(image, dol.entry, frozenset(names), seeds, auto)
    failures = sweep(image)

    out = []
    out.append("# spm3ds diagnostic report")
    out.append("")
    out.append("Share this report freely. It has no game bytes. Do not share the .dol file itself.")
    out.append("")
    out.append("## File")
    out.append(f"- File name: `{os.path.basename(dol_path)}`")
    out.append(f"- Size: {size:,} bytes")
    out.append(f"- SHA-1: `{_sha1(dol_path)}`")
    out.append("")
    out.append("## DOL header")
    out.append(f"- Entry point: `{dol.entry:#010x}`")
    out.append("")
    out.append("| Kind | # | Load address | Size |")
    out.append("|---|---|---|---|")
    for s in dol.text:
        out.append(f"| text | {s.index} | `{s.address:#010x}` | {s.size:,} bytes |")
    for s in dol.data:
        out.append(f"| data | {s.index} | `{s.address:#010x}` | {s.size:,} bytes |")
    out.append(f"| bss | - | `{dol.bss_address:#010x}` | {dol.bss_size:,} bytes |")
    out.append("")

    out.append("## Analysis")
    out.append(f"- Functions reachable from the entry point: {len(result.entries)}")
    out.append(f"- Instructions reached: {result.instruction_count()}")
    out.append(f"- Symbols bound (from --symbols): {len(names)}")
    out.append("")
    out.append("### Blocking problems")
    fatal = result.fatal
    if not fatal:
        out.append("None.")
    for p in fatal:
        out.append(f"- `{p.addr:#010x}` [{p.kind}] {p.message.split(': ', 1)[-1]}")
    out.append("")
    out.append("### Unsupported words in the text sections (addresses and opcode numbers only)")
    out.append(f"{len(failures)} word(s) do not decode.")
    by_opcode = {}
    for addr, word, _ in failures:
        by_opcode.setdefault(word >> 26, []).append(addr)
    for opcode, addrs in sorted(by_opcode.items(), key=lambda kv: -len(kv[1])):
        shown = ", ".join(f"`{a:#010x}`" for a in addrs[:8])
        more = f" and {len(addrs) - 8} more" if len(addrs) > 8 else ""
        out.append(f"- primary opcode {opcode}: {len(addrs)} word(s): {shown}{more}")
    out.append("")

    out.append("## Jump tables")
    if auto:
        for bctr, targets in sorted(auto.items()):
            out.append(f"- Detected at `{bctr:#010x}` with {len(targets)} entries")
    else:
        out.append("No switch tables detected automatically.")
    for n in notes:
        if n.startswith("    -"):
            out.append(f"  {n.strip()}")
    out.append("")

    out.append("## Instruction mix (supported instructions reached)")
    out.append("| Mnemonic | Count |")
    out.append("|---|---|")
    for name, count in result.histogram().most_common(40):
        out.append(f"| {name} | {count} |")
    out.append("")

    out.append("## Memory footprint estimate")
    loaded = sum(s.size for s in dol.text) + sum(s.size for s in dol.data)
    total = loaded + dol.bss_size
    pages = sum(-(-(s.size) // PAGE) for s in dol.text + dol.data) + -(-dol.bss_size // PAGE)
    out.append(f"- Loaded sections (text + data): {_mib(loaded)}")
    out.append(f"- bss: {_mib(dol.bss_size)}")
    out.append(f"- Total declared: {_mib(total)}")
    out.append(f"- Wii MEM1 (24 MiB) share: {total / WII_MEM1 * 100:.1f}%")
    out.append(f"- Wii MEM2 (64 MiB) share: {total / WII_MEM2 * 100:.1f}%")
    out.append(f"- 64 KiB pages covering sections: {pages} (about {_mib(pages * PAGE)} if paged)")
    out.append(f"- {NEW3DS_NOTE}")
    out.append("")
    out.append("This estimate counts the executable's sections only. Heap, streaming data and "
               "textures are extra.")
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python3 -m spm3ds.diagnose",
                                     description="Write a shareable diagnostic report for main.dol")
    parser.add_argument("dol")
    parser.add_argument("--symbols", help="optional symbol file: lines of 'ADDRESS NAME'")
    parser.add_argument("--out", help="write the report to this file instead of printing it")
    args = parser.parse_args(argv)
    try:
        text = build_report(args.dol, args.symbols)
    except (DolError, OSError) as exc:
        print(f"could not read {args.dol}: {exc}", file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
