"""Function discovery, branch classification, problem reporting, and the tail-call regression."""
import os
import tempfile
import unittest

from spm3ds.analysis import CompileError, analyze, classify, sweep
from spm3ds.ppc import decode
from spm3ds.recomp import generate_c
from tests import helpers as h
from tests.helpers import CODE_BASE, program_image
from tests.support import HAVE_GCC, compile_c, run_c

FN2 = CODE_BASE + 0x100


class TailCallRegression(unittest.TestCase):
    """A plain `b` back to the function's own entry is a loop, not a tail call."""

    def setUp(self):
        self.words = [h.addi(3, 3, 1), h.b(-4)]  # loop: addi; b to own entry
        self.image = program_image(self.words, CODE_BASE)

    def test_classification_is_local(self):
        result = analyze(self.image, CODE_BASE)
        branch = decode(self.image[CODE_BASE + 4], CODE_BASE + 4)
        self.assertEqual(classify(branch, CODE_BASE, result.entries, frozenset()), "local")

    def test_body_contains_both_instructions(self):
        result = analyze(self.image, CODE_BASE)
        self.assertEqual(sorted(result.bodies[CODE_BASE]), [CODE_BASE, CODE_BASE + 4])

    def test_emitted_c_jumps_instead_of_recursing(self):
        source = generate_c(self.image, CODE_BASE)
        self.assertIn("goto L_80003000;", source)
        self.assertNotIn("fn_80003000(c); return;", source)

    @unittest.skipUnless(HAVE_GCC, "host gcc not available")
    def test_loop_terminates_natively(self):
        # 0: addi  1: cmpi r3,6  2: beq +16 (to the blr)  3: b -12 (back to entry, unconditional)
        words = [h.addi(3, 3, 1), h.cmpi(0, 3, 6), h.bc(12, 2, 8), h.b(-12), h.BLR]
        state, _ = run_c(words)
        self.assertEqual(state["r"][3], 6)


class TailCalls(unittest.TestCase):
    def setUp(self):
        self.words = [h.b(0x100), h.BLR]         # main tail-calls FN2
        self.image = program_image(self.words, CODE_BASE)
        self.image.update(program_image([h.addi(3, 0, 9), h.BLR], FN2))

    def test_seeded_callee_is_a_tail_call(self):
        result = analyze(self.image, CODE_BASE, seeds=[FN2])
        self.assertIn(FN2, result.entries)
        branch = decode(self.image[CODE_BASE], CODE_BASE)
        self.assertEqual(classify(branch, CODE_BASE, result.entries, frozenset()), "tail")
        self.assertNotIn(CODE_BASE + 4, result.bodies[CODE_BASE])

    def test_unseeded_uncalled_callee_is_compiled_inline(self):
        # Without a symbol or a call, the target cannot be told apart from a local jump.
        # The code is still correct: the callee's blr returns straight to our caller.
        result = analyze(self.image, CODE_BASE)
        self.assertNotIn(FN2, result.entries)
        self.assertIn(FN2, result.bodies[CODE_BASE])

    def test_call_is_not_tail(self):
        words = [h.bl(0x100), h.BLR]
        image = program_image(words, CODE_BASE)
        image.update(program_image([h.BLR], FN2))
        result = analyze(image, CODE_BASE)
        self.assertIn(CODE_BASE + 4, result.bodies[CODE_BASE])


class Sweep(unittest.TestCase):
    """The linear sweep reports every undecodable word, reachable or not."""

    def test_reports_every_unsupported_word_in_order(self):
        words = [h.addi(3, 0, 1), 0x04000000, h.addi(4, 0, 2), 0x04000004, h.BLR]
        failures = sweep(program_image(words, CODE_BASE))
        self.assertEqual([addr for addr, _, _ in failures], [CODE_BASE + 4, CODE_BASE + 12])
        self.assertEqual(failures[0][1], 0x04000000)

    def test_dead_code_is_swept_but_not_fatal_for_reachability(self):
        words = [h.b(8), 0x04000000, h.BLR]  # the unsupported word is jumped over
        image = program_image(words, CODE_BASE)
        self.assertEqual([addr for addr, _, _ in sweep(image)], [CODE_BASE + 4])
        self.assertEqual(analyze(image, CODE_BASE).fatal, [])


class Problems(unittest.TestCase):
    def test_unsupported_on_a_live_path_is_fatal(self):
        words = [h.addi(3, 0, 1), 0x04000000, h.BLR]
        result = analyze(program_image(words, CODE_BASE), CODE_BASE)
        self.assertEqual([p.kind for p in result.fatal], ["unsupported"])

    def test_indirect_branch_is_reported_but_not_fatal(self):
        words = [h.bcctr(20, 0), h.BLR]
        result = analyze(program_image(words, CODE_BASE), CODE_BASE)
        kinds = [p.kind for p in result.problems]
        self.assertIn("indirect", kinds)
        self.assertEqual(result.fatal, [])

    def test_unresolved_call_is_fatal(self):
        words = [h.bl(0x1000), h.BLR]
        result = analyze(program_image(words, CODE_BASE), CODE_BASE)
        self.assertTrue(any(p.kind == "unresolved_call" for p in result.fatal))

    def test_compile_refuses_and_names_the_address(self):
        words = [0x04000000, h.BLR]
        with self.assertRaises(CompileError) as ctx:
            generate_c(program_image(words, CODE_BASE), CODE_BASE)
        message = str(ctx.exception)
        self.assertIn("80003000", message)
        self.assertIn("unsupported", message)

    def test_external_symbol_is_not_an_unresolved_call(self):
        hle_addr = CODE_BASE + 0x200
        words = [h.bl(0x200), h.BLR]
        result = analyze(program_image(words, CODE_BASE), CODE_BASE, frozenset({hle_addr}))
        self.assertEqual(result.fatal, [])

    def test_missing_entry_point(self):
        result = analyze({}, CODE_BASE)
        self.assertTrue(any(p.kind == "missing" for p in result.problems))


class CommandLine(unittest.TestCase):
    def test_analyze_mode_reports_and_compile_mode_refuses(self):
        from spm3ds.recomp import main
        from tests.helpers import build_dol
        blob = build_dol([(CODE_BASE, [h.addi(3, 0, 1), 0x04000000, h.BLR])], CODE_BASE)
        with tempfile.TemporaryDirectory() as tmp:
            dol_path = os.path.join(tmp, "main.dol")
            with open(dol_path, "wb") as f:
                f.write(blob)
            self.assertEqual(main(["analyze", dol_path]), 0)
            self.assertEqual(main(["compile", dol_path, os.path.join(tmp, "out.c")]), 1)
            self.assertFalse(os.path.exists(os.path.join(tmp, "out.c")))


if __name__ == "__main__":
    unittest.main()
