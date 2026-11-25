"""PowerPC (Gekko) decoder, operation semantics and reference interpreter.

Modelled:
  * integer ALU with Rc (CR0) forms, rotates, shifts, compares, cntlzw, extsb/extsh
  * D-form and X-form loads and stores, including the update forms lwzu and stwu
  * single and double precision FP arithmetic (A-form and X-form), FP loads and stores
  * b, bl, bc (all BO forms with CTR), bclr, bcctr and bcctrl
  * mfspr/mtspr for LR and CTR, and mfcr

Carry: XER.CA (bit 29) is modelled. addic, subfic, addc, subfc, adde, subfe, addze, subfze,
addme, subfme, srawi and sraw set or read it.

Not modelled; decoding raises Unsupported: XER overflow and summary overflow (so OE forms are
rejected), FPSCR, Rc on FP instructions, paired singles, bclrl, bc/bclr with LK, system and
cache instructions. fcmpo is treated as fcmpu: the invalid-operation exception flags are not
modelled.
"""
import math
import struct
from dataclasses import dataclass

from .quant import TYPE_FORMATS, dequantize, load_fields, quantize, store_fields
from .memory import GuestFault

M32 = 0xFFFFFFFF
M64 = 0xFFFFFFFFFFFFFFFF
SIGN64 = 1 << 63
SPR_LR = 8
SPR_CTR = 9
SPR_GQR0 = 912   # GQR0..GQR7 are SPRs 912..919

# Largest single-precision value, and the double at which rounding to single overflows.
# Ties at the halfway point round to infinity, as IEEE 754 requires.
CA_BIT = 1 << 29  # XER.CA

# Carry-setting integer ops, all writing rD. Each entry is (invert rA, source of the second
# operand, carry-in source): the sum is (rA or ~rA) + second + carry-in.
CARRY_OPS = {
    "addic": (False, "imm", "zero"),
    "subfic": (True, "imm", "one"),   # imm - rA = ~rA + imm + 1
    "addc": (False, "rb", "zero"),
    "subfc": (True, "rb", "one"),     # rB - rA = ~rA + rB + 1
    "adde": (False, "rb", "ca"),
    "subfe": (True, "rb", "ca"),
    "addze": (False, "zero", "ca"),
    "subfze": (True, "zero", "ca"),
    "addme": (False, "minus1", "ca"),  # rA + CA - 1
    "subfme": (True, "minus1", "ca"),  # ~rA + CA - 1
}

SINGLE_MAX = (2.0 - 2.0 ** -23) * 2.0 ** 127
SINGLE_OVERFLOW = SINGLE_MAX + 2.0 ** 103


class Unsupported(Exception):
    """An instruction outside the implemented subset."""


@dataclass(frozen=True)
class Insn:
    addr: int
    op: str
    rd: int = 0      # rD; rS for stores, logical ops, rotates and mtspr
    ra: int = 0
    rb: int = 0
    rc: bool = False
    imm: int = 0     # sign-extended immediate, or the already-shifted value for the *is forms
    sh: int = 0
    mb: int = 0
    me: int = 0
    bo: int = 0
    bi: int = 0
    crf: int = 0
    target: int = 0
    spr: int = 0
    link: bool = False
    fd: int = 0
    fa: int = 0
    fb: int = 0
    fc: int = 0
    wbit: int = 0    # paired singles: W (psq_l/psq_st: only the first single)
    gqr: int = 0     # paired singles: which GQR register holds the quantization


# ---------------------------------------------------------------- bit helpers

def s32(x):
    x &= M32
    return x - (1 << 32) if x & 0x80000000 else x


def sext(value, bits):
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def rotl32(x, n):
    x &= M32
    n &= 31
    return ((x << n) | (x >> (32 - n))) & M32


def mask32(mb, me):
    """Mask with IBM bit numbering: bit 0 is the most significant. Wraps when mb > me."""
    start = M32 >> mb
    end = (M32 << (31 - me)) & M32
    return (start & end) if mb <= me else (start | end)


def slw(a, b):
    n = b & 63
    return 0 if n & 32 else (a << n) & M32


def srw(a, b):
    n = b & 63
    return 0 if n & 32 else (a & M32) >> n


def sraw(a, b):
    n = b & 63
    x = s32(a)
    if n >= 32:
        return M32 if x < 0 else 0
    return (x >> n) & M32


def add_carry(a, b, cin):
    """Return (sum mod 2^32, carry out) for a + b + cin with unsigned 32-bit inputs."""
    s = (a & M32) + (b & M32) + cin
    return s & M32, (s >> 32) & 1


