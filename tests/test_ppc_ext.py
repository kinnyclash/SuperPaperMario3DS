"""Extended PowerPC instructions (spm3ds/ppc_ext.py).

Three kinds of check:
  * hand-computed results for each instruction family, run through the interpreter;
  * the same programs compiled to C and compared with the interpreter;
  * random integer and memory programs, and fixed floating-point operand vectors, compared
    between the interpreter and compiled C.

Encoders here are written from the instruction field layouts, independently of the decoder.
"""
import random
import struct
import unittest

from spm3ds.ppc import Unsupported, decode
from tests import helpers as h
from tests.helpers import BLR, DATA_BASE, enc_a, enc_d, enc_x, enc_xo, spr_field
from tests.support import HAVE_GCC, assert_same_state, interpret, run_c

SO, OV, CA = 1 << 31, 1 << 30, 1 << 29
M32 = 0xFFFFFFFF


# ---- encoders ---------------------------------------------------------------------------

def li32(reg, value):
    value &= M32
    return [h.addis(reg, 0, value >> 16), h.ori(reg, reg, value & 0xFFFF)]


def xo(name, d, a, b=0, oe=1, rc=0):
    code = {"add": 266, "subf": 40, "mullw": 235, "divw": 491, "divwu": 459, "neg": 104,
            "addc": 10, "subfc": 8, "adde": 138, "subfe": 136, "addze": 202, "subfze": 200,
            "addme": 234, "subfme": 232}[name]
    return enc_xo(31, d, a, b, oe, code, rc)


def crop(name, d, a, b):
    code = {"crand": 257, "crandc": 129, "creqv": 289, "crnand": 225, "crnor": 33,
            "cror": 449, "crorc": 417, "crxor": 193}[name]
    return enc_x(19, d, a, b, code)


def mtcrf(crm, s):
    return (31 << 26) | (s << 21) | (crm << 12) | (144 << 1)


def mcrf(dst, src):
    return (19 << 26) | (dst << 23) | (src << 18)


def mcrxr(crf):
    return (31 << 26) | (crf << 23) | (512 << 1)


def mfspr(d, spr):
    return (31 << 26) | (d << 21) | (spr_field(spr) << 11) | (339 << 1)


def mtspr(spr, s):
    return (31 << 26) | (s << 21) | (spr_field(spr) << 11) | (467 << 1)


def xmem(xo10, d, a, b):
    return enc_x(31, d, a, b, xo10)


XMEM = {"lwbrx": 534, "lhbrx": 790, "stwbrx": 662, "sthbrx": 918, "lhax": 343, "lhaux": 375,
        "lwzux": 55, "lbzux": 119, "lhzux": 311, "stwux": 183, "stbux": 247, "sthux": 439,
        "lfsx": 535, "lfsux": 567, "lfdx": 599, "lfdux": 631, "stfsx": 663, "stfsux": 695,
        "stfdx": 727, "stfdux": 759, "stfiwx": 983, "dcbz": 1014, "dcbf": 86, "sync": 598}


def lmw(d, a, imm):
    return enc_d(46, d, a, imm)


def stmw(s, a, imm):
    return enc_d(47, s, a, imm)


def fpx(xo10, d, a, b, op=63):
    return enc_x(op, d, a, b, xo10)


# ---- hand-computed results --------------------------------------------------------------

