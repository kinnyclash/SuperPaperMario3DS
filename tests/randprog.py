"""Random straight-line programs for differential testing (interpreter versus compiled C).

Constraints that keep the comparison well-defined:
  * r3 holds the data base address and is never written.
  * Memory accesses use r3 with aligned displacements inside the data area.
  * Indexed loads are excluded, because a random index could leave mapped memory.
  * FP values start finite and small, and FP operations never multiply or divide, so the
    results cannot overflow to infinity and produce NaN bit patterns that differ by platform.
"""
import random

from tests import helpers as h
from tests.helpers import DATA_BASE

DATA_SPAN = 256
LOGICAL_XO = {"and": 28, "andc": 60, "or": 444, "orc": 412, "xor": 316, "nor": 124,
              "nand": 476, "eqv": 284, "slw": 24, "srw": 536, "sraw": 792}


def _reg(rng, exclude=(3,)):
    while True:
        r = rng.randrange(32)
        if r not in exclude:
            return r


def _bit(rng):
    return rng.randrange(2)


def _int_ops(rng):
    """Each entry is a function that returns one instruction word."""
    def arith():
        name = rng.choice(["add", "subf", "mullw", "divw", "divwu"])
        d, a, b = _reg(rng), _reg(rng, ()), _reg(rng, ())
        if name == "add":
            return h.add(d, a, b, rc=_bit(rng))
        if name == "subf":
            return h.subf(d, a, b, rc=_bit(rng))
        if name == "mullw":
            return h.mullw(d, a, b)
        if name == "divw":
            return h.divw(d, a, b)
        return h.divwu(d, a, b)

    def logical():
        name = rng.choice(list(LOGICAL_XO))
        return h.xlog(LOGICAL_XO[name], _reg(rng), _reg(rng, ()), _reg(rng, ()), rc=_bit(rng))

    def immediates():
        kind = rng.randrange(10)
        d, a = _reg(rng), _reg(rng, ())
        simm = rng.randrange(-32768, 32768)
        uimm = rng.randrange(65536)
        if kind == 0:
            return h.addi(d, a, simm)
        if kind == 1:
            return h.addis(d, a, uimm)
        if kind == 2:
            return h.addic(d, a, simm, rc=bool(_bit(rng)))
        if kind == 3:
            return h.subfic(d, a, simm)
        if kind == 4:
            return h.mulli(d, a, simm)
        if kind == 5:
            return h.ori(_reg(rng), _reg(rng, ()), uimm)
        if kind == 6:
            return h.oris(_reg(rng), _reg(rng, ()), uimm)
        if kind == 7:
            return h.xori(_reg(rng), _reg(rng, ()), uimm)
        if kind == 8:
            return h.andi_dot(_reg(rng), _reg(rng, ()), uimm)
        return h.xori(_reg(rng), _reg(rng, ()), uimm)

    def rotates():
        kind = rng.randrange(3)
        a, s = _reg(rng), _reg(rng, ())
        mb, me = rng.randrange(32), rng.randrange(32)
        if kind == 0:
            return h.rlwinm(a, s, rng.randrange(32), mb, me, rc=_bit(rng))
        if kind == 1:
            return h.rlwimi(a, s, rng.randrange(32), mb, me, rc=_bit(rng))
        return h.rlwnm(a, s, _reg(rng, ()), mb, me, rc=_bit(rng))

    def unary():
        kind = rng.randrange(5)
        a, s = _reg(rng), _reg(rng, ())
        if kind == 0:
            return h.srawi(a, s, rng.randrange(32))
        if kind == 1:
            return h.cntlzw(a, s)
        if kind == 2:
            return h.extsb(a, s)
        if kind == 3:
            return h.extsh(a, s)
        return h.neg(_reg(rng), _reg(rng, ()))

    def compares():
        kind = rng.randrange(4)
        crf = rng.randrange(8)
        a = _reg(rng, ())
        if kind == 0:
            return h.cmpi(crf, a, rng.randrange(-32768, 32768))
        if kind == 1:
            return h.cmpli(crf, a, rng.randrange(65536))
        if kind == 2:
            return h.cmp_(crf, a, _reg(rng, ()))
        return h.cmpl(crf, a, _reg(rng, ()))

    def moves():
        return h.mfcr(_reg(rng))

    return [arith, logical, immediates, rotates, unary, compares, moves]


def _memory_ops(rng):
    def store():
        kind = rng.randrange(3)
        s = _reg(rng, ())
        if kind == 0:
            return h.stw(s, 3, rng.randrange(0, DATA_SPAN, 4))
        if kind == 1:
            return h.stb(s, 3, rng.randrange(0, DATA_SPAN))
        return h.sth(s, 3, rng.randrange(0, DATA_SPAN, 2))

    def load():
        kind = rng.randrange(4)
        d = _reg(rng)
        if kind == 0:
            return h.lwz(d, 3, rng.randrange(0, DATA_SPAN, 4))
        if kind == 1:
            return h.lbz(d, 3, rng.randrange(0, DATA_SPAN))
        if kind == 2:
            return h.lhz(d, 3, rng.randrange(0, DATA_SPAN, 2))
        return h.lha(d, 3, rng.randrange(0, DATA_SPAN, 2))

    return [store, load]


def _fp_ops(rng):
    def arith():
        name = rng.choice(["fadd", "fsub", "fadds", "fsubs"])
        d, a, b = rng.randrange(32), rng.randrange(32), rng.randrange(32)
        fn = {"fadd": h.fadd, "fsub": h.fsub, "fadds": h.fadds, "fsubs": h.fsubs}[name]
        return fn(d, a, b)

    def moves():
        kind = rng.randrange(4)
        d, b = rng.randrange(32), rng.randrange(32)
        if kind == 0:
            return h.fmr(d, b)
        if kind == 1:
            return h.fneg(d, b)
        if kind == 2:
            return h.fabs(d, b)
        return h.frsp(d, b)

    def compare():
        return h.fcmpu(rng.randrange(8), rng.randrange(32), rng.randrange(32))

    def store():
        if rng.random() < 0.5:
            return h.stfd(rng.randrange(32), 3, rng.randrange(0, DATA_SPAN, 8))
        return h.stfs(rng.randrange(32), 3, rng.randrange(0, DATA_SPAN, 4))

    return [arith, moves, compare, store]


def random_case(seed, length=260):
    """Return (words, init, dumps) for a reproducible random program."""
    rng = random.Random(seed)
    words = [h.addis(3, 0, DATA_BASE >> 16)]
    for reg in [0, 1, 2] + list(range(4, 32)):
        value = rng.getrandbits(32)
        words.append(h.addis(reg, 0, value >> 16))
        words.append(h.ori(reg, reg, value & 0xFFFF))

    init_doubles = b"".join(_double(rng) for _ in range(32))
    for f in range(32):
        words.append(h.lfd(f, 3, 8 * f))

    int_ops, mem_ops, fp_ops = _int_ops(rng), _memory_ops(rng), _fp_ops(rng)
    for _ in range(length):
        roll = rng.random()
        if roll < 0.55:
            words.append(rng.choice(int_ops)())
        elif roll < 0.75:
            words.append(rng.choice(mem_ops)())
        else:
            words.append(rng.choice(fp_ops)())
    words.append(h.BLR)
    init = [(DATA_BASE, init_doubles)]
    dumps = [(DATA_BASE, DATA_SPAN)]
    return words, init, dumps


def _double(rng):
    import struct
    return struct.pack(">d", rng.uniform(-4.0, 4.0))
