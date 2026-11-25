"""Automatic jump-table detection (spm3ds/jumpdetect.py).

Programs are built with the test encoders. The switch has three cases and a default; the
table lives at DATA_BASE+16 and holds absolute case addresses. Each check states the
expected result by hand: the detected base, the count, and the targets.
"""
import struct
import unittest

from spm3ds.jumpdetect import detect
from tests.helpers import (BLR, CODE_BASE, DATA_BASE, NOP, addi, addis, bc, bcctr, cmpli, lwz, lwzx,
                           mtctr, rlwinm)
from tests.support import HAVE_GCC, assert_same_state, interpret, run_c

TABLE = DATA_BASE + 16


def switch_words(bound_check=True):
    """0: addis r2 (DATA) 1: lwz r3 (index at DATA+0) 2: addi r2,16 3: cmpli r3,2 ...
    Cases: index 0 -> 10, 1 -> 20, 2 -> 30; otherwise 99 (default at word 15)."""
    return [
        addis(2, 0, DATA_BASE >> 16),                      # 0
        lwz(3, 2, 0),                                      # 1
        addi(2, 2, 16),                                    # 2
        cmpli(0, 3, 2) if bound_check else NOP,            # 3
        bc(12, 1, (15 - 4) * 4),                           # 4  bgt -> default
        rlwinm(4, 3, 2, 0, 29),                            # 5  index * 4
        lwzx(5, 2, 4),                                     # 6  r5 = table[index]
        mtctr(5),                                          # 7
        bcctr(20, 0),                                      # 8
        addi(3, 0, 10), BLR,                               # 9, 10
        addi(3, 0, 20), BLR,                               # 11, 12
        addi(3, 0, 30), BLR,                               # 13, 14
        addi(3, 0, 99), BLR,                               # 15, 16
    ]


CASE_TARGETS = [CODE_BASE + 4 * 9, CODE_BASE + 4 * 11, CODE_BASE + 4 * 13]
TABLE_BYTES = struct.pack(">3I", *CASE_TARGETS)


def read_word_from(table_bytes, base):
    def read(address):
        off = address - base
        if 0 <= off and off + 4 <= len(table_bytes):
            return struct.unpack(">I", table_bytes[off:off + 4])[0]
        return None
    return read


def text_range(words):
    return (CODE_BASE, CODE_BASE + 4 * len(words))


class JumpDetectTests(unittest.TestCase):
    def detect_with_table(self, words):
        read = read_word_from(TABLE_BYTES, TABLE)
        return detect(words, CODE_BASE, read, text_range(words))

    def test_switch_is_detected_with_base_count_and_targets(self):
        words = switch_words()
        found, rejected = self.detect_with_table(words)
        self.assertEqual(rejected, [])
        self.assertEqual(len(found), 1)
        d = found[0]
        self.assertEqual(d.branch, CODE_BASE + 4 * 8)
        self.assertEqual(d.table, TABLE)
        self.assertEqual(d.count, 3)               # bgt against 2 -> cases 0..2
        self.assertEqual(d.targets, CASE_TARGETS)
        self.assertTrue(any("bgt" in r for r in d.reasons))

    def test_no_bound_means_no_detection(self):
        words = switch_words(bound_check=False)
        found, rejected = self.detect_with_table(words)
        self.assertEqual(found, [])
        self.assertEqual(len(rejected), 1)
        self.assertIn("no table base and index bound", rejected[0][1])

    def test_entries_outside_text_are_rejected(self):
        words = switch_words()
        bad = struct.pack(">3I", CASE_TARGETS[0], 0x90000000, CASE_TARGETS[2])
        found, rejected = detect(words, CODE_BASE, read_word_from(bad, TABLE), text_range(words))
        self.assertEqual(found, [])
        self.assertIn("not an aligned address in text", rejected[0][1])

    def test_unmapped_table_is_rejected(self):
        words = switch_words()
        found, rejected = detect(words, CODE_BASE, lambda a: None, text_range(words))
        self.assertEqual(found, [])
        self.assertIn("not mapped", rejected[0][1])

    def test_detected_table_drives_interpreter_and_c(self):
        words = switch_words()
        found, _ = self.detect_with_table(words)
        jump = {found[0].branch: found[0].targets}
        dumps = [(DATA_BASE, 4)]
        for index, expected in ((0, 10), (1, 20), (2, 30), (5, 99)):
            init = [(DATA_BASE, struct.pack(">I", index) + bytes(12) + TABLE_BYTES)]
            state = interpret(words, init=init, dumps=dumps, jump_targets=jump)
            self.assertEqual(state["r"][3], expected, f"index {index}")
            if HAVE_GCC:
                actual, _ = run_c(words, init=init, dumps=dumps, jump_targets=jump)
                assert_same_state(self, actual, state)


if __name__ == "__main__":
    unittest.main()
