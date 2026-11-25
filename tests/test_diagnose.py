"""Diagnostic report (spm3ds/diagnose.py): checks content, fingerprint and privacy rules."""
import hashlib
import os
import tempfile
import unittest

from spm3ds.diagnose import build_report
from tests.helpers import BLR, CODE_BASE, DATA_BASE, addi, bc, build_dol, lwz, addis

SECRET = b"SECRET-GAME-STRING-123"


class DiagnoseTests(unittest.TestCase):
    def write_dol(self, workdir, words, data=()):
        blob = build_dol([(CODE_BASE, words)], CODE_BASE, data=list(data), bss=(DATA_BASE + 0x100, 0x2000))
        path = os.path.join(workdir, "main.dol")
        with open(path, "wb") as f:
            f.write(blob)
        return path, blob

    def test_report_has_fingerprint_header_and_sections(self):
        with tempfile.TemporaryDirectory() as workdir:
            words = [addis(2, 0, DATA_BASE >> 16), lwz(3, 2, 0), addi(3, 3, 1), BLR]
            path, blob = self.write_dol(workdir, words, data=[(DATA_BASE, SECRET)])
            report = build_report(path)
        self.assertIn(f"SHA-1: `{hashlib.sha1(blob).hexdigest()}`", report)
        self.assertIn("Entry point: `0x80003000`", report)
        self.assertIn("| text | 0 | `0x80003000` | 16 bytes |", report)
        self.assertIn("| bss | - | `0x80010100` | 8,192 bytes |", report)
        self.assertIn("Share this report freely", report)

    def test_report_contains_no_game_bytes_or_instruction_words(self):
        with tempfile.TemporaryDirectory() as workdir:
            words = [addis(2, 0, DATA_BASE >> 16), lwz(3, 2, 0), addi(3, 3, 1), BLR]
            path, _ = self.write_dol(workdir, words, data=[(DATA_BASE, SECRET)])
            report = build_report(path)
        self.assertNotIn(SECRET.decode(), report)
        for w in words:
            self.assertNotIn(f"{w:08x}", report.lower())

    def test_unsupported_words_are_reported_by_address_and_opcode(self):
        with tempfile.TemporaryDirectory() as workdir:
            # primary opcode 1 is reserved, so it is listed as unsupported.
            words = [addi(3, 0, 1), (1 << 26) | 0x1234, BLR]
            path, _ = self.write_dol(workdir, words)
            report = build_report(path)
        self.assertIn("primary opcode 1: 1 word(s): `0x80003004`", report)

    def test_memory_section_mentions_new_3ds_query(self):
        with tempfile.TemporaryDirectory() as workdir:
            path, _ = self.write_dol(workdir, [BLR])
            report = build_report(path)
        self.assertIn("osGetMemRegionSize(MEMREGION_APPLICATION)", report)
        self.assertIn("Wii MEM1 (24 MiB) share", report)


if __name__ == "__main__":
    unittest.main()