class Overflow(unittest.TestCase):
    def run_prog(self, words, init=(), dumps=()):
        return interpret(words + [BLR], init, dumps)

    def test_addo_sets_ov_and_sticky_so(self):
        words = li32(4, 0x7FFFFFFF) + li32(5, 1) + [xo("add", 6, 4, 5)]
        st = self.run_prog(words)
        self.assertEqual(st["r"][6], 0x80000000)
        self.assertEqual(st["xer"], SO | OV)

    def test_ov_clears_but_so_stays(self):
        words = li32(4, 0x7FFFFFFF) + li32(5, 1) + [xo("add", 6, 4, 5), xo("add", 7, 5, 5, rc=1)]
        st = self.run_prog(words)
        self.assertEqual(st["r"][7], 2)
        self.assertEqual(st["xer"], SO)
        self.assertEqual(st["cr"] >> 28, 0b0101)  # GT, and SO copied from XER

    def test_compare_copies_so(self):
        words = li32(4, 0x7FFFFFFF) + li32(5, 1) + [xo("add", 6, 4, 5), h.cmpi(3, 5, 1)]
        st = self.run_prog(words)
        self.assertEqual((st["cr"] >> (28 - 12)) & 0xF, 0b0011)  # EQ | SO in cr3

    def test_subfo(self):
        # 0 - 0x80000000 overflows; 5 - 3 does not.
        words = li32(4, 0x80000000) + li32(5, 0) + [xo("subf", 6, 4, 5)]
        self.assertEqual(self.run_prog(words)["xer"] & OV, OV)
        words = li32(4, 3) + li32(5, 5) + [xo("subf", 6, 4, 5)]
        st = self.run_prog(words)
        self.assertEqual((st["r"][6], st["xer"]), (2, 0))

    def test_nego_mullwo_divwo_divwuo(self):
        cases = [
            (li32(4, 0x80000000) + [xo("neg", 6, 4)], 0x80000000, OV),
            (li32(4, 5) + [xo("neg", 6, 4)], (-5) & M32, 0),
            (li32(4, 0x10000) + li32(5, 0x10000) + [xo("mullw", 6, 4, 5)], 0, OV),
            (li32(4, 0xFFFFFFFF) + li32(5, 7) + [xo("mullw", 6, 4, 5)], (-7) & M32, 0),
            (li32(4, 9) + li32(5, 0) + [xo("divw", 6, 4, 5)], None, OV),
            (li32(4, 0x80000000) + li32(5, 0xFFFFFFFF) + [xo("divw", 6, 4, 5)], None, OV),
            (li32(4, 9) + li32(5, 2) + [xo("divw", 6, 4, 5)], 4, 0),
            (li32(4, 9) + li32(5, 0) + [xo("divwu", 6, 4, 5)], None, OV),
        ]
        for words, value, ov in cases:
            with self.subTest(words=[hex(w) for w in words]):
                st = self.run_prog(words)
                if value is not None:
                    self.assertEqual(st["r"][6], value)
                self.assertEqual(st["xer"] & OV, ov)

    def test_carry_and_overflow_together(self):
        # addco 0xFFFFFFFF + 1: carry out, no signed overflow (-1 + 1 = 0).
        st = self.run_prog(li32(4, 0xFFFFFFFF) + li32(5, 1) + [xo("addc", 6, 4, 5)])
        self.assertEqual((st["r"][6], st["xer"]), (0, CA))
        # addeo 0x7FFFFFFF + 0 + CA(1): overflow.
        words = (li32(4, 0xFFFFFFFF) + li32(5, 1) + [xo("addc", 6, 4, 5, oe=0)]
                 + li32(7, 0x7FFFFFFF) + li32(8, 0) + [xo("adde", 9, 7, 8)])
        st = self.run_prog(words)
        self.assertEqual(st["r"][9], 0x80000000)
        self.assertEqual(st["xer"], SO | OV)

    def test_mcrxr_moves_and_clears(self):
        words = li32(4, 0x7FFFFFFF) + li32(5, 1) + [xo("add", 6, 4, 5), mcrxr(2)]
        st = self.run_prog(words)
        self.assertEqual((st["cr"] >> 20) & 0xF, 0b1100)  # SO, OV
        self.assertEqual(st["xer"], 0)

    def test_xer_spr_round_trip(self):
        words = li32(4, 0xE000007F) + [mtspr(1, 4), mfspr(5, 1)]
        st = self.run_prog(words)
        self.assertEqual(st["r"][5], 0xE000007F)
        self.assertEqual(st["xer"], SO | OV | CA)


