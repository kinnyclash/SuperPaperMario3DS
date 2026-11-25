"""Indirect calls through ppc_dispatch with a large table (binary search over sorted entries)."""
import unittest

from tests import helpers as h
from tests.helpers import BLR, CODE_BASE
from tests.support import HAVE_GCC, assert_same_state, interpret, run_c

N = 50
LEAF0 = CODE_BASE + 0x1000          # leaf k at LEAF0 + 8k: r3 += k + 1; blr


def program(order):
    words = [h.mflr(31)]
    for k in order:
        target = LEAF0 + 8 * k
        words += [h.addis(5, 0, target >> 16), h.ori(5, 5, target & 0xFFFF), h.mtctr(5),
                  h.bcctr(20, 0, link=True)]
    words += [h.mtlr(31), BLR]
    words += [0] * ((LEAF0 - CODE_BASE) // 4 - len(words))
    for k in range(N):
        words += [h.addi(3, 3, k + 1), BLR]
    seeds = tuple(LEAF0 + 8 * k for k in range(N))
    return words, seeds


@unittest.skipUnless(HAVE_GCC, "host gcc not available")
class LargeDispatchTable(unittest.TestCase):
    def test_first_last_and_middle_entries(self):
        order = [0, N - 1, 25, 7, 42, 1, N - 2, 13]
        words, seeds = program(order)
        expected = interpret(words, (), ())
        self.assertEqual(expected["r"][3], sum(k + 1 for k in order))
        actual, stderr = run_c(words, (), (), seeds=seeds)
        self.assertEqual(stderr, "")
        assert_same_state(self, expected, actual, "dispatch")


if __name__ == "__main__":
    unittest.main()
