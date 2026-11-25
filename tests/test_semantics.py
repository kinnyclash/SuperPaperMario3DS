"""Operation semantics checked against independent references.

Each reference uses a different method from the implementation: bit-by-bit loops for masks
and shifts, an arithmetic identity for division, and exact rational checks for rounding.
"""
import math
import random
import struct
import unittest

from spm3ds import ppc
from spm3ds.ppc import (SINGLE_OVERFLOW, cmp_field, cntlzw, divw, divwu, extsb, extsh,
                        fctiwz_bits, fdiv, fp_cmp_field, mask32, round_single, rotl32, sraw,
                        slw, srw, s32)

M = 0xFFFFFFFF


def ref_mask(mb, me):
    """IBM bit numbering: bit i (from the left) is set when it lies in the selected range."""
    bits = []
    for i in range(32):
        if mb <= me:
            bits.append(mb <= i <= me)
        else:
            bits.append(i >= mb or i <= me)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def ref_rotl(x, n):
    bits = [(x >> (31 - i)) & 1 for i in range(32)]
    rotated = bits[n:] + bits[:n]
    value = 0
    for bit in rotated:
        value = (value << 1) | bit
    return value


class MaskAndRotate(unittest.TestCase):
    def test_mask_matches_bitwise_reference_for_all_bounds(self):
        for mb in range(32):
            for me in range(32):
                with self.subTest(mb=mb, me=me):
                    self.assertEqual(mask32(mb, me), ref_mask(mb, me))

    def test_rotl_matches_bitwise_reference(self):
        rng = random.Random(10)
        for _ in range(200):
            x, n = rng.getrandbits(32), rng.randrange(32)
            self.assertEqual(rotl32(x, n), ref_rotl(x, n))


class ShiftsAndDivision(unittest.TestCase):
    def test_slw_srw_match_shifts_below_32_and_zero_at_32_and_above(self):
        for amount in (0, 1, 5, 31, 32, 33, 63, 64, 95):
            for x in (0x12345678, 0x80000001, 0xFFFFFFFF):
                with self.subTest(amount=amount, x=x):
                    n = amount & 63
                    expected_left = 0 if n >= 32 else (x << n) & M
                    expected_right = 0 if n >= 32 else x >> n
                    self.assertEqual(slw(x, amount), expected_left)
                    self.assertEqual(srw(x, amount), expected_right)

    def test_sraw_sign_fills_when_shift_is_32_or_more(self):
        self.assertEqual(sraw(0x80000000, 40), M)
        self.assertEqual(sraw(0x7FFFFFFF, 40), 0)
        self.assertEqual(sraw(0xFFFFFFF0, 2), 0xFFFFFFFC)

    def test_divw_satisfies_truncating_division_identity(self):
        rng = random.Random(11)
        for _ in range(500):
            x, y = s32(rng.getrandbits(32)), s32(rng.getrandbits(32))
            if y == 0 or (x == -(1 << 31) and y == -1):
                continue
            q = s32(divw(x & M, y & M))
            r = x - q * y
            self.assertTrue(abs(r) < abs(y), (x, y, q))
            self.assertTrue(r == 0 or (r < 0) == (x < 0), (x, y, q))

    def test_divide_by_zero_and_overflow_give_zero(self):
        self.assertEqual(divw(7, 0), 0)
        self.assertEqual(divw(0x80000000, M), 0)
        self.assertEqual(divwu(7, 0), 0)

    def test_cntlzw_matches_loop_reference(self):
        for shift in range(33):
            x = (1 << (31 - shift)) if shift < 32 else 0
            expected = 0
            while expected < 32 and not (x << expected) & 0x80000000:
                expected += 1
            self.assertEqual(cntlzw(x), expected, hex(x))

    def test_sign_extensions(self):
        self.assertEqual(extsb(0x80), 0xFFFFFF80)
        self.assertEqual(extsb(0x7F), 0x7F)
        self.assertEqual(extsh(0x8000), 0xFFFF8000)
        self.assertEqual(extsh(0x1234), 0x1234)


class ConditionRegister(unittest.TestCase):
    def test_signed_fields_at_each_position(self):
        for crf in range(8):
            shift = 28 - 4 * crf
            lt = cmp_field(0, crf, -1, 0)
            gt = cmp_field(0, crf, 5, 2)
            eq = cmp_field(0, crf, 7, 7)
            self.assertEqual(lt >> shift & 0xF, 8)
            self.assertEqual(gt >> shift & 0xF, 4)
            self.assertEqual(eq >> shift & 0xF, 2)

    def test_other_fields_are_preserved(self):
        cr = 0xFFFFFFFF
        result = cmp_field(cr, 3, 1, 1)
        self.assertEqual(result & ~(0xF << 16) & M, cr & ~(0xF << 16) & M)

    def test_unordered_fp_compare_sets_so_bit(self):
        cr = fp_cmp_field(0, 0, math.nan, 1.0)
        self.assertEqual(cr >> 28, 1)


class FloatingPoint(unittest.TestCase):
    def test_round_single_result_is_a_representable_single(self):
        rng = random.Random(12)
        for _ in range(500):
            x = rng.uniform(-1e30, 1e30)
            rounded = round_single(x)
            self.assertEqual(struct.unpack(">f", struct.pack(">f", rounded))[0], rounded)

    def test_round_single_error_is_at_most_half_an_ulp(self):
        rng = random.Random(13)
        for _ in range(500):
            x = rng.uniform(-1000, 1000)
            rounded = round_single(x)
            ulp = 2.0 ** (math.frexp(x)[1] - 24) if x else 2.0 ** -149
            self.assertLessEqual(abs(rounded - x), ulp / 2 + 1e-300)

    def test_round_single_overflow_boundary(self):
        self.assertEqual(round_single(SINGLE_OVERFLOW), math.inf)
        self.assertEqual(round_single(-SINGLE_OVERFLOW), -math.inf)
        below = math.nextafter(SINGLE_OVERFLOW, 0.0)
        self.assertEqual(round_single(below), (2.0 - 2.0 ** -23) * 2.0 ** 127)

    def test_fdiv_special_cases(self):
        self.assertEqual(fdiv(1.0, 0.0), math.inf)
        self.assertEqual(fdiv(-1.0, 0.0), -math.inf)
        self.assertEqual(fdiv(1.0, -0.0), -math.inf)
        self.assertTrue(math.isnan(fdiv(0.0, 0.0)))

    def test_fctiwz_truncates_and_saturates(self):
        self.assertEqual(fctiwz_bits(3.9) & M, 3)
        self.assertEqual(fctiwz_bits(-3.9) & M, (-3) & M)
        self.assertEqual(fctiwz_bits(1e12) & M, 0x7FFFFFFF)
        self.assertEqual(fctiwz_bits(-1e12) & M, 0x80000000)
        self.assertEqual(fctiwz_bits(math.nan) & M, 0x80000000)
        self.assertEqual(fctiwz_bits(2.0) >> 32, 0xFFF80000)  # high word prefix

    def test_fma_is_single_rounding(self):
        # 1 + 2^-30 squared is 1 + 2^-29 + 2^-60. Fused: the 2^-60 term survives into the
        # addend, so the result differs from a separately rounded multiply and add.
        a = 1.0 + 2.0 ** -30
        fused = ppc.fma(a, a, -1.0)
        separate = a * a - 1.0
        self.assertNotEqual(fused, separate)
        self.assertAlmostEqual(fused, 2.0 ** -29 + 2.0 ** -60, places=25)


if __name__ == "__main__":
    unittest.main()
