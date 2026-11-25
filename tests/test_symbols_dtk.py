"""Symbol files in decomp-toolkit format (spm3ds/hle.py parse_symbols)."""
import unittest

from spm3ds.hle import SymbolError, external_from_symbols, parse_symbols

DTK = """\
__start = .init:0x80004000; // type:function size:0x22C scope:global
memset = .init:0x80004230; // type:function size:0x30
lbl_80300000 = .data:0x80300000; // type:object size:0x10
OSCreateThread = .text:0x8019A1C0; // type:function size:0x1B0 scope:global
VIWaitForRetrace = .text:0x801C0000; // type:function size:0x40
__vt__8MyClass = .data:0x80310000; // type:object size:0x20 scope:global
plainText = .text:0x80020000;
"""


class DtkSymbols(unittest.TestCase):
    def test_functions_only(self):
        self.assertEqual(parse_symbols(DTK), [
            (0x80004000, "__start"), (0x80004230, "memset"), (0x8019A1C0, "OSCreateThread"),
            (0x801C0000, "VIWaitForRetrace"), (0x80020000, "plainText")])

    def test_hle_binding(self):
        bound, unimplemented = external_from_symbols(DTK)
        # memset is implemented in runtime/hle.c, so it is bound too.
        self.assertEqual(bound, {0x80004230: "memset", 0x8019A1C0: "OSCreateThread",
                                 0x801C0000: "VIWaitForRetrace"})
        self.assertIn("__start", unimplemented)

    def test_mixed_with_simple_format(self):
        text = "80001000 OSReport  # simple\n" + DTK
        self.assertEqual(parse_symbols(text)[0], (0x80001000, "OSReport"))

    def test_malformed(self):
        with self.assertRaises(SymbolError):
            parse_symbols("broken = .text:zzz;")
        with self.assertRaises(SymbolError):
            parse_symbols("noaddress = ;")


if __name__ == "__main__":
    unittest.main()
