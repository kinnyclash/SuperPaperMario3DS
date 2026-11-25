"""Carry (XER.CA), multiply-high, update-form memory and FP sign/compare instructions.

References do not reuse the implementation. 64-bit arithmetic is done with Python integers
and compared with the exact sum or difference. Multiply-high is read from the bytes of an
exact 64-bit product. Shift carries are checked on the bit string of the operand.
Each instruction is also compiled to C and compared with the interpreter where gcc exists.
"""
import random
import struct
import unittest

from spm3ds.ppc import Unsupported, decode
from tests.helpers import (BLR, DATA_BASE, addi, addis, be_double, enc_d, enc_x, enc_xo, lfd, lwz,
                           sraw, srawi)
from tests.support import HAVE_GCC, assert_same_state, interpret, run_c

M32 = 0xFFFFFFFF
M64 = (1 << 64) - 1
CA = 1 << 29


def s32(x):
    return x - (1 << 32) if x & 0x80000000 else x


def load_words(values):
    """Load each 32-bit value into r3.., from DATA_BASE. Returns the instructions and memory."""
    words = [addis(2, 0, DATA_BASE >> 16)]
    for i, _ in enumerate(values):
        words.append(lwz(3 + i, 2, 4 * i))
    return words, struct.pack(">" + "I" * len(values), *values)


def run(words, values, c=False):
    """Interpret (and optionally compile) the program: load values, run words, return state."""
    loads, data = load_words(values)
    program = loads + words + [BLR]
    init = [(DATA_BASE, data)]
    interp = interpret(program, init=init)
    if c:
        native, _ = run_c(program, init=init)
        assert_same_state(unittest.TestCase(), interp, native, "C vs interpreter")
    return interp


def rnd_values(rng, n):
    return [rng.getrandbits(32) for _ in range(n)]


class WideAddition(unittest.TestCase):
    """64-bit add: addc sets CA from the low words, adde adds the high words plus CA."""

    def test_addc_adde_match_exact_64_bit_sum(self):
        rng = random.Random(40)
        program = [enc_xo(31, 7, 4, 6, 0, 10), enc_xo(31, 8, 3, 5, 0, 138)]
        for i in range(120):
            vals = rnd_values(rng, 4)
            if i % 10 == 0:  # force carries through the low word
                vals[1] = M32 - vals[3] + 1 & M32
            a = (vals[0] << 32) | vals[1]
            b = (vals[2] << 32) | vals[3]
            total = a + b
            with self.subTest(a=hex(a), b=hex(b)):
                state = run(program, vals, c=(i < 3 and HAVE_GCC))
                self.assertEqual(state["r"][7], total & M32)
                self.assertEqual(state["r"][8], (total >> 32) & M32)
                self.assertEqual(state["xer"], CA if total > M64 else 0)

    def test_subfc_subfe_match_exact_64_bit_difference(self):
        rng = random.Random(41)
        program = [enc_xo(31, 7, 6, 4, 0, 8), enc_xo(31, 8, 5, 3, 0, 136)]
        for i in range(120):
            vals = rnd_values(rng, 4)
            a = (vals[0] << 32) | vals[1]
            b = (vals[2] << 32) | vals[3]
            with self.subTest(a=hex(a), b=hex(b)):
                state = run(program, vals, c=(i < 3 and HAVE_GCC))
                diff = (a - b) & M64
                self.assertEqual(state["r"][7], diff & M32)
                self.assertEqual(state["r"][8], diff >> 32)
                # CA is 1 when no borrow occurred, that is when a >= b.
                self.assertEqual(state["xer"], CA if a >= b else 0)


class ImmediateCarry(unittest.TestCase):
    def test_addic_sets_carry_from_unsigned_sum(self):
        rng = random.Random(42)
        for _ in range(100):
            a = rng.getrandbits(32)
            imm = rng.randrange(-32768, 32768)
            with self.subTest(a=hex(a), imm=imm):
                state = run([enc_d(12, 7, 3, imm)], [a])
                self.assertEqual(state["r"][7], (a + imm) & M32)
                self.assertEqual(state["xer"], CA if a + (imm & M32) > M32 else 0)

    def test_subfic_is_imm_minus_ra_with_carry_as_no_borrow(self):
        rng = random.Random(43)
        for _ in range(100):
            a = rng.getrandbits(32)
            imm = rng.randrange(-32768, 32768)
            with self.subTest(a=hex(a), imm=imm):
                state = run([enc_d(8, 7, 3, imm)], [a])
                self.assertEqual(state["r"][7], (imm - a) & M32)
                self.assertEqual(state["xer"], CA if (imm & M32) >= a else 0)


