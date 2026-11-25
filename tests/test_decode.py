"""Decoder tests that do not rely on the interpreter or the recompiler.

Known-answer words are standard PowerPC encodings, checked by hand against the field layout.
The field sweep builds words with the test-side encoders and checks each decoded field.
"""
import itertools
import random
import unittest

from spm3ds import ppc
from spm3ds.ppc import Unsupported, decode
from tests import helpers as h


class KnownAnswers(unittest.TestCase):
    def dec(self, word, addr=0x80003000):
        return decode(word, addr)

    def test_nop_is_ori_zero(self):
        i = self.dec(0x60000000)
        self.assertEqual((i.op, i.rd, i.ra, i.imm), ("ori", 0, 0, 0))

    def test_li_is_addi(self):
        i = self.dec(0x38600001)
        self.assertEqual((i.op, i.rd, i.ra, i.imm), ("addi", 3, 0, 1))

    def test_lwz_and_stw(self):
        self.assertEqual(self.dec(0x80640000).op, "lwz")
        self.assertEqual(self.dec(0x90640000).op, "stw")

    def test_blr_and_bctr_and_bctrl(self):
        self.assertEqual(self.dec(0x4E800020).op, "bclr")
        i = self.dec(0x4E800420)
        self.assertEqual((i.op, i.bo, i.link), ("bcctr", 20, False))
        self.assertEqual(self.dec(0x4E800421).link, True)

    def test_bdnz_negative_displacement(self):
        i = self.dec(0x4200FFF8, addr=0x80003010)
        self.assertEqual((i.op, i.bo, i.bi), ("bc", 16, 0))
        self.assertEqual(i.target, 0x80003008)

    def test_lfd_and_stfd(self):
        i = self.dec(0xC8230000)
        self.assertEqual((i.op, i.fd, i.ra, i.imm), ("lfd", 1, 3, 0))
        i = self.dec(0xD8230008)
        self.assertEqual((i.op, i.fd, i.ra, i.imm), ("stfd", 1, 3, 8))

    def test_fadd_a_form(self):
        i = self.dec(0xFC22182A)
        self.assertEqual((i.op, i.fd, i.fa, i.fb), ("fadd", 1, 2, 3))

    def test_slwi_is_rlwinm(self):
        i = self.dec(0x5483103A)
        self.assertEqual((i.op, i.rd, i.ra, i.sh, i.mb, i.me), ("rlwinm", 4, 3, 2, 0, 29))

    def test_mflr_and_mtctr(self):
        self.assertEqual(self.dec(0x7C0802A6).op, "mfspr")
        i = self.dec(0x7D2903A6)
        self.assertEqual((i.op, i.rd, i.spr), ("mtspr", 9, 9))

    def test_cmpw_is_cmp_crf0(self):
        i = self.dec(0x7C032000)
        self.assertEqual((i.op, i.crf, i.ra, i.rb), ("cmp", 0, 3, 4))


class FieldSweep(unittest.TestCase):
    """Build words with the test encoders and check the decoder returns the same fields."""

    def test_d_form_fields(self):
        rng = random.Random(1)
        for _ in range(300):
            d, a = rng.randrange(32), rng.randrange(32)
            imm = rng.randrange(-32768, 32768)
            i = decode(h.addi(d, a, imm), 0)
            self.assertEqual((i.op, i.rd, i.ra, i.imm), ("addi", d, a, imm))
            i = decode(h.lwz(d, a, imm), 0)
            self.assertEqual((i.op, i.rd, i.ra, i.imm), ("lwz", d, a, imm))

    def test_xo_arithmetic_and_rc(self):
        rng = random.Random(2)
        for _ in range(300):
            d, a, b = (rng.randrange(32) for _ in range(3))
            rc = rng.random() < 0.5
            i = decode(h.add(d, a, b, rc=int(rc)), 0)
            self.assertEqual((i.op, i.rd, i.ra, i.rb, i.rc), ("add", d, a, b, rc))
            i = decode(h.subf(d, a, b), 0)
            self.assertEqual((i.op, i.rd, i.ra, i.rb), ("subf", d, a, b))

    def test_rotate_fields(self):
        rng = random.Random(3)
        for _ in range(300):
            a, s, sh = rng.randrange(32), rng.randrange(32), rng.randrange(32)
            mb, me = rng.randrange(32), rng.randrange(32)
            i = decode(h.rlwinm(a, s, sh, mb, me), 0)
            self.assertEqual((i.op, i.rd, i.ra, i.sh, i.mb, i.me), ("rlwinm", s, a, sh, mb, me))

    def test_every_logical_xo_is_recognised(self):
        names = {28: "and", 60: "andc", 316: "xor", 444: "or", 412: "orc", 124: "nor",
                 476: "nand", 284: "eqv", 24: "slw", 536: "srw", 792: "sraw"}
        for xo, name in names.items():
            with self.subTest(name=name):
                self.assertEqual(decode(h.xlog(xo, 5, 6, 7), 0).op, name)

    def test_spr_fields_roundtrip(self):
        self.assertEqual(decode(h.mflr(12), 0).spr, 8)
        self.assertEqual(decode(h.mtctr(12), 0).spr, 9)

    def test_fp_a_form_fields(self):
        rng = random.Random(4)
        ops = {"fadd": h.fadd, "fsub": h.fsub, "fmul": h.fmul, "fdiv": h.fdiv}
        for name, fn in ops.items():
            for _ in range(50):
                d, a, b, c = (rng.randrange(32) for _ in range(4))
                if name == "fmul":
                    i = decode(fn(d, a, c), 0)
                    self.assertEqual((i.op, i.fd, i.fa, i.fc), (name, d, a, c))
                else:
                    i = decode(fn(d, a, b), 0)
                    self.assertEqual((i.op, i.fd, i.fa, i.fb), (name, d, a, b))

    def test_branch_displacements(self):
        for disp in range(-0x2000000, 0x2000000, 0x40000):
            disp &= ~3
            i = decode(h.b(disp), 0x80003000)
            self.assertEqual(i.target, (0x80003000 + disp) & 0xFFFFFFFF)


class Rejections(unittest.TestCase):
    def test_paired_singles_rejected(self):
        with self.assertRaises(Unsupported):
            decode(0x10000000, 0)  # primary opcode 4

    def test_oe_forms_rejected(self):
        with self.assertRaises(Unsupported):
            decode(h.enc_xo(31, 3, 4, 5, 1, 266), 0)

    def test_update_form_with_zero_base_rejected(self):
        with self.assertRaises(Unsupported):
            decode(h.stwu(3, 0, -8), 0)

    def test_bc_with_link_rejected(self):
        with self.assertRaises(Unsupported):
            decode(h.bc(12, 2, 8) | 1, 0)

    def test_unknown_xo_rejected(self):
        with self.assertRaises(Unsupported):
            decode(h.enc_x(31, 1, 2, 3, 1000), 0)


if __name__ == "__main__":
    unittest.main()