class ConditionRegister(unittest.TestCase):
    def test_mtcrf_writes_selected_fields(self):
        words = li32(4, 0x12345678) + [mtcrf(0x81, 4), BLR]  # fields 0 and 7
        self.assertEqual(interpret(words)["cr"], 0x10000008)

    def test_mcrf(self):
        words = li32(4, 0xA0000000) + [mtcrf(0xFF, 4), mcrf(5, 0), BLR]
        self.assertEqual(interpret(words)["cr"], 0xA0000A00)

    def test_logic_ops(self):
        # cr bits 0..3 = 1,0,1,0 (0xA in field 0)
        base = li32(4, 0xA0000000) + [mtcrf(0xFF, 4)]
        expect = {"crand": (0, 2, 1), "crand_": (0, 1, 0), "cror": (1, 3, 0),
                  "crxor": (0, 2, 0), "crnor": (1, 3, 1), "crnand": (0, 2, 0),
                  "creqv": (1, 3, 1), "crandc": (0, 1, 1), "crorc": (1, 3, 1)}
        for name, (a, b, result) in expect.items():
            op = name.rstrip("_")
            with self.subTest(op=name):
                st = interpret(base + [crop(op, 31, a, b), BLR])
                self.assertEqual(st["cr"] & 1, result)


class MemoryForms(unittest.TestCase):
    INIT = [(DATA_BASE, bytes(range(1, 65)))]
    DUMP = [(DATA_BASE, 64)]

    def prog(self, body):
        return li32(3, DATA_BASE) + body + [BLR]

    def test_byte_reversed(self):
        words = self.prog(li32(4, 8) + [xmem(XMEM["lwbrx"], 5, 3, 4), xmem(XMEM["lhbrx"], 6, 3, 4)]
                          + li32(7, 0xAABBCCDD) + li32(8, 16)
                          + [xmem(XMEM["stwbrx"], 7, 3, 8)]
                          + li32(9, 20) + [xmem(XMEM["sthbrx"], 7, 3, 9)])
        st = interpret(words, self.INIT, self.DUMP)
        self.assertEqual(st["r"][5], 0x0C0B0A09)
        self.assertEqual(st["r"][6], 0x0A09)
        mem = st["mem"][DATA_BASE]
        self.assertEqual(mem[16:20], bytes([0xDD, 0xCC, 0xBB, 0xAA]))
        self.assertEqual(mem[20:22], bytes([0xDD, 0xCC]))

    def test_lmw_stmw(self):
        words = self.prog([lmw(28, 3, 0), stmw(28, 3, 32)])
        st = interpret(words, self.INIT, self.DUMP)
        self.assertEqual(st["r"][28], 0x01020304)
        self.assertEqual(st["r"][31], 0x0D0E0F10)
        self.assertEqual(st["mem"][DATA_BASE][32:48], bytes(range(1, 17)))

    def test_dcbz_zeroes_aligned_block(self):
        words = self.prog(li32(4, 37) + [xmem(XMEM["dcbz"], 0, 3, 4)])
        mem = interpret(words, self.INIT, self.DUMP)["mem"][DATA_BASE]
        self.assertEqual(mem[:32], bytes(range(1, 33)))
        self.assertEqual(mem[32:64], bytes(32))

    def test_update_and_algebraic(self):
        words = self.prog(li32(4, 4) + [h.addi(5, 3, 0), xmem(XMEM["lhaux"], 6, 5, 4)])
        init = [(DATA_BASE + 4, b"\x80\x01")]
        st = interpret(words, init, self.DUMP)
        self.assertEqual(st["r"][6], 0xFFFF8001)
        self.assertEqual(st["r"][5], DATA_BASE + 4)

    def test_fp_indexed_and_stfiwx(self):
        init = [(DATA_BASE, struct.pack(">f", 1.5) + bytes(12) + struct.pack(">d", -2.25))]
        words = self.prog(li32(4, 0) + li32(5, 16)
                          + [xmem(XMEM["lfsx"], 1, 3, 4), xmem(XMEM["lfdx"], 2, 3, 5),
                             fpx(14, 3, 0, 2)]  # fctiw f3 = -2 (ties to even)
                          + li32(6, 32) + [xmem(XMEM["stfiwx"], 3, 3, 6)])
        st = interpret(words, init, self.DUMP)
        self.assertEqual(st["f"][1], struct.unpack(">Q", struct.pack(">d", 1.5))[0])
        self.assertEqual(st["mem"][DATA_BASE][32:36], struct.pack(">i", -2))

    def test_invalid_forms_rejected(self):
        with self.assertRaises(Unsupported):
            decode(xmem(XMEM["lwzux"], 5, 0, 4), 0)  # update with rA = 0
        with self.assertRaises(Unsupported):
            decode(xmem(XMEM["lwzux"], 5, 5, 4), 0)  # update load with rA = rD
        with self.assertRaises(Unsupported):
            decode(lmw(10, 12, 0), 0)                # rA inside the loaded range