def sraw_carry(a, n):
    """Return (value, CA) for an arithmetic right shift. CA is set when a negative value
    loses one or more 1 bits."""
    n &= 63
    x = s32(a)
    if n >= 32:
        return (M32 if x < 0 else 0), (1 if x < 0 and (a & M32) else 0)
    value = (x >> n) & M32
    lost = (a & M32) & ((1 << n) - 1)
    return value, (1 if x < 0 and lost else 0)


def mulhw(a, b):
    """High 32 bits of the signed 64-bit product."""
    return ((s32(a) * s32(b)) >> 32) & M32


def mulhwu(a, b):
    """High 32 bits of the unsigned 64-bit product."""
    return ((a & M32) * (b & M32)) >> 32


def divw(a, b):
    """Signed divide, truncating toward zero. Division by zero and overflow give 0."""
    x, y = s32(a), s32(b)
    if y == 0 or (x == -(1 << 31) and y == -1):
        return 0
    q = abs(x) // abs(y)
    if (x < 0) != (y < 0):
        q = -q
    return q & M32


def divwu(a, b):
    a &= M32
    b &= M32
    return 0 if b == 0 else a // b


def cntlzw(x):
    return 32 - (x & M32).bit_length()


def extsb(x):
    return sext(x, 8) & M32


def extsh(x):
    return sext(x, 16) & M32


def cmp_field(cr, crf, a, b):
    """Set condition register field `crf` from comparing a with b (both already in range)."""
    shift = 28 - 4 * crf
    field = 8 if a < b else (4 if a > b else 2)
    return (cr & ~(0xF << shift) & M32) | (field << shift)


def fp_cmp_field(cr, crf, a, b):
    shift = 28 - 4 * crf
    if math.isnan(a) or math.isnan(b):
        field = 1
    else:
        field = 8 if a < b else (4 if a > b else 2)
    return (cr & ~(0xF << shift) & M32) | (field << shift)


# ---------------------------------------------------------------- floating point helpers

def bits_to_double(bits):
    return struct.unpack(">d", (bits & M64).to_bytes(8, "big"))[0]


def double_to_bits(x):
    return int.from_bytes(struct.pack(">d", x), "big")


def round_single(x):
    """Round a double to the nearest single, returned as a double (IEEE 754 binary32)."""
    if math.isnan(x):
        return x
    if abs(x) >= SINGLE_OVERFLOW:
        return math.copysign(math.inf, x)
    return struct.unpack(">f", struct.pack(">f", x))[0]


def fdiv(a, b):
    if b == 0.0:
        if a == 0.0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def fma(a, c, b):
    try:
        return math.fma(a, c, b)
    except ValueError:
        return math.nan


def single_value(bits):
    """The double that a single-precision bit pattern holds exactly."""
    return struct.unpack(">f", (bits & M32).to_bytes(4, "big"))[0]


def single_bits(x):
    """Round a double to single precision and return its 32-bit pattern."""
    return struct.unpack(">I", struct.pack(">f", round_single(x)))[0]


def fctiwz_bits(x):
    """Convert to a 32-bit integer, truncating, saturating. The result sits in the low word
    of the FPR, with the high word set to 0xFFF8 as Gekko does."""
    if math.isnan(x):
        value = -(1 << 31)
    elif x >= 2.0 ** 31:
        value = (1 << 31) - 1
    elif x < -(2.0 ** 31):
        value = -(1 << 31)
    else:
        value = int(x)
    return 0xFFF8000000000000 | (value & M32)


# ---------------------------------------------------------------- operation tables

# name -> (python function of two unsigned 32-bit values, C expression template)
INT_BINARY = {
    "add": (lambda a, b: a + b, "({a} + {b})"),
    "subf": (lambda a, b: b - a, "({b} - {a})"),
    "mullw": (lambda a, b: a * b, "({a} * {b})"),
    "divw": (divw, "ppc_divw({a}, {b})"),
    "divwu": (divwu, "ppc_divwu({a}, {b})"),
    "and": (lambda a, b: a & b, "({a} & {b})"),
    "andc": (lambda a, b: a & ~b, "({a} & ~{b})"),
    "or": (lambda a, b: a | b, "({a} | {b})"),
    "orc": (lambda a, b: a | ~b, "({a} | ~{b})"),
    "xor": (lambda a, b: a ^ b, "({a} ^ {b})"),
    "nor": (lambda a, b: ~(a | b), "(~({a} | {b}))"),
    "nand": (lambda a, b: ~(a & b), "(~({a} & {b}))"),
    "eqv": (lambda a, b: ~(a ^ b), "(~({a} ^ {b}))"),
    "slw": (slw, "ppc_slw({a}, {b})"),
    "srw": (srw, "ppc_srw({a}, {b})"),
    "mulhw": (mulhw, "ppc_mulhw({a}, {b})"),
    "mulhwu": (mulhwu, "ppc_mulhwu({a}, {b})"),
}
# sraw and srawi set XER.CA, so they are handled by their own branches, not INT_BINARY.

