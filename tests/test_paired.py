"""Paired-single instructions (Gekko, opcode 4 and psq_* 56/57/60/61).

Expected values are written out by hand from exact single-precision numbers. Encodings are
built here from the bit layout in the Gekko documentation, not with the decoder under test:
  A-form (opcode 4): frD 6..10, frA 11..15, frB 16..20, frC 21..25, xo 26..30 (value << 1)
  X-form (opcode 4): frD, frA, frB, xo 21..30 (value << 1)
  psq_*: frD 6..10, rA 11..15, W 15, I (GQR index) 12..14, displacement 0..11
Each program is also compiled to C and compared with the interpreter where gcc exists.
"""
import struct
import unittest

from spm3ds.memory import GuestFault
from tests.helpers import BLR, CODE_BASE, DATA_BASE, addi, addis, mfspr, mtspr
from tests.support import HAVE_GCC, assert_same_state, interpret, run_c

M32 = 0xFFFFFFFF


def ps_a(xo, d, a, b, c):
    return (4 << 26) | (d << 21) | (a << 16) | (b << 11) | (c << 6) | (xo << 1)


def ps_x(xo, d, a, b):
    return (4 << 26) | (d << 21) | (a << 16) | (b << 11) | (xo << 1)


def psq(op, d, a, disp, w=0, i=0):
    return (op << 26) | (d << 21) | (a << 16) | (w << 15) | (i << 12) | (disp & 0xFFF)


def single_bits(x):
    return struct.unpack(">I", struct.pack(">f", x))[0]


def pair(hi, lo):
    """Expected register contents: ps0 (hi word) and ps1 (lo word), both single patterns."""
    return (single_bits(hi) << 32) | single_bits(lo)


def setup_base():
    return [addis(2, 0, DATA_BASE >> 16)]


# Data at DATA_BASE: singles 1.5, -2.0 (pair at +0), 0.25, 4.0 (pair at +8).
DATA = struct.pack(">ffff", 1.5, -2.0, 0.25, 4.0)
INIT = [(DATA_BASE, DATA)]


class PairedArithTests(unittest.TestCase):
    def program(self):
        return setup_base() + [
            psq(56, 1, 2, 0),            # f1 = (1.5, -2.0)
            psq(56, 2, 2, 8),            # f2 = (0.25, 4.0)
            ps_a(21, 3, 1, 2, 0),        # ps_add   f3 = f1 + f2       -> (1.75, 2.0)
            ps_a(20, 4, 1, 2, 0),        # ps_sub   f4 = f1 - f2       -> (1.25, -6.0)
            ps_a(25, 5, 1, 0, 2),        # ps_mul   f5 = f1 * f2(frC)  -> (0.375, -8.0)
            ps_a(29, 6, 1, 1, 2),        # ps_madd  f6 = f1*f2 + f1    -> (1.875, -10.0)
            ps_a(28, 7, 1, 1, 2),        # ps_msub  f7 = f1*f2 - f1    -> (-1.125, -6.0)
            ps_a(31, 8, 1, 1, 2),        # ps_nmadd f8 = -(f1*f2 + f1) -> (-1.875, 10.0)
            ps_a(18, 9, 1, 2, 0),        # ps_div   f9 = f1 / f2       -> (6.0, -0.5)
            ps_x(40, 10, 0, 1),          # ps_neg   f10 = -f1          -> (-1.5, 2.0)
            ps_x(264, 11, 0, 1),         # ps_abs   f11 = |f1|         -> (1.5, 2.0)
            ps_x(136, 12, 0, 1),         # ps_nabs  f12 = -|f1|        -> (-1.5, -2.0)
            ps_x(72, 13, 0, 1),          # ps_mr    f13 = f1           -> (1.5, -2.0)
            ps_x(528, 14, 1, 2),         # ps_merge00 (f1.ps0, f2.ps0) -> (1.5, 0.25)
            ps_x(560, 15, 1, 2),         # ps_merge01 (f1.ps0, f2.ps1) -> (1.5, 4.0)
            ps_x(592, 16, 1, 2),         # ps_merge10 (f1.ps1, f2.ps0) -> (-2.0, 0.25)
            ps_x(624, 17, 1, 2),         # ps_merge11 (f1.ps1, f2.ps1) -> (-2.0, 4.0)
            BLR,
        ]

    EXPECTED = {
        3: (1.75, 2.0), 4: (1.25, -6.0), 5: (0.375, -8.0), 6: (1.875, -10.0),
        7: (-1.125, -6.0), 8: (-1.875, 10.0), 9: (6.0, -0.5), 10: (-1.5, 2.0),
        11: (1.5, 2.0), 12: (-1.5, -2.0), 13: (1.5, -2.0), 14: (1.5, 0.25),
        15: (1.5, 4.0), 16: (-2.0, 0.25), 17: (-2.0, 4.0),
    }

    def check(self, state):
        self.assertEqual(state["f"][1], pair(1.5, -2.0))
        self.assertEqual(state["f"][2], pair(0.25, 4.0))
        for reg, (hi, lo) in self.EXPECTED.items():
            self.assertEqual(state["f"][reg], pair(hi, lo), f"f{reg}")

    def test_interpreter_matches_hand_values(self):
        self.check(interpret(self.program(), init=INIT))

    @unittest.skipUnless(HAVE_GCC, "gcc not available")
    def test_c_matches_interpreter(self):
        expected = interpret(self.program(), init=INIT)
        actual, _ = run_c(self.program(), init=INIT)
        assert_same_state(self, actual, expected)
        self.check(actual)


