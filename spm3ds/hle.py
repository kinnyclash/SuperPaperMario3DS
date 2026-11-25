"""HLE binding: maps guest function addresses to host implementations.

A symbol file binds addresses to names, one per line: `ADDRESS NAME`, for example
`80003100 OSReport`. Blank lines and lines starting with `#` are ignored. Names that have a
host implementation in runtime/hle.c are bound; the rest are reported.

The symbol file comes from the user's own decompilation work. It is not shipped here.
"""

# Keep in step with runtime/hle.c.
IMPLEMENTED = (
    "OSReport",
    "OSHalt",
    "memcpy",
    "memset",
    "DCFlushRange",
    "DCInvalidateRange",
    "ICInvalidateRange",
    "OSCreateHeap",
    "OSAllocFromHeap",
    "OSFreeToHeap",
    "OSGetTick",
    "OSGetTime",
    "spm3ds_FSOpen",
    "spm3ds_FSSize",
    "spm3ds_FSRead",
    "spm3ds_FSClose",
)


class SymbolError(ValueError):
    pass


def parse_symbols(text):
    """Return a list of (address, name) pairs. Raises SymbolError on malformed lines."""
    pairs = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise SymbolError(f"line {number}: expected 'ADDRESS NAME', got {raw!r}")
        try:
            address = int(parts[0], 16)
        except ValueError:
            raise SymbolError(f"line {number}: bad address {parts[0]!r}") from None
        pairs.append((address, parts[1]))
    return pairs


def external_from_symbols(text):
    """Return ({address: name} for implemented HLE names, [names with no implementation])."""
    bound = {}
    unimplemented = []
    for address, name in parse_symbols(text):
        if name in IMPLEMENTED:
            bound[address] = name
        else:
            unimplemented.append(name)
    return bound, unimplemented
