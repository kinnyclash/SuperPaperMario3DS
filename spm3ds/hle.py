"""HLE binding: maps guest function addresses to host implementations.

A symbol file binds addresses to names, one per line: `ADDRESS NAME`, for example
`80003100 OSReport`. Blank lines and lines starting with `#` are ignored. Names that have a
host implementation in runtime/hle.c are bound; the rest are reported.

The symbol file comes from the user's own decompilation work. It is not shipped here.
"""

# Keep in step with runtime/hle.c, runtime/os.c and runtime/dvd.c.
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
    # runtime/dvd.c (Nintendo SDK names, unverified; see runtime/dvd.h)
    "DVDOpen",
    "DVDFastOpen",
    "DVDConvertPathToEntrynum",
    "DVDClose",
    "DVDReadPrio",
    "DVDReadAsyncPrio",
    "DVDGetCommandBlockStatus",
    "DVDGetDriveStatus",
    # Provided by the 3DS game app (platform/3ds/source/game/game_main.c): presents a frame.
    "VIWaitForRetrace",
    # runtime/os_thread.c (Nintendo SDK names, unverified; see runtime/os_thread.h)
    "OSCreateThread",
    "OSResumeThread",
    "OSSuspendThread",
    "OSSleepThread",
    "OSWakeupThread",
    "OSInitThreadQueue",
    "OSYieldThread",
    "OSExitThread",
    "OSJoinThread",
    "OSDetachThread",
    "OSGetCurrentThread",
    "OSIsThreadTerminated",
    "OSIsThreadSuspended",
    "OSSetThreadPriority",
    "OSGetThreadPriority",
    "OSDisableInterrupts",
    "OSEnableInterrupts",
    "OSRestoreInterrupts",
    "OSInitMessageQueue",
    "OSSendMessage",
    "OSJamMessage",
    "OSReceiveMessage",
    "OSInitMutex",
    "OSLockMutex",
    "OSUnlockMutex",
    "OSTryLockMutex",
    "OSInitCond",
    "OSWaitCond",
    "OSSignalCond",
    "OSCreateAlarm",
    "OSSetAlarm",
    "OSSetPeriodicAlarm",
    "OSCancelAlarm",
)


class SymbolError(ValueError):
    pass


CODE_SECTIONS = (".init", ".text")


def _parse_dtk_line(number, raw):
    """One decomp-toolkit symbols.txt line:
        NAME = SECTION:0xADDRESS; // type:function size:0x... scope:...
    Returns (address, name, is_function)."""
    body, _, comment = raw.partition("//")
    name, _, rest = body.partition("=")
    name = name.strip()
    rest = rest.strip().rstrip(";").strip()
    section, colon, addr = rest.rpartition(":")
    if not name or not colon:
        raise SymbolError(f"line {number}: expected 'NAME = SECTION:0xADDRESS;', got {raw!r}")
    try:
        address = int(addr, 16)
    except ValueError:
        raise SymbolError(f"line {number}: bad address {addr!r}") from None
    fields = dict(f.split(":", 1) for f in comment.split() if ":" in f)
    kind = fields.get("type")
    is_function = kind == "function" or (kind is None and section.strip() in CODE_SECTIONS)
    return address, name, is_function


def parse_symbols(text):
    """Return a list of (address, name) pairs for functions. Raises SymbolError on malformed
    lines.

    Two formats are accepted, line by line:
      * `ADDRESS NAME` (hex address), with `#` comments;
      * decomp-toolkit's symbols.txt, `NAME = .text:0xADDRESS; // type:function ...`, as
        published by community decompilation projects. Only function symbols are returned
        (type:function, or a symbol in .init/.text without a type), because data addresses
        must not be analysed as code.
    """
    pairs = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if "=" in raw.split("//", 1)[0]:
            address, name, is_function = _parse_dtk_line(number, raw)
            if is_function:
                pairs.append((address, name))
            continue
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