class PairedMemoryTests(unittest.TestCase):
    def test_w_bit_loads_one_for_ps1(self):
        words = setup_base() + [psq(56, 1, 2, 0, w=1), BLR]
        state = interpret(words, init=INIT)
        self.assertEqual(state["f"][1], pair(1.5, 1.0))

    def test_store_round_trip_and_w_bit_store_only_ps0(self):
        dumps = [(DATA_BASE + 32, 8), (DATA_BASE + 40, 8)]
        words = setup_base() + [
            psq(56, 1, 2, 0),        # f1 = (1.5, -2.0)
            psq(60, 1, 2, 32),       # store both halves at +32
            psq(60, 1, 2, 40, w=1),  # store only ps0 at +40
            BLR,
        ]
        state = interpret(words, init=INIT, dumps=dumps)
        self.assertEqual(state["mem"][DATA_BASE + 32], DATA[0:8])
        self.assertEqual(state["mem"][DATA_BASE + 40], DATA[0:4] + bytes(4))
        if HAVE_GCC:
            c_state, _ = run_c(words, init=INIT, dumps=dumps)
            self.assertEqual(c_state["mem"], state["mem"])

    def test_update_forms_write_back_the_address(self):
        words = setup_base() + [
            psq(57, 1, 2, 4),        # f1 = (-2.0, 0.25), r2 = DATA+4
            BLR,
        ]
        state = interpret(words, init=INIT)
        self.assertEqual(state["f"][1], pair(-2.0, 0.25))
        self.assertEqual(state["r"][2], DATA_BASE + 4)

    def test_gqr_round_trip_through_mtspr_mfspr(self):
        words = [addis(5, 0, 0x1234), addi(5, 5, 0x5678), mtspr(912, 5), mfspr(6, 912), BLR]
        state = interpret(words)
        self.assertEqual(state["r"][6], 0x12345678)

    def run_both(self, words, init, dumps=()):
        state = interpret(words, init=init, dumps=dumps)
        if HAVE_GCC:
            actual, _ = run_c(words, init=init, dumps=dumps)
            assert_same_state(self, actual, state)
        return state

    def test_quantized_loads_hand_values(self):
        # u8, scale 0: bytes 0x10, 0x80 -> (16.0, 128.0)
        words = setup_base() + [addis(5, 0, 0x0004), mtspr(912, 5), psq(56, 1, 2, 0), BLR]
        state = self.run_both(words, [(DATA_BASE, bytes([0x10, 0x80]) + bytes(14))])
        self.assertEqual(state["f"][1], pair(16.0, 128.0))
        # s8 with load scale 2 (divide by 4): 0xFF = -1 -> -0.25, 0x40 = 64 -> 16.0
        words = setup_base() + [addis(5, 0, 0x0206), mtspr(912, 5), psq(56, 1, 2, 0), BLR]
        state = self.run_both(words, [(DATA_BASE, bytes([0xFF, 0x40]) + bytes(14))])
        self.assertEqual(state["f"][1], pair(-0.25, 16.0))
        # u16 with load scale -1 (multiply by 2): 0x0100 -> 512.0, 0x0003 -> 6.0
        words = setup_base() + [addis(5, 0, 0x3F05), mtspr(912, 5), psq(56, 1, 2, 0), BLR]
        state = self.run_both(words, [(DATA_BASE, bytes([0x01, 0x00, 0x00, 0x03]) + bytes(12))])
        self.assertEqual(state["f"][1], pair(512.0, 6.0))

    def test_quantized_w_bit_loads_one_for_ps1(self):
        words = setup_base() + [addis(5, 0, 0x0004), mtspr(912, 5), psq(56, 1, 2, 0, w=1), BLR]
        state = self.run_both(words, [(DATA_BASE, bytes([0x07, 0x09]) + bytes(14))])
        self.assertEqual(state["f"][1], pair(7.0, 1.0))

    def test_quantized_stores_saturate_truncate_and_scale(self):
        # Store type 4 (u8, scale 0): 300.0 saturates to 255, -5.0 saturates to 0.
        # The pair is loaded from memory as float singles (300.0, -5.0) into f1.
        words = setup_base() + [
            psq(56, 1, 2, 0),                 # f1 = (300.0, -5.0) as singles (GQR0 = float)
            addis(5, 0, 0), addi(5, 5, 4),    # GQR1 store type 4 scale 0
            mtspr(913, 5),
            psq(60, 1, 2, 16, i=1),
            BLR,
        ]
        dumps = [(DATA_BASE + 16, 2)]
        state = self.run_both(words, [(DATA_BASE, struct.pack(">ff", 300.0, -5.0) + bytes(8))],
                              dumps)
        self.assertEqual(state["mem"][DATA_BASE + 16], bytes([0xFF, 0x00]))  # 300 -> 255, -5 -> 0
        # Store type 7 (s16) with store scale 1 (multiply by 2): 1.9 -> 3, -2.6 -> -5.
        words = setup_base() + [
            psq(56, 1, 2, 0),
            addis(5, 0, 0), addi(5, 5, 0x107), mtspr(913, 5),
            psq(60, 1, 2, 16, i=1),
            BLR,
        ]
        dumps = [(DATA_BASE + 16, 4)]
        state = self.run_both(words, [(DATA_BASE, struct.pack(">ff", 1.9, -2.6) + bytes(8))],
                              dumps)
        self.assertEqual(state["mem"][DATA_BASE + 16], bytes([0x00, 0x03, 0xFF, 0xFB]))

    def test_quantized_store_w_bit_writes_only_ps0(self):
        words = setup_base() + [
            psq(56, 1, 2, 0),
            addis(5, 0, 0), addi(5, 5, 4), mtspr(913, 5),
            psq(60, 1, 2, 16, w=1, i=1),
            BLR,
        ]
        dumps = [(DATA_BASE + 16, 2)]
        state = self.run_both(words, [(DATA_BASE, struct.pack(">ff", 9.0, 9.0) + bytes(8))],
                              dumps)
        self.assertEqual(state["mem"][DATA_BASE + 16], bytes([9, 0]))

    def test_reserved_quantization_type_is_refused(self):
        # Load type 1 is reserved.
        words = setup_base() + [addis(5, 0, 0x0001), mtspr(912, 5), psq(56, 1, 2, 0), BLR]
        with self.assertRaises(GuestFault):
            interpret(words, init=INIT)


if __name__ == "__main__":
    unittest.main()
