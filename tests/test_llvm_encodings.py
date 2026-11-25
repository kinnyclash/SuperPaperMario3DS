"""Decoder checked against an independent assembler.

The instruction words come from LLVM's PowerPC assembler (llvm-mc), which this project did
not write. Each word is decoded by spm3ds and its mnemonic and operand fields are compared
with the operands in the source text. The test is skipped when llvm-mc is not installed.
"""
import re
import shutil
import subprocess
import unittest

from spm3ds.ppc import decode

LLVM_MC = shutil.which("llvm-mc")
TRIPLE = "-triple=powerpc-unknown-linux"  # big-endian 32-bit PowerPC, the Gekko base ISA
ENCODING = re.compile(r"encoding: \[0x([0-9a-f]{2}),0x([0-9a-f]{2}),0x([0-9a-f]{2}),0x([0-9a-f]{2})\]")


def assemble(lines):
    """Assemble each line separately and return its 32-bit big-endian word."""
    words = []
    for line in lines:
        result = subprocess.run([LLVM_MC, TRIPLE, "-show-encoding"], input=line + "\n",
                                capture_output=True, text=True, check=True)
        match = ENCODING.search(result.stdout)
        if not match:
            raise AssertionError(f"llvm-mc gave no encoding for {line!r}: {result.stdout}")
        words.append(int("".join(match.groups()), 16))
    return words


# (source line, expected mnemonic, expected fields that are checked)
CASES = [
    ("addc 7, 4, 6", "addc", {"rd": 7, "ra": 4, "rb": 6, "rc": False}),
    ("adde 8, 3, 5", "adde", {"rd": 8, "ra": 3, "rb": 5}),
    ("subfc 7, 6, 4", "subfc", {"rd": 7, "ra": 6, "rb": 4}),
    ("subfe 8, 5, 3", "subfe", {"rd": 8, "ra": 5, "rb": 3}),
    ("addze 8, 4", "addze", {"rd": 8, "ra": 4}),
    ("subfze 8, 4", "subfze", {"rd": 8, "ra": 4}),
    ("addme 8, 4", "addme", {"rd": 8, "ra": 4}),
    ("subfme 8, 4", "subfme", {"rd": 8, "ra": 4}),
    ("addic 7, 3, -1", "addic", {"rd": 7, "ra": 3, "imm": -1, "rc": False}),
    ("addic. 7, 3, 1", "addic", {"rd": 7, "ra": 3, "imm": 1, "rc": True}),
    ("subfic 7, 3, 5", "subfic", {"rd": 7, "ra": 3, "imm": 5}),
    ("mulhw 7, 3, 4", "mulhw", {"rd": 7, "ra": 3, "rb": 4}),
    ("mulhwu 7, 3, 4", "mulhwu", {"rd": 7, "ra": 3, "rb": 4}),
    ("lbzu 7, 1(2)", "lbzu", {"rd": 7, "ra": 2, "imm": 1}),
    ("lhzu 7, 2(2)", "lhzu", {"rd": 7, "ra": 2, "imm": 2}),
    ("stbu 7, 3(2)", "stbu", {"rd": 7, "ra": 2, "imm": 3}),
    ("sthu 7, 6(2)", "sthu", {"rd": 7, "ra": 2, "imm": 6}),
    ("srawi 3, 3, 5", "srawi", {"rd": 3, "ra": 3, "sh": 5}),
    ("sraw 3, 3, 4", "sraw", {"rd": 3, "ra": 3, "rb": 4}),
    ("fnabs 2, 1", "fnabs", {"fd": 2, "fb": 1}),
    ("fcmpo 0, 1, 2", "fcmpo", {"crf": 0, "fa": 1, "fb": 2}),
    ("mtctr 7", "mtspr", {"rd": 7, "spr": 9}),
    ("mfctr 7", "mfspr", {"rd": 7, "spr": 9}),
    ("bctr", "bcctr", {"bo": 20, "bi": 0}),
    ("bctrl", "bcctr", {"bo": 20, "bi": 0, "link": True}),
]


@unittest.skipUnless(LLVM_MC, "llvm-mc is not installed")
class LlvmDecodeCheck(unittest.TestCase):
    def test_decoder_agrees_with_llvm_assembler(self):
        sources = [c[0] for c in CASES]
        words = assemble(sources)
        for (source, name, fields), word in zip(CASES, words):
            with self.subTest(source=source, word=f"{word:08x}"):
                insn = decode(word, 0x80003000)
                self.assertEqual(insn.op, name)
                for key, value in fields.items():
                    self.assertEqual(getattr(insn, key), value, key)


if __name__ == "__main__":
    unittest.main()