class CarryChains(unittest.TestCase):
    def test_addze_and_addme_use_carry_in(self):
        # addic r7, r3, 1 sets CA exactly when r3 is 0xFFFFFFFF. addze then adds CA to r4.
        rng = random.Random(44)
        for a in (0xFFFFFFFF, 0, 0x7FFFFFFF, rng.getrandbits(32)):
            for b in (0, 1, rng.getrandbits(32)):
                with self.subTest(a=hex(a), b=hex(b)):
                    ca = 1 if a == M32 else 0
                    state = run([enc_d(12, 7, 3, 1), enc_xo(31, 8, 4, 0, 0, 202)], [a, b])
                    self.assertEqual(state["r"][8], (b + ca) & M32)
                    state = run([enc_d(12, 7, 3, 1), enc_xo(31, 8, 4, 0, 0, 234)], [a, b])
                    self.assertEqual(state["r"][8], (b + ca - 1) & M32)

    def test_subfze_is_carry_minus_ra(self):
        rng = random.Random(45)
        for _ in range(60):
            a, b = rng.getrandbits(32), rng.getrandbits(32)
            with self.subTest(a=hex(a), b=hex(b)):
                ca = 1 if a + 1 > M32 else 0  # CA from addic r7, r3, 1
                state = run([enc_d(12, 7, 3, 1), enc_xo(31, 8, 4, 0, 0, 200)], [a, b])
                self.assertEqual(state["r"][8], ((~b & M32) + ca) & M32)


class ShiftCarry(unittest.TestCase):
    @staticmethod
    def ref_carry(a, n):
        """CA is set when the value is negative and a shifted-out bit is 1. Checked on bits."""
        if n == 0:
            return 0
        bits = format(a & M32, "032b")
        lost = bits[32 - min(n, 32):] if n < 32 else bits
        return 1 if (a & 0x80000000) and "1" in lost else 0

    def test_srawi_carry_matches_bit_string_reference(self):
        rng = random.Random(46)
        for sh in list(range(32)) + [0]:
            for a in (0x80000000, 0xFFFFFFFF, 0x80000001, 0x7FFFFFFF, 0, rng.getrandbits(32)):
                with self.subTest(a=hex(a), sh=sh):
                    state = run([srawi(3, 3, sh)], [a])  # rS = r3 (loaded value)
                    self.assertEqual(state["xer"], CA * self.ref_carry(a, sh))

    def test_sraw_register_amount_matches_reference(self):
        rng = random.Random(47)
        for _ in range(120):
            a = rng.getrandbits(32)
            n = rng.getrandbits(7)  # the shift uses the low 6 bits; bit 6 is ignored
            with self.subTest(a=hex(a), n=n):
                state = run([sraw(3, 3, 4)], [a, n])  # rS = r3 (value), rB = r4 (amount)
                amount = n & 63
                expected = ((-1 if a & 0x80000000 else 0) & M32) if amount >= 32 \
                    else (s32(a) >> amount) & M32
                self.assertEqual(state["r"][3], expected)
                self.assertEqual(state["xer"], CA * self.ref_carry(a, amount))