# These write rA and read rS from the rD field, so the source is the first operand.
LOGICAL_OPS = {"and", "andc", "or", "orc", "xor", "nor", "nand", "eqv", "slw", "srw", "sraw"}

# name -> (python function, C expression template) for a single source operand
INT_UNARY = {
    "neg": (lambda a: -a, "(0u - {a})"),
    "cntlzw": (cntlzw, "ppc_cntlzw({a})"),
    "extsb": (extsb, "ppc_extsb({a})"),
    "extsh": (extsh, "ppc_extsh({a})"),
}
UNARY_FROM_RA = {"neg"}  # the others read rS from the rD field and write rA

SHIFT_IMM = {"srawi"}
X_MEM_LOAD = {"lwzx": (4, False), "lbzx": (1, False), "lhzx": (2, False)}
X_MEM_STORE = {"stwx": 4, "stbx": 1, "sthx": 2}
D_MEM_LOAD = {"lwz": 4, "lwzu": 4, "lbz": 1, "lbzu": 1, "lhz": 2, "lhzu": 2, "lha": 2}
D_MEM_STORE = {"stw": 4, "stwu": 4, "stb": 1, "stbu": 1, "sth": 2, "sthu": 2}
FP_MEM_LOAD = {"lfs", "lfd"}
FP_MEM_STORE = {"stfs", "stfd"}
# Paired-single arithmetic, applied to each half. Arguments are (a, c, b) as doubles.
PS_ARITH = {
    "ps_add": lambda a, c, b: a + b,
    "ps_sub": lambda a, c, b: a - b,
    "ps_mul": lambda a, c, b: a * c,
    "ps_div": lambda a, c, b: fdiv(a, b),
    "ps_madd": lambda a, c, b: fma(a, c, b),
    "ps_msub": lambda a, c, b: fma(a, c, -b),
    "ps_nmadd": lambda a, c, b: -fma(a, c, b),
    "ps_nmsub": lambda a, c, b: -fma(a, c, -b),
}
# merge: (ps index taken from frA, ps index taken from frB)
PS_MERGE = {"ps_merge00": (0, 0), "ps_merge01": (0, 1), "ps_merge10": (1, 0),
            "ps_merge11": (1, 1)}
PS_OPS = set(PS_ARITH) | {"ps_neg", "ps_nabs", "ps_abs", "ps_mr"} | set(PS_MERGE)

UPDATE_LOADS = {"lwzu", "lbzu", "lhzu"}   # invalid when rA is 0 or equals rD
UPDATE_STORES = {"stwu", "stbu", "sthu"}  # invalid when rA is 0
UPDATE_FORMS = UPDATE_LOADS | UPDATE_STORES

FP_BINARY = {  # double precision: result = fa op fb
    "fadd": lambda a, b: a + b,
    "fsub": lambda a, b: a - b,
    "fdiv": fdiv,
}
FP_SINGLE = {  # single precision: rounded result of the double operation
    "fadds": lambda a, c, b: a + b,
    "fsubs": lambda a, c, b: a - b,
    "fmuls": lambda a, c, b: a * c,
    "fdivs": lambda a, c, b: fdiv(a, b),
}
# Paired singles (Gekko). Opcode 4 holds the arithmetic; each operation applies to the two
# 32-bit single-precision halves of an FPR, ps0 (bits 63..32) and ps1 (bits 31..0).
PS_A_FORM = {18: "ps_div", 20: "ps_sub", 21: "ps_add", 25: "ps_mul", 28: "ps_msub",
             29: "ps_madd", 30: "ps_nmsub", 31: "ps_nmadd"}
PS_X_FORM = {40: "ps_neg", 72: "ps_mr", 136: "ps_nabs", 264: "ps_abs",
             528: "ps_merge00", 560: "ps_merge01", 592: "ps_merge10", 624: "ps_merge11"}
PS_LOADS = {56: "psq_l", 57: "psq_lu"}
PS_STORES = {60: "psq_st", 61: "psq_stu"}

FP_A_FORM = {  # the A-form instructions, keyed by 5-bit extended opcode
    63: {18: "fdiv", 20: "fsub", 21: "fadd", 25: "fmul", 28: "fmsub", 29: "fmadd",
         30: "fnmsub", 31: "fnmadd"},
    59: {18: "fdivs", 20: "fsubs", 21: "fadds", 25: "fmuls"},
}


# ---------------------------------------------------------------- decoder

def _spr(word):
    raw = (word >> 11) & 0x3FF
    return ((raw & 0x1F) << 5) | (raw >> 5)