def f64(x):
    return struct.unpack(">Q", struct.pack(">d", x))[0]


def pair(a, b):
    return struct.unpack(">Q", struct.pack(">ff", a, b))[0]


class FloatingPoint(unittest.TestCase):
    def run_fp(self, values, body):
        init = [(DATA_BASE, b"".join(struct.pack(">d", v) for v in values))]
        words = li32(3, DATA_BASE) + [h.lfd(i + 1, 3, 8 * i) for i in range(len(values))]
        return interpret(words + body + [BLR], init)

    def test_fsel(self):
        st = self.run_fp([1.0, 2.0, 3.0, -1.0], [enc_a(63, 10, 1, 3, 2, 23),
                                                 enc_a(63, 11, 4, 3, 2, 23)])
        self.assertEqual(st["f"][10], f64(2.0))  # 1.0 >= 0 -> frC
        self.assertEqual(st["f"][11], f64(3.0))  # -1.0 < 0 -> frB

    def test_fctiw_rounds_half_to_even(self):
        for x, want in [(2.5, 2), (3.5, 4), (-2.5, -2), (1e12, 0x7FFFFFFF), (-1e12, -0x80000000)]:
            with self.subTest(x=x):
                st = self.run_fp([x], [fpx(14, 10, 0, 1)])
                self.assertEqual(st["f"][10] & M32, want & M32)

    def test_reciprocals(self):
        st = self.run_fp([4.0, -1.0], [enc_a(59, 10, 0, 1, 0, 24), enc_a(63, 11, 0, 1, 0, 26),
                                       enc_a(63, 12, 0, 2, 0, 26)])
        self.assertEqual(st["f"][10], f64(0.25))
        self.assertEqual(st["f"][11], f64(0.5))
        self.assertEqual(st["f"][12], 0x7FF8000000000000)

    def test_fused_single(self):
        st = self.run_fp([1.5, 2.0, 0.25], [enc_a(59, 10, 1, 3, 2, 29)])  # 1.5*2 + 0.25
        self.assertEqual(st["f"][10], f64(3.25))

    def test_fpscr_moves(self):
        words = [(63 << 26) | (5 << 23) | (0x9 << 12) | (134 << 1),  # mtfsfi 5, 9
                 (63 << 26) | (31 << 21) | (38 << 1),                 # mtfsb1 31
                 (63 << 26) | (10 << 21) | (583 << 1),                # mffs f10
                 (63 << 26) | (2 << 23) | (5 << 18) | (64 << 1)]      # mcrfs cr2, 5
        st = interpret(words + [BLR])
        self.assertEqual(st["f"][10], 0xFFF8000000000000 | 0x00000901)
        self.assertEqual((st["cr"] >> 20) & 0xF, 9)


