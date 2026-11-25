"""Automatic detection of switch-style jump tables in a PowerPC text section.

The pattern recognised is the one compilers emit for `switch (x)` with dense cases:

    cmpli  crF, rX, N          ; bound check on the index
    bgt    crF, default        ; (or bge: default when index >= N)
    rlwinm rI, rX, 2, 0, 29    ; index * 4 (optional)
    lwzx   rT, rBase, rI       ; entry = table[index]
    mtctr  rT
    bcctr  20, 0               ; jump to CTR

The table base is rebuilt from addis/addi/ori constants. Each detection is reported with the
reasons it was accepted. A switch without a readable bound is NOT reported: an unbounded table
size would be a guess, and guessing could make the recompiler miss or invent cases.

Detections are only candidates. The result is the same kind of declaration as a hand-written
JUMPTABLE line, and it is checked against the same rules (entries inside text, aligned).
"""
from dataclasses import dataclass, field

M32 = 0xFFFFFFFF
WINDOW = 40          # instructions to search backwards from the bcctr
MAX_COUNT = 4096


@dataclass
class Detection:
    branch: int
    table: int
    count: int
    targets: list
    confidence: str
    reasons: list = field(default_factory=list)


def _fields(w):
    return {"op": w >> 26, "rd": (w >> 21) & 31, "ra": (w >> 16) & 31, "rb": (w >> 11) & 31,
            "xo": (w >> 1) & 0x3FF, "imm": w & 0xFFFF}


def _simm(v):
    return v - 0x10000 if v & 0x8000 else v


def _dest(w):
    """Register written by the instruction (only the forms this detector understands)."""
    f = _fields(w)
    op = f["op"]
    if op in (14, 15, 32):
        return f["rd"]
    if op in (24, 21):                    # ori, rlwinm write rA
        return f["ra"]
    if op == 31 and f["xo"] in (23, 266, 444):   # lwzx, add, or (or rA, rS, rB writes rA)
        return f["ra"] if f["xo"] == 444 else f["rd"]
    return None


def _const(words, index, reg, depth=0):
    """Constant value of `reg` before words[index], or None when not a simple constant."""
    if reg == 0:
        return 0
    if depth > 4:
        return None
    for k in range(index - 1, max(-1, index - WINDOW - 1), -1):
        w = words[k]
        if _dest(w) != reg:
            continue
        f = _fields(w)
        if f["op"] == 15:                                   # addis
            base = 0 if f["ra"] == 0 else _const(words, k, f["ra"], depth + 1)
            return None if base is None else (base + (_simm(f["imm"]) << 16)) & M32
        if f["op"] == 14:                                   # addi
            base = 0 if f["ra"] == 0 else _const(words, k, f["ra"], depth + 1)
            return None if base is None else (base + _simm(f["imm"])) & M32
        if f["op"] == 24:                                   # ori rA, rS, uimm
            src = _const(words, k, f["rd"], depth + 1)
            return None if src is None else (src | f["imm"]) & M32
        return None
    return None


def _writer(words, index, reg):
    for k in range(index - 1, max(-1, index - WINDOW - 1), -1):
        if _dest(words[k]) == reg:
            return k
    return None


def _find_bound(words, index, idx_reg, reasons):
    """Return (count, reason) from a cmpli on idx_reg followed by a bgt/bge to the default case."""
    for k in range(index - 1, max(-1, index - WINDOW - 1), -1):
        f = _fields(words[k])
        if f["op"] != 10 or f["ra"] != idx_reg:
            continue
        bound = f["imm"]
        bf = f["rd"] >> 2
        # The branch that follows the compare (before the bcctr), on the same CR field.
        for j in range(k + 1, index):
            w = words[j]
            if (w >> 26) != 16:
                continue
            bo, bi = (w >> 21) & 31, (w >> 16) & 31
            if bi >> 2 != bf:
                continue
            bit = bi & 3
            if bo == 12 and bit == 1:          # bgt: index > N goes to default -> cases 0..N
                reasons.append(f"cmpli compares index with {bound}; bgt sends larger values to default")
                return bound + 1
            if bo == 4 and bit == 0:           # bge: index >= N goes to default -> cases 0..N-1
                reasons.append(f"cmpli compares index with {bound}; bge sends values >= {bound} to default")
                return bound
        return None
    return None


def detect(words, base, read_word, text_range):
    """Find jump tables in `words` (the text section starting at `base`).

    read_word(address) -> 32-bit value from the loaded image, or None when unmapped.
    text_range = (start, end) of executable code. Returns a list of Detection, and a list of
    (address, reason) for each bcctr that was looked at and not detected.
    """
    found, rejected = [], []
    start, end = text_range
    for i, w in enumerate(words):
        if (w >> 26) != 19 or ((w >> 1) & 0x3FF) != 528 or w & 1:
            continue
        bo = (w >> 21) & 31
        branch = base + 4 * i
        if bo != 20:
            rejected.append((branch, "bcctr is conditional; only unconditional switches are detected"))
            continue
        reasons = []
        # mtctr must be the instruction just before the bcctr (or a single move-free gap).
        mt = None
        for k in range(i - 1, max(-1, i - 4), -1):
            if (words[k] >> 26) == 31 and (words[k] >> 1) & 0x3FF == 467:
                spr = (((words[k] >> 16) & 31) | (((words[k] >> 11) & 31) << 5))
                if spr == 9:
                    mt = k
                    break
        if mt is None:
            rejected.append((branch, "no mtctr found just before the bcctr"))
            continue
        src = (words[mt] >> 21) & 31
        load = _writer(words, mt, src)
        if load is None or (words[load] >> 26) != 31 or ((words[load] >> 1) & 0x3FF) != 23:
            rejected.append((branch, "CTR is not loaded from a table with lwzx"))
            continue
        lf = _fields(words[load])
        reasons.append(f"mtctr at 0x{base + 4 * mt:08X} takes its value from lwzx")
        # Decide which operand is the base and which the index: try both.
        choice = None
        for base_reg, idx_reg in ((lf["ra"], lf["rb"]), (lf["rb"], lf["ra"])):
            table = _const(words, load, base_reg)
            if table is None:
                continue
            idx_src = idx_reg
            scaled = _writer(words, load, idx_reg)
            if scaled is not None and (words[scaled] >> 26) == 21:
                idx_src = (words[scaled] >> 21) & 31   # rlwinm rI, rX: bound applies to rX
                reasons.append(f"index scaled from r{idx_src} by rlwinm")
            count = _find_bound(words, load, idx_src, reasons)
            if count is not None:
                choice = (table, count)
                break
        if choice is None:
            rejected.append((branch, "no table base and index bound found "
                                     "(a table with no readable bound is not detected)"))
            continue
        table, count = choice
        if not 1 <= count <= MAX_COUNT:
            rejected.append((branch, f"implausible entry count {count}"))
            continue
        targets, problem = [], None
        for n in range(count):
            value = read_word(table + 4 * n)
            if value is None:
                problem = f"table entry {n} at 0x{table + 4 * n:08X} is not mapped"
                break
            if not start <= value < end or value & 3:
                problem = f"table entry {n} = 0x{value:08X} is not an aligned address in text"
                break
            targets.append(value)
        if problem:
            rejected.append((branch, problem))
            continue
        reasons.append(f"{count} entries read from table 0x{table:08X}, all inside text")
        found.append(Detection(branch, table, count, targets, "high" if count <= 256 else "medium",
                               reasons))
    return found, rejected