def decode(word, addr):
    w = word & M32
    op = w >> 26
    d = (w >> 21) & 31
    a = (w >> 16) & 31
    b = (w >> 11) & 31
    c = (w >> 6) & 31
    rc = bool(w & 1)
    simm = sext(w, 16)
    uimm = w & 0xFFFF

    def ins(name, **fields):
        return Insn(addr, name, **fields)

    def bad(what):
        return Unsupported(f"{addr:#010x} [{w:08x}]: {what}")

    if op == 7:
        return ins("mulli", rd=d, ra=a, imm=simm)
    if op == 8:
        return ins("subfic", rd=d, ra=a, imm=simm)
    if op == 12:
        return ins("addic", rd=d, ra=a, imm=simm)
    if op == 13:
        return ins("addic", rd=d, ra=a, imm=simm, rc=True)
    if op == 14:
        return ins("addi", rd=d, ra=a, imm=simm)
    if op == 15:
        return ins("addis", rd=d, ra=a, imm=simm << 16)
    if op in (10, 11):
        if (w >> 21) & 1:
            raise bad("64-bit compare")
        if op == 10:
            return ins("cmpli", ra=a, imm=uimm, crf=(w >> 23) & 7)
        return ins("cmpi", ra=a, imm=simm, crf=(w >> 23) & 7)
    if op == 16:
        if w & 0b11:
            raise bad("conditional branch with AA or LK")
        return ins("bc", bo=d, bi=a, target=(addr + sext(w & 0xFFFC, 16)) & M32)
    if op == 18:
        if w & 0b10:
            raise bad("absolute branch")
        target = (addr + sext(w & 0x03FFFFFC, 26)) & M32
        return ins("bl" if w & 1 else "b", target=target, link=bool(w & 1))
    if op == 19:
        xo = (w >> 1) & 0x3FF
        if xo == 16:
            if w & 1:
                raise bad("bclrl (conditional call through LR)")
            return ins("bclr", bo=d, bi=a)
        if xo == 528:
            if not d & 4:
                raise bad("bcctr with CTR decrement is invalid")
            return ins("bcctr", bo=d, bi=a, link=bool(w & 1))
        raise bad(f"opcode 19 xo={xo}")
    if op in (20, 21):
        name = "rlwimi" if op == 20 else "rlwinm"
        return ins(name, rd=d, ra=a, sh=b, mb=c, me=(w >> 1) & 31, rc=rc)
    if op == 23:
        return ins("rlwnm", rd=d, ra=a, rb=b, mb=c, me=(w >> 1) & 31, rc=rc)
    if op == 24:
        return ins("ori", rd=d, ra=a, imm=uimm)
    if op == 25:
        return ins("ori", rd=d, ra=a, imm=uimm << 16)  # oris, shifted immediate
    if op == 26:
        return ins("xori", rd=d, ra=a, imm=uimm)
    if op == 27:
        return ins("xori", rd=d, ra=a, imm=uimm << 16)
    if op == 28:
        return ins("andi", rd=d, ra=a, imm=uimm, rc=True)
    if op == 29:
        return ins("andi", rd=d, ra=a, imm=uimm << 16, rc=True)

    dform = {32: "lwz", 33: "lwzu", 34: "lbz", 35: "lbzu", 36: "stw", 37: "stwu", 38: "stb",
             39: "stbu", 40: "lhz", 41: "lhzu", 42: "lha", 44: "sth", 45: "sthu"}
    if op in dform:
        name = dform[op]
        if name in UPDATE_FORMS and (a == 0 or (name in UPDATE_LOADS and a == d)):
            raise bad(f"{name} with invalid base register")
        return ins(name, rd=d, ra=a, imm=simm)
    fpform = {48: "lfs", 50: "lfd", 52: "stfs", 54: "stfd"}
    if op in fpform:
        return ins(fpform[op], fd=d, ra=a, imm=simm)

    if op == 59 or op == 63:
        x5 = (w >> 1) & 31
        if x5 in FP_A_FORM[op]:
            if rc:
                raise bad("Rc on FP arithmetic")
            return ins(FP_A_FORM[op][x5], fd=d, fa=a, fb=b, fc=c)
        if op == 59:
            raise bad("opcode 59")
        x10 = (w >> 1) & 0x3FF
        if rc:
            raise bad("Rc on FP")
        if x10 in (0, 32):
            if (w >> 21) & 3:
                raise bad("fcmpu/fcmpo with reserved bits")
            return ins("fcmpu" if x10 == 0 else "fcmpo", crf=(w >> 23) & 7, fa=a, fb=b)
        fixed = {15: "fctiwz", 12: "frsp", 72: "fmr", 40: "fneg", 264: "fabs", 136: "fnabs"}
        if x10 in fixed:
            return ins(fixed[x10], fd=d, fb=b)
        raise bad(f"opcode 63 xo={x10}")

    if op == 4:
        xo5 = (w >> 1) & 31
        if xo5 in PS_A_FORM:
            if rc:
                raise bad("Rc on paired-single arithmetic")
            return ins(PS_A_FORM[xo5], fd=d, fa=a, fb=b, fc=c)
        xo10 = (w >> 1) & 0x3FF
        if xo10 in PS_X_FORM:
            if rc:
                raise bad("Rc on paired-single move")
            return ins(PS_X_FORM[xo10], fd=d, fb=b, fa=a)
        raise bad(f"paired-single opcode 4 xo={xo10} is not modelled")

    if op in PS_LOADS or op in PS_STORES:
        name = PS_LOADS.get(op) or PS_STORES[op]
        if name in ("psq_lu", "psq_stu") and a == 0:
            raise bad(f"{name} with rA = 0")
        if name == "psq_lu" and a == d:
            raise bad("psq_lu with rA = rD")
        return ins(name, fd=d, ra=a, imm=sext(w & 0xFFF, 12),
                   wbit=(w >> 15) & 1, gqr=(w >> 12) & 7)

    if op == 31:
        x9 = (w >> 1) & 0x1FF
        xo_names = {266: "add", 40: "subf", 235: "mullw", 491: "divw", 459: "divwu",
                    104: "neg", 10: "addc", 8: "subfc", 138: "adde", 136: "subfe",
                    202: "addze", 200: "subfze", 234: "addme", 232: "subfme",
                    75: "mulhw", 11: "mulhwu"}
        if x9 in xo_names:
            if (w >> 10) & 1:
                raise bad("OE forms (overflow recording) are not modelled")
            name = xo_names[x9]
            if name in ("neg", "addze", "subfze", "addme", "subfme"):
                return ins(name, rd=d, ra=a, rc=rc)
            return ins(name, rd=d, ra=a, rb=b, rc=rc)
        x10 = (w >> 1) & 0x3FF
        if x10 in (0, 32):
            if (w >> 21) & 1:
                raise bad("64-bit compare")
            return ins("cmp" if x10 == 0 else "cmpl", crf=(w >> 23) & 7, ra=a, rb=b)
        if x10 == 19:
            return ins("mfcr", rd=d)
        xloads = {23: "lwzx", 87: "lbzx", 279: "lhzx"}
        if x10 in xloads:
            return ins(xloads[x10], rd=d, ra=a, rb=b)
        xstores = {151: "stwx", 215: "stbx", 407: "sthx"}
        if x10 in xstores:
            return ins(xstores[x10], rd=d, ra=a, rb=b)
        xlogical = {28: "and", 60: "andc", 316: "xor", 444: "or", 412: "orc", 124: "nor",
                    476: "nand", 284: "eqv", 24: "slw", 536: "srw", 792: "sraw"}
        if x10 in xlogical:
            return ins(xlogical[x10], rd=d, ra=a, rb=b, rc=rc)
        if x10 == 824:
            return ins("srawi", rd=d, ra=a, sh=b, rc=rc)
        unary = {26: "cntlzw", 954: "extsb", 922: "extsh"}
        if x10 in unary:
            return ins(unary[x10], rd=d, ra=a, rc=rc)
        if x10 in (339, 467):
            spr = _spr(w)
            if (spr not in (SPR_LR, SPR_CTR) and not SPR_GQR0 <= spr <= SPR_GQR0 + 7) or rc:
                raise bad(f"mfspr/mtspr of SPR {spr} is not modelled")
            return ins("mfspr" if x10 == 339 else "mtspr", rd=d, spr=spr)
        raise bad(f"opcode 31 xo={x10}")

    raise bad(f"primary opcode {op}")


