"""Differential tests on random straight-line programs: interpreter versus compiled C."""
import unittest

from tests.randprog import random_case
from tests.support import HAVE_GCC, assert_same_state, interpret, run_c


@unittest.skipUnless(HAVE_GCC, "host gcc not available")
class RandomDifferential(unittest.TestCase):
    SEEDS = range(1, 9)

    def test_interpreter_and_native_code_agree(self):
        for seed in self.SEEDS:
            with self.subTest(seed=seed):
                words, init, dumps = random_case(seed)
                expected = interpret(words, init, dumps)
                actual, stderr = run_c(words, init, dumps)
                self.assertEqual(stderr, "")
                assert_same_state(self, expected, actual, label=f"seed {seed}")

    def test_random_programs_exercise_every_family(self):
        # Guard against a generator that silently stops producing a family.
        from spm3ds.ppc import decode
        seen = set()
        for seed in self.SEEDS:
            words, _, _ = random_case(seed)
            for addr, w in enumerate(words):
                seen.add(decode(w, 0x80003000 + 4 * addr).op)
        for needed in ["add", "and", "rlwinm", "rlwimi", "srawi", "cntlzw", "lwz", "stw",
                       "lbz", "stb", "fadd", "fmr", "fcmpu", "stfd", "stfs", "lfd", "cmpi"]:
            with self.subTest(op=needed):
                self.assertIn(needed, seen)


if __name__ == "__main__":
    unittest.main()