class MultiplyHigh(unittest.TestCase):
    EDGE = [0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x12345678]

    def test_mulhw_matches_high_bytes_of_exact_signed_product(self):
        rng = random.Random(48)
        pairs = [(a, b) for a in self.EDGE for b in self.EDGE]
        pairs += [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(60)]
        for a, b in pairs:
            with self.subTest(a=hex(a), b=hex(b)):
                prod = s32(a) * s32(b)
                expected = int.from_bytes(struct.pack(">q", prod)[:4], "big")
                state = run([enc_xo(31, 7, 3, 4, 0, 75)], [a, b])
                self.assertEqual(state["r"][7], expected)

    def test_mulhwu_matches_high_bytes_of_exact_unsigned_product(self):
        rng = random.Random(49)
        pairs = [(a, b) for a in self.EDGE for b in self.EDGE]
        pairs += [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(60)]
        for a, b in pairs:
            with self.subTest(a=hex(a), b=hex(b)):
                expected = int.from_bytes(struct.pack(">Q", a * b)[:4], "big")
                state = run([enc_xo(31, 7, 3, 4, 0, 11)], [a, b])
                self.assertEqual(state["r"][7], expected)


class UpdateFormMemory(unittest.TestCase):
    def test_lbzu_lhzu_load_and_update_base(self):
        data = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88])
        init = [(DATA_BASE, data)]
        program = [addis(2, 0, DATA_BASE >> 16), enc_d(35, 7, 2, 1), BLR]
        state = interpret(program, init=init)
        self.assertEqual(state["r"][7], 0x22)
        self.assertEqual(state["r"][2], DATA_BASE + 1)
        program = [addis(2, 0, DATA_BASE >> 16), enc_d(41, 7, 2, 2), BLR]
        state = interpret(program, init=init)
        self.assertEqual(state["r"][7], 0x3344)
        self.assertEqual(state["r"][2], DATA_BASE + 2)

    def test_stbu_sthu_store_and_update_base(self):
        program = [addis(2, 0, DATA_BASE >> 16), addi(7, 0, 0x1122),
                   enc_d(39, 7, 2, 3),   # stbu: stores 0x22 at +3, rA = +3
                   enc_d(45, 7, 2, 3),   # sthu: stores 0x1122 at +6, rA = +6
                   BLR]
        state = interpret(program, init=[(DATA_BASE, bytes(16))], dumps=[(DATA_BASE, 12)])
        self.assertEqual(state["r"][2], DATA_BASE + 6)
        self.assertEqual(state["mem"][DATA_BASE], b"\x00\x00\x00\x22\x00\x00\x11\x22\x00\x00\x00\x00")

    def test_invalid_update_forms_are_rejected(self):
        # lbzu with rA == rD, and stbu with rA == 0, are invalid forms.
        with self.assertRaises(Unsupported):
            decode(enc_d(35, 2, 2, 0), 0x80003000)
        with self.assertRaises(Unsupported):
            decode(enc_d(39, 7, 0, 4), 0x80003000)


class FloatingSignAndCompare(unittest.TestCase):
    def test_fnabs_sets_sign_bit_and_keeps_magnitude(self):
        values = [3.5, -2.0, 0.0, -0.0]
        data = b"".join(be_double(v) for v in values)
        for index, v in enumerate(values):
            with self.subTest(v=v):
                program = [addis(2, 0, DATA_BASE >> 16),
                           lfd(1, 2, 8 * index),
                           enc_x(63, 2, 0, 1, 136),  # fnabs f2, f1
                           BLR]
                state = interpret(program, init=[(DATA_BASE, data)])
                expected = struct.unpack(">Q", struct.pack(">d", -abs(v)))[0]
                self.assertEqual(state["f"][2], expected)

    def test_fcmpo_orders_like_fcmpu_for_ordered_values(self):
        data = be_double(3.5) + be_double(-2.0)
        program = [addis(2, 0, DATA_BASE >> 16), lfd(1, 2, 0), lfd(2, 2, 8),
                   enc_x(63, 0, 1, 2, 32),  # fcmpo cr0, f1, f2 : 3.5 > -2.0 -> GT
                   BLR]
        state = interpret(program, init=[(DATA_BASE, data)])
        self.assertEqual(state["cr"] >> 28, 4)


class RejectedForms(unittest.TestCase):
    def test_overflow_recording_forms_are_rejected(self):
        for xo in (10, 8, 138, 136, 75):
            with self.subTest(xo=xo):
                with self.assertRaises(Unsupported):
                    decode(enc_xo(31, 7, 3, 4, 1, xo), 0x80003000)


if __name__ == "__main__":
    unittest.main()