# ---------------------------------------------------------------- interpreter

class Machine:
    """Reference interpreter. Executes guest code directly from the decoded text image.

    `image` maps addresses to instruction words. `memory` is a MemoryMap. Calls to any
    address in `external` raise GuestFault, because the interpreter has no HLE hooks.
    """

    def __init__(self, image, memory, external=frozenset(), jump_targets=None):
        self.image = image
        self.mem = memory
        self.external = frozenset(external)
        self.jump_targets = {int(k): tuple(v) for k, v in (jump_targets or {}).items()}
        self.r = [0] * 32
        self.f = [0] * 32
        self.lr = 0
        self.ctr = 0
        self.cr = 0
        self.xer = 0          # only bit 29 (CA) is modelled
        self.gqr = [0] * 8    # quantization registers GQR0..GQR7
        self.entries = frozenset()
        self._cache = {}

    def run(self, entry):
        from .analysis import analyze  # imported here to avoid a circular import
        self.entries = frozenset(
            analyze(self.image, entry, self.external, (), self.jump_targets).entries)
        self._run(entry, entry)

    def state(self):
        return {"r": list(self.r), "f": list(self.f), "lr": self.lr,
                "ctr": self.ctr, "cr": self.cr, "xer": self.xer & CA_BIT}

    def _set_ca(self, carry):
        self.xer = (self.xer & ~CA_BIT) | (CA_BIT if carry else 0)

    def _fetch(self, pc):
        insn = self._cache.get(pc)
        if insn is None:
            if pc not in self.image:
                raise GuestFault(f"{pc:#010x}: code outside the loaded image")
            insn = decode(self.image[pc], pc)
            self._cache[pc] = insn
        return insn

    def _taken(self, insn):
        bo = insn.bo
        ctr_ok = True
        if not bo & 4:
            self.ctr = (self.ctr - 1) & M32
            ctr_ok = (self.ctr != 0) != bool(bo & 2)
        cond_ok = bool(bo & 16) or (((self.cr >> (31 - insn.bi)) & 1) == (1 if bo & 8 else 0))
        return ctr_ok and cond_ok

    def _run(self, fn, pc):
        while True:
            insn = self._fetch(pc)
            op = insn.op
            if op == "bclr":
                if self._taken(insn):
                    return
                pc += 4
            elif op == "bcctr":
                if insn.addr in self.jump_targets:
                    target = self.ctr & ~3 & M32
                    if target not in self.jump_targets[insn.addr]:
                        raise GuestFault(f"{pc:#010x}: CTR {target:#010x} is not in the "
                                         f"declared jump table")
                    pc = target
                    continue
                if self._taken(insn):
                    target = self.ctr & ~3 & M32
                    if insn.link:
                        self.lr = (pc + 4) & M32
                        self._call(target)
                        pc += 4
                    else:
                        self._call(target)
                        return
                else:
                    pc += 4
            elif op == "bc":
                pc = insn.target if self._taken(insn) else pc + 4
            elif op == "bl":
                self.lr = (pc + 4) & M32
                self._call(insn.target)
                pc += 4
            elif op == "b":
                if insn.target in self.external:
                    raise GuestFault(f"{pc:#010x}: tail call to external {insn.target:#010x}")
                if insn.target in self.entries and insn.target != fn:
                    self._call(insn.target)
                    return
                fn_target = insn.target
                pc = fn_target
            else:
                self._exec(insn)
                pc += 4

    def _call(self, target):
        if target in self.external:
            raise GuestFault(f"call to external {target:#010x} is not supported by the interpreter")
        self._run(target, target)

    def _exec_paired(self, insn):
        """Paired singles. Each FPR holds two singles: ps0 in bits 63..32, ps1 in bits 31..0."""
        op = insn.op
        f = self.f
        if op in ("psq_l", "psq_lu", "psq_st", "psq_stu"):
            gqr = self.gqr[insn.gqr]
            is_load = op in ("psq_l", "psq_lu")
            ea = self._ea(insn)
            if op in ("psq_lu", "psq_stu"):
                self.r[insn.ra] = ea
            if is_load:
                kind, scale = load_fields(gqr)
                if kind == 0:
                    hi = self.mem.read_uint(ea, 4)
                    lo = 0x3F800000 if insn.wbit else self.mem.read_uint((ea + 4) & M32, 4)
                else:
                    if kind not in TYPE_FORMATS:
                        raise GuestFault(f"{op}: load type {kind} is reserved")
                    size, signed = TYPE_FORMATS[kind]
                    hi = single_bits(dequantize(self.mem.read_uint(ea, size), size, signed, scale))
                    if insn.wbit:
                        lo = 0x3F800000
                    else:
                        lo = single_bits(dequantize(self.mem.read_uint((ea + size) & M32, size),
                                                    size, signed, scale))
                f[insn.fd] = (hi << 32) | lo
            else:
                kind, scale = store_fields(gqr)
                if kind == 0:
                    self.mem.write_uint(ea, 4, f[insn.fd] >> 32)
                    if not insn.wbit:
                        self.mem.write_uint((ea + 4) & M32, 4, f[insn.fd] & M32)
                else:
                    if kind not in TYPE_FORMATS:
                        raise GuestFault(f"{op}: store type {kind} is reserved")
                    size, signed = TYPE_FORMATS[kind]
                    self.mem.write_uint(ea, size, quantize(single_value(f[insn.fd] >> 32),
                                                           size, signed, scale))
                    if not insn.wbit:
                        self.mem.write_uint((ea + size) & M32, size,
                                            quantize(single_value(f[insn.fd] & M32),
                                                     size, signed, scale))
            return
        if op in PS_ARITH:
            fn = PS_ARITH[op]
            a, c, b = f[insn.fa], f[insn.fc], f[insn.fb]
            hi = single_bits(fn(single_value(a >> 32), single_value(c >> 32), single_value(b >> 32)))
            lo = single_bits(fn(single_value(a & M32), single_value(c & M32), single_value(b & M32)))
            f[insn.fd] = (hi << 32) | lo
            return
        if op in ("ps_neg", "ps_nabs", "ps_abs", "ps_mr"):
            v = f[insn.fb]
            hi, lo = v >> 32, v & M32
            if op == "ps_neg":
                hi, lo = hi ^ 0x80000000, lo ^ 0x80000000
            elif op == "ps_nabs":
                hi, lo = hi | 0x80000000, lo | 0x80000000
            elif op == "ps_abs":
                hi, lo = hi & 0x7FFFFFFF, lo & 0x7FFFFFFF
            f[insn.fd] = (hi << 32) | lo
            return
        if op in PS_MERGE:
            a_src, b_src = PS_MERGE[op]   # 0 selects ps0, 1 selects ps1
            a_word = f[insn.fa] >> (32 * (1 - a_src)) & M32
            b_word = f[insn.fb] >> (32 * (1 - b_src)) & M32
            f[insn.fd] = (a_word << 32) | b_word
            return
        raise GuestFault(f"interpreter has no paired-single rule for {op}")

    def _set_cr0(self, value):
        self.cr = cmp_field(self.cr, 0, s32(value), 0)

    def _ea(self, insn, indexed=False):
        base = self.r[insn.ra] if insn.ra else 0
        if indexed:
            return (base + self.r[insn.rb]) & M32
        return (base + insn.imm) & M32

    def _exec(self, insn):
        op = insn.op
        r = self.r
        f = self.f
        if op in PS_OPS or op in ("psq_l", "psq_lu", "psq_st", "psq_stu"):
            self._exec_paired(insn)
            return
        if op in CARRY_OPS:
            ca = 1 if self.xer & CA_BIT else 0
            a_inv, b_src, cin_src = CARRY_OPS[op]
            a = (~r[insn.ra] if a_inv else r[insn.ra]) & M32
            if b_src == "rb":
                b = r[insn.rb]
            elif b_src == "imm":
                b = insn.imm & M32
            elif b_src == "minus1":
                b = M32
            else:
                b = 0
            cin = {"zero": 0, "one": 1, "ca": ca}[cin_src]
            value, carry = add_carry(a, b, cin)
            r[insn.rd] = value
            self._set_ca(carry)
            if insn.rc:
                self._set_cr0(value)
            return
        if op in ("sraw", "srawi"):
            amount = r[insn.rb] if op == "sraw" else insn.sh
            value, carry = sraw_carry(r[insn.rd], amount)
            r[insn.ra] = value
            self._set_ca(carry)
            if insn.rc:
                self._set_cr0(value)
            return
        if op in INT_BINARY:
            fn = INT_BINARY[op][0]
            if op in LOGICAL_OPS:
                dst, value = insn.ra, fn(r[insn.rd], r[insn.rb]) & M32
            else:
                dst, value = insn.rd, fn(r[insn.ra], r[insn.rb]) & M32
            r[dst] = value
            if insn.rc:
                self._set_cr0(value)
            return
        if op in INT_UNARY:
            fn = INT_UNARY[op][0]
            if op in UNARY_FROM_RA:
                dst, value = insn.rd, fn(r[insn.ra]) & M32
            else:
                dst, value = insn.ra, fn(r[insn.rd]) & M32
            r[dst] = value
            if insn.rc:
                self._set_cr0(value)
            return
        if op in ("addi", "addis"):
            base = r[insn.ra] if insn.ra else 0
            r[insn.rd] = (base + insn.imm) & M32
            return
        if op == "mulli":
            r[insn.rd] = (r[insn.ra] * insn.imm) & M32
            return
        if op in ("ori", "xori", "andi"):
            src = r[insn.rd]
            value = {"ori": src | (insn.imm & M32),
                     "xori": src ^ (insn.imm & M32),
                     "andi": src & (insn.imm & M32)}[op]
            r[insn.ra] = value & M32
            if insn.rc:
                self._set_cr0(value)
            return
        if op in ("rlwinm", "rlwimi", "rlwnm"):
            n = r[insn.rb] & 31 if op == "rlwnm" else insn.sh
            m = mask32(insn.mb, insn.me)
            rot = rotl32(r[insn.rd], n)
            if op == "rlwimi":
                value = (rot & m) | (r[insn.ra] & ~m & M32)
            else:
                value = rot & m
            r[insn.ra] = value & M32
            if insn.rc:
                self._set_cr0(value)
            return
        if op == "cmp":
            self.cr = cmp_field(self.cr, insn.crf, s32(r[insn.ra]), s32(r[insn.rb]))
            return
        if op == "cmpl":
            self.cr = cmp_field(self.cr, insn.crf, r[insn.ra], r[insn.rb])
            return
        if op == "cmpi":
            self.cr = cmp_field(self.cr, insn.crf, s32(r[insn.ra]), insn.imm)
            return
        if op == "cmpli":
            self.cr = cmp_field(self.cr, insn.crf, r[insn.ra], insn.imm & M32)
            return
        if op == "mfcr":
            r[insn.rd] = self.cr
            return
        if op == "mfspr":
            if insn.spr == SPR_LR:
                r[insn.rd] = self.lr
            elif insn.spr == SPR_CTR:
                r[insn.rd] = self.ctr
            else:
                r[insn.rd] = self.gqr[insn.spr - SPR_GQR0]
            return
        if op == "mtspr":
            if insn.spr == SPR_LR:
                self.lr = r[insn.rd]
            elif insn.spr == SPR_CTR:
                self.ctr = r[insn.rd]
            else:
                self.gqr[insn.spr - SPR_GQR0] = r[insn.rd]
            return
        if op in D_MEM_LOAD or op in X_MEM_LOAD:
            indexed = op in X_MEM_LOAD
            ea = self._ea(insn, indexed)
            size = D_MEM_LOAD[op] if op in D_MEM_LOAD else X_MEM_LOAD[op][0]
            value = self.mem.read_uint(ea, size)
            if op == "lha":
                value = sext(value, 16) & M32
            r[insn.rd] = value
            if op in UPDATE_FORMS:
                r[insn.ra] = ea
            return
        if op in D_MEM_STORE or op in X_MEM_STORE:
            indexed = op in X_MEM_STORE
            ea = self._ea(insn, indexed)
            size = D_MEM_STORE[op] if op in D_MEM_STORE else X_MEM_STORE[op]
            self.mem.write_uint(ea, size, r[insn.rd])
            if op in UPDATE_FORMS:
                r[insn.ra] = ea
            return
        if op == "lfs":
            raw = self.mem.read_uint(self._ea(insn), 4)
            f[insn.fd] = double_to_bits(struct.unpack(">f", raw.to_bytes(4, "big"))[0])
            return
        if op == "lfd":
            f[insn.fd] = self.mem.read_uint(self._ea(insn), 8)
            return
        if op == "stfs":
            value = round_single(bits_to_double(f[insn.fd]))
            self.mem.write_uint(self._ea(insn), 4, struct.unpack(">I", struct.pack(">f", value))[0])
            return
        if op == "stfd":
            self.mem.write_uint(self._ea(insn), 8, f[insn.fd])
            return
        if op in FP_BINARY or op in ("fadds", "fsubs", "fmuls", "fdivs"):
            a = bits_to_double(f[insn.fa])
            b = bits_to_double(f[insn.fb])
            c = bits_to_double(f[insn.fc])
            if op in FP_BINARY:
                value = FP_BINARY[op](a, b)
            else:
                value = round_single(FP_SINGLE[op](a, c, b))
            f[insn.fd] = double_to_bits(value)
            return
        if op in ("fmul", "fmadd", "fmsub", "fnmadd", "fnmsub"):
            a = bits_to_double(f[insn.fa])
            b = bits_to_double(f[insn.fb])
            c = bits_to_double(f[insn.fc])
            if op == "fmul":
                value = a * c
            elif op == "fmadd":
                value = fma(a, c, b)
            elif op == "fmsub":
                value = fma(a, c, -b)
            elif op == "fnmadd":
                value = -fma(a, c, b)
            else:
                value = -fma(a, c, -b)
            f[insn.fd] = double_to_bits(value)
            return
        if op == "fmr":
            f[insn.fd] = f[insn.fb]
            return
        if op == "fneg":
            f[insn.fd] = f[insn.fb] ^ SIGN64
            return
        if op == "fabs":
            f[insn.fd] = f[insn.fb] & ~SIGN64 & M64
            return
        if op == "fnabs":
            f[insn.fd] = f[insn.fb] | SIGN64
            return
        if op == "frsp":
            f[insn.fd] = double_to_bits(round_single(bits_to_double(f[insn.fb])))
            return
        if op == "fctiwz":
            f[insn.fd] = fctiwz_bits(bits_to_double(f[insn.fb]))
            return
        if op in ("fcmpu", "fcmpo"):
            self.cr = fp_cmp_field(self.cr, insn.crf,
                                   bits_to_double(f[insn.fa]), bits_to_double(f[insn.fb]))
            return
        raise GuestFault(f"interpreter has no rule for {op}")
