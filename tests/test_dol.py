import unittest

from spm3ds.dol import DolError, parse_dol
from tests.helpers import CODE_BASE, build_dol


class DolParserTests(unittest.TestCase):
    def setUp(self):
        self.words = [0x38600000, 0x4E800020]
        self.data = bytes(range(16))
        self.blob = build_dol(
            text=[(CODE_BASE, self.words)],
            entry=CODE_BASE,
            data=[(0x80100000, self.data)],
            bss=(0x80200000, 0x1000),
        )

    def test_header_fields(self):
        dol = parse_dol(self.blob)
        self.assertEqual(dol.entry, CODE_BASE)
        self.assertEqual(dol.bss_address, 0x80200000)
        self.assertEqual(dol.bss_size, 0x1000)
        self.assertEqual(len(dol.text), 1)
        self.assertEqual(dol.text[0].address, CODE_BASE)
        self.assertEqual(len(dol.data), 1)

    def test_text_words_are_big_endian(self):
        image = parse_dol(self.blob).text_words()
        self.assertEqual(image, {CODE_BASE: 0x38600000, CODE_BASE + 4: 0x4E800020})

    def test_read_from_data_section(self):
        dol = parse_dol(self.blob)
        self.assertEqual(dol.read(0x80100004, 4), bytes([4, 5, 6, 7]))

    def test_read_outside_sections_raises(self):
        dol = parse_dol(self.blob)
        with self.assertRaises(DolError):
            dol.read(0x90000000, 4)

    def test_short_file_rejected(self):
        with self.assertRaises(DolError):
            parse_dol(b"\0" * 0x80)

    def test_section_past_end_of_file_rejected(self):
        truncated = self.blob[:-4]
        with self.assertRaises(DolError):
            parse_dol(truncated)

    def test_entry_outside_text_rejected(self):
        blob = build_dol(text=[(CODE_BASE, [0x4E800020])], entry=CODE_BASE + 0x100)
        with self.assertRaises(DolError):
            parse_dol(blob)


if __name__ == "__main__":
    unittest.main()