class PairedExtras(unittest.TestCase):
    def run_ps(self, pairs, body):
        init = [(DATA_BASE, b"".join(struct.pack(">ff", a, b) for a, b in pairs))]
        words = li32(3, DATA_BASE) + [h.lfd(i + 1, 3, 8 * i) for i in range(len(pairs))]
        return interpret(words + body + [BLR], init)

    def test_sum_muls_madds_sel(self):
        pairs = [(1.0, 2.0), (3.0, 4.0), (5.0, -6.0)]  # f1, f2, f3
        st = self.run_ps(pairs, [
            enc_a(4, 10, 1, 2, 3, 10),  # ps_sum0: (1 + 4, f3.ps1 = -6)
            enc_a(4, 11, 1, 2, 3, 11),  # ps_sum1: (f3.ps0 = 5, 1 + 4)
            enc_a(4, 12, 1, 0, 3, 13),  # ps_muls1: (1 * -6, 2 * -6)
            enc_a(4, 13, 1, 2, 3, 14),  # ps_madds0: (1*5 + 3, 2*5 + 4)
            enc_a(4, 14, 3, 1, 2, 23),  # ps_sel: (5 >= 0 -> 3, -6 < 0 -> 2)
        ])
        self.assertEqual(st["f"][10], pair(5.0, -6.0))
        self.assertEqual(st["f"][11], pair(5.0, 5.0))
        self.assertEqual(st["f"][12], pair(-6.0, -12.0))
        self.assertEqual(st["f"][13], pair(8.0, 14.0))
        self.assertEqual(st["f"][14], pair(3.0, 2.0))

    def test_compare_lanes(self):
        st = self.run_ps([(1.0, 9.0), (2.0, 3.0)], [
            enc_x(4, 1 << 2, 1, 2, 0),    # ps_cmpu0 cr1: 1 < 2 -> LT
            enc_x(4, 2 << 2, 1, 2, 64),   # ps_cmpu1 cr2: 9 > 3 -> GT
        ])
        self.assertEqual((st["cr"] >> 24) & 0xF, 8)
        self.assertEqual((st["cr"] >> 20) & 0xF, 4)

    def test_indexed_quantized_load_store(self):
        # psq_lx f5, r3, r4 with GQR0 = 0 (float) and W = 0; then psq_stx to offset 16.
        def psqx(xo6, d, a, b, w=0, i=0):
            return (4 << 26) | (d << 21) | (a << 16) | (b << 11) | (w << 10) | (i << 7) | (xo6 << 1)
        words = (li32(3, DATA_BASE) + li32(4, 8) + [psqx(6, 5, 3, 4)] + li32(6, 16)
                 + [psqx(7, 5, 3, 6), BLR])
        init = [(DATA_BASE + 8, struct.pack(">ff", 1.25, -0.5))]
        st = interpret(words, init, [(DATA_BASE + 16, 8)])
        self.assertEqual(st["f"][5], pair(1.25, -0.5))
        self.assertEqual(st["mem"][DATA_BASE + 16], struct.pack(">ff", 1.25, -0.5))


# ---- interpreter versus compiled C ------------------------------------------------------

def random_ext_case(seed, length=180):
    """Random programs mixing overflow forms, CR logic, XER moves and memory forms.

    r3 holds the data base, r4 holds an aligned index; neither is written by the random ops.
    Update forms first load their base register with a fresh aligned data address."""
    rng = random.Random(seed)
    words = li32(3, DATA_BASE) + li32(4, 8)
    for reg in range(5, 32):
        words += li32(reg, rng.getrandbits(32))

    def reg():
        return rng.randrange(5, 32)

    names = ["add", "subf", "mullw", "divw", "divwu", "neg", "addc", "subfc", "adde",
             "subfe", "addze", "subfze", "addme", "subfme"]
    for _ in range(length):
        k = rng.randrange(12)
        if k <= 3:
            words.append(xo(rng.choice(names), reg(), reg(), reg(), oe=1, rc=rng.randrange(2)))
        elif k == 4:
            words.append(crop(rng.choice(["crand", "crandc", "creqv", "crnand", "crnor",
                                          "cror", "crorc", "crxor"]),
                              rng.randrange(32), rng.randrange(32), rng.randrange(32)))
        elif k == 5:
            words.append(rng.choice([mtcrf(rng.randrange(256), reg()),
                                     mcrf(rng.randrange(8), rng.randrange(8)),
                                     mcrxr(rng.randrange(8)),
                                     h.cmp_(rng.randrange(8), reg(), reg())]))
        elif k == 6:
            words.append(rng.choice([mtspr(1, reg()), mfspr(reg(), 1)]))
        elif k == 7:
            name = rng.choice(["lwbrx", "lhbrx", "lhax"])
            words.append(xmem(XMEM[name], reg(), 3, 4))
        elif k == 8:
            name = rng.choice(["stwbrx", "sthbrx"])
            words.append(xmem(XMEM[name], reg(), 3, 4))
        elif k == 9:
            base = reg()
            dst = reg()
            while dst == base:
                dst = reg()
            words += li32(base, DATA_BASE + rng.randrange(0, 128, 4))
            name = rng.choice(["lwzux", "lbzux", "lhzux", "lhaux", "stwux", "stbux", "sthux"])
            words.append(xmem(XMEM[name], dst, base, 4))
        elif k == 10:
            first = rng.randrange(24, 32)
            words.append(rng.choice([lmw, stmw])(first, 3, rng.randrange(0, 128, 4)))
        else:
            words.append(xmem(XMEM["dcbz"], 0, 3, 4))
    words.append(BLR)
    init = [(DATA_BASE, bytes(rng.getrandbits(8) for _ in range(256)))]
    return words, init, [(DATA_BASE, 256)]


