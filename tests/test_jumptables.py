"""Declared jump tables: a switch compiled to a bctr through a table inside one function.

The table is written into guest memory as big-endian addresses, exactly as a compiler would
emit it. The interpreter and the compiled C must agree on every selector value.
"""
import unittest

from spm3ds.analysis import analyze
from spm3ds.ppc import GuestFault
from spm3ds.jumptables import JumpTableError, parse_jump_tables, resolve
from spm3ds.memory import MemoryMap
from tests.helpers import (BLR, CODE_BASE, DATA_BASE, addi, addis, b, bcctr, lwzx,
                           mtctr, program_image, rlwinm)
from tests.support import HAVE_GCC, assert_same_state, interpret, run_c

BCTR_AT = CODE_BASE + 5 * 4
CASES = [CODE_BASE + 6 * 4, CODE_BASE + 8 * 4, CODE_BASE + 10 * 4]  # labels of the three cases


def switch_words(selector):
    """A three-way switch on `selector`. Each case sets r3 and leaves through the end label."""
    return [
        addi(3, 0, selector),          # 0
        addis(12, 0, DATA_BASE >> 16),  # 1  r12 = table base
        rlwinm(4, 3, 2, 0, 29),        # 2  r4 = selector * 4
        lwzx(5, 12, 4),                # 3  r5 = table[selector]
        mtctr(5),                      # 4
        bcctr(20, 0),                  # 5  bctr
        addi(3, 0, 11),                # 6  case 0
        b(16),                         # 7  -> end
        addi(3, 0, 22),                # 8  case 1
        b(8),                          # 9  -> end
        addi(3, 0, 33),                # 10 case 2
        BLR,                           # 11 end
    ]


def table_bytes(targets):
    return b"".join(t.to_bytes(4, "big") for t in targets)


DECLARED = {BCTR_AT: CASES}
INIT = [(DATA_BASE, table_bytes(CASES))]
EXPECTED_R3 = [11, 22, 33]


class TableDispatch(unittest.TestCase):
    def test_each_selector_reaches_its_case_in_the_interpreter(self):
        for selector, expected in enumerate(EXPECTED_R3):
            with self.subTest(selector=selector):
                state = interpret(switch_words(selector), init=INIT, jump_targets=DECLARED)
                self.assertEqual(state["r"][3], expected)

    @unittest.skipUnless(HAVE_GCC, "gcc is not installed")
    def test_compiled_switch_matches_the_interpreter(self):
        for selector in range(3):
            with self.subTest(selector=selector):
                words = switch_words(selector)
                expected = interpret(words, init=INIT, jump_targets=DECLARED)
                native, _ = run_c(words, init=INIT, jump_targets=DECLARED)
                assert_same_state(self, expected, native, f"selector {selector}")

    def test_value_outside_the_declared_targets_is_rejected(self):
        # The table in memory has three entries, but only two are declared: selector 2
        # reaches an undeclared target, which must stop the interpreter.
        partial = {BCTR_AT: CASES[:2]}
        with self.assertRaises(GuestFault):
            interpret(switch_words(2), init=INIT, jump_targets=partial)


class TableAnalysis(unittest.TestCase):
    def test_declared_table_resolves_the_indirect_branch(self):
        image = program_image(switch_words(0), CODE_BASE)
        result = analyze(image, CODE_BASE, frozenset(), (), DECLARED)
        self.assertEqual(result.fatal, [])
        self.assertFalse([p for p in result.problems if p.kind == "indirect"])
        body = result.bodies[CODE_BASE]
        for target in CASES:
            self.assertIn(target, body, f"case {target:#010x} must be part of the function")

    def test_without_a_declaration_the_bctr_is_reported_as_indirect(self):
        image = program_image(switch_words(0), CODE_BASE)
        result = analyze(image, CODE_BASE)
        self.assertIn(BCTR_AT, [p.addr for p in result.problems if p.kind == "indirect"])

    def test_bctrl_with_a_table_is_rejected(self):
        words = switch_words(0)
        words[5] = bcctr(20, 0, link=True)
        image = program_image(words, CODE_BASE)
        result = analyze(image, CODE_BASE, frozenset(), (), DECLARED)
        self.assertTrue([p for p in result.fatal if p.kind == "unsupported"])


class DeclarationFile(unittest.TestCase):
    def test_parses_hex_addresses_and_comments(self):
        text = "# switch in main\nJUMPTABLE 0x80003014 80010000 3  # three cases\n"
        self.assertEqual(parse_jump_tables(text)[0].branch, 0x80003014)
        self.assertEqual(parse_jump_tables(text)[0].table, 0x80010000)
        self.assertEqual(parse_jump_tables(text)[0].count, 3)

    def test_rejects_malformed_lines_and_bad_counts(self):
        for text in ("JUMPTABLE 80003014 80010000", "TABLE 80003014 80010000 3",
                     "JUMPTABLE 8000zz14 80010000 3", "JUMPTABLE 80003014 80010000 0",
                     "JUMPTABLE 80003014 80010000 999999"):
            with self.subTest(text=text):
                with self.assertRaises(JumpTableError):
                    parse_jump_tables(text)

    def test_resolve_reads_entries_from_memory(self):
        memory = MemoryMap.wii_default(mem1_size=0x20000, mem2_size=0x1000)
        memory.write(DATA_BASE, table_bytes(CASES))
        decls = parse_jump_tables(f"JUMPTABLE {BCTR_AT:08x} {DATA_BASE:08x} 3\n")
        self.assertEqual(resolve(decls, lambda ea: memory.read_uint(ea, 4)), {BCTR_AT: CASES})

    def test_duplicate_declaration_for_one_bctr_is_rejected(self):
        decls = parse_jump_tables(f"JUMPTABLE {BCTR_AT:08x} {DATA_BASE:08x} 1\n"
                                  f"JUMPTABLE {BCTR_AT:08x} {DATA_BASE:08x} 2\n")
        with self.assertRaises(JumpTableError):
            resolve(decls, lambda ea: 0)


if __name__ == "__main__":
    unittest.main()
