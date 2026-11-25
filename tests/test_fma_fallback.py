"""The exact fused multiply-add fallback (spm3ds/ppc.py _fma_exact) used before Python 3.13."""
import math
import random
import struct
import unittest

from spm3ds import ppc


def bits(x):
    return struct.pack(">d", x)


class FmaFallback(unittest.TestCase):
    def test_single_rounding_known_case(self):
        # a*a - 1 with a = 1 + 2^-27: exact result 2^-26 + 2^-54. A separately rounded
        # multiply loses the 2^-54 term; a fused one keeps it.
        a = 1.0 + 2.0 ** -27
        self.assertEqual(ppc._fma_exact(a, a, -1.0), 2.0 ** -26 + 2.0 ** -54)

    def test_special_values(self):
        self.assertTrue(math.isnan(ppc._fma_exact(math.inf, 0.0, 1.0)))
        self.assertTrue(math.isnan(ppc._fma_exact(math.nan, 1.0, 1.0)))
        self.assertEqual(ppc._fma_exact(math.inf, 2.0, 1.0), math.inf)
        self.assertEqual(ppc._fma_exact(1e308, 10.0, 0.0), math.inf)
        self.assertEqual(ppc._fma_exact(-1e308, 10.0, 0.0), -math.inf)

    def test_zero_signs(self):
        self.assertEqual(bits(ppc._fma_exact(-0.0, 1.0, -0.0)), bits(-0.0))
        self.assertEqual(bits(ppc._fma_exact(0.0, 1.0, -0.0)), bits(0.0))
        self.assertEqual(bits(ppc._fma_exact(2.0, 3.0, -6.0)), bits(0.0))

    @unittest.skipUnless(hasattr(math, "fma"), "math.fma needs Python 3.13")
    def test_matches_math_fma(self):
        rng = random.Random(1234)
        for _ in range(5000):
            a = rng.uniform(-1e6, 1e6) * 2.0 ** rng.randint(-60, 60)
            c = rng.uniform(-1e6, 1e6) * 2.0 ** rng.randint(-60, 60)
            b = rng.uniform(-1e6, 1e6) * 2.0 ** rng.randint(-60, 60)
            self.assertEqual(bits(ppc._fma_exact(a, c, b)), bits(math.fma(a, c, b)), (a, c, b))


if __name__ == "__main__":
    unittest.main()