FP_VALUES = [1.5, -2.25, 0.75, 3.0, -0.0, 0.0, 1e-3, 7.0]
PAIR_VALUES = [(1.5, -2.0), (0.25, 3.0), (-1.0, 0.5), (2.0, -0.0)]


def vector_case():
    """One straight-line program that applies every new FP and paired op to fixed values."""
    init = [(DATA_BASE, b"".join(struct.pack(">d", v) for v in FP_VALUES)),
            (DATA_BASE + 64, b"".join(struct.pack(">ff", a, b) for a, b in PAIR_VALUES))]
    words = li32(3, DATA_BASE)
    words += [h.lfd(1 + i, 3, 8 * i) for i in range(len(FP_VALUES))]           # f1..f8
    words += [h.lfd(20 + i, 3, 64 + 8 * i) for i in range(len(PAIR_VALUES))]   # f20..f23
    out = 9
    for a, b, c in [(1, 2, 3), (2, 4, 1), (3, 5, 6), (7, 8, 1)]:
        for x5 in (28, 29, 30, 31):
            words.append(enc_a(59, out % 11 + 9, a, b, c, x5))
            out += 1
        words.append(enc_a(63, 19, a, b, c, 23))       # fsel
        words.append(enc_a(59, 18, 0, a, 0, 24))       # fres
        words.append(enc_a(63, 17, 0, b, 0, 26))       # frsqrte
        words.append(fpx(14, 16, 0, c))                # fctiw
        words.append(h.stfd(16, 3, 128))
    for a, b, c in [(20, 21, 22), (21, 23, 20), (22, 20, 23)]:
        for x5 in (10, 11, 12, 13, 14, 15, 23, 24, 26):
            words.append(enc_a(4, 24 + x5 % 8, a, b, c, x5))
        for x10 in (0, 32, 64, 96):
            words.append(enc_x(4, (x10 // 32) << 2, a, b, x10))
    words.append(BLR)
    return words, init, [(DATA_BASE + 128, 8)]


@unittest.skipUnless(HAVE_GCC, "host gcc not available")
class NativeEquivalence(unittest.TestCase):
    def check(self, words, init=(), dumps=(), label=""):
        expected = interpret(words, init, dumps)
        actual, stderr = run_c(words, init, dumps)
        self.assertEqual(stderr, "")
        assert_same_state(self, expected, actual, label)

    def test_random_integer_and_memory_programs(self):
        for seed in range(1, 7):
            with self.subTest(seed=seed):
                words, init, dumps = random_ext_case(seed)
                self.check(words, init, dumps, f"seed {seed}")

    def test_fp_and_paired_vectors(self):
        words, init, dumps = vector_case()
        self.check(words, init, dumps, "vectors")

    def test_hand_programs_compile_identically(self):
        # The hand-checked memory programs, compiled.
        t = MemoryForms()
        for body in ([lmw(28, 3, 0), stmw(28, 3, 32)],
                     li32(4, 37) + [xmem(XMEM["dcbz"], 0, 3, 4)],
                     li32(4, 8) + [xmem(XMEM["lwbrx"], 5, 3, 4), xmem(XMEM["sthbrx"], 5, 3, 4)]):
            self.check(t.prog(body), t.INIT, t.DUMP)

    def test_random_programs_cover_the_families(self):
        seen = set()
        for seed in range(1, 7):
            words, _, _ = random_ext_case(seed)
            for i, w in enumerate(words):
                seen.add(decode(w, 0x80003000 + 4 * i).op)
        for op in ("addo", "nego", "divwo", "crxor", "mtcrf", "mcrf", "mcrxr", "mtspr_x",
                   "lwbrx", "stwbrx", "lhaux", "stwux", "lmw", "stmw", "dcbz"):
            with self.subTest(op=op):
                self.assertIn(op, seen)


if __name__ == "__main__":
    unittest.main()
