"""Hand-assembled programs: interpreter results, then interpreter/C equivalence."""
import unittest

from spm3ds.ppc import M32 as MASK32, bits_to_double
from tests.helpers import CODE_BASE, DATA_BASE, PROGRAMS, be_double
from tests.support import HAVE_GCC, assert_same_state, interpret, run_c

# Seed the callee of the indirect_call program so the dispatch table contains it.
SEEDS = {"indirect_call": (0x80003018,)}


class ProgramExpectations(unittest.TestCase):
    def test_interpreter_matches_hand_computed_values(self):
        for name, prog in PROGRAMS.items():
            with self.subTest(program=name):
                state = interpret(prog.words, prog.init, prog.dumps)
                for reg, value in prog.expect_r.items():
                    self.assertEqual(state["r"][reg], value & MASK32, f"r{reg}")
                for reg, value in prog.expect_f.items():
                    self.assertEqual(bits_to_double(state["f"][reg]), value, f"f{reg}")
                if prog.expect_cr is not None:
                    self.assertEqual(state["cr"], prog.expect_cr)

    def test_fp_store_writes_big_endian_double(self):
        prog = PROGRAMS["fp_double"]
        state = interpret(prog.words, prog.init, prog.dumps)
        expected = be_double(1.5 * (1.5 + 2.25) + 2.25)
        self.assertEqual(state["mem"][DATA_BASE + 16], expected)

    def test_ctr_loop_counts_down_to_zero(self):
        state = interpret(PROGRAMS["ctr_loop"].words)
        self.assertEqual(state["ctr"], 0)


@unittest.skipUnless(HAVE_GCC, "host gcc not available")
class NativeEquivalence(unittest.TestCase):
    def test_generated_c_matches_interpreter_for_every_program(self):
        for name, prog in PROGRAMS.items():
            with self.subTest(program=name):
                expected = interpret(prog.words, prog.init, prog.dumps)
                actual, _ = run_c(prog.words, prog.init, prog.dumps,
                                  seeds=SEEDS.get(name, ()))
                assert_same_state(self, expected, actual, label=name)


if __name__ == "__main__":
    unittest.main()
