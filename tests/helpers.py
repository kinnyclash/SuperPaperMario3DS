"""Test-side PowerPC encoders and hand-assembled programs.

The encoders below are written directly from the instruction field layout (opcode, D, A, B,
XO, Rc and so on). They do not use spm3ds.ppc, so they give the decoder an independent check.
No Nintendo code, data or binaries are used here.
"""
import struct
from collections import namedtuple

CODE_BASE = 0x80003000
DATA_BASE = 0x80010000
BLR = 0x4E800020

# init: [(address, bytes)] written before the run. dumps: [(address, length)] compared after.
Program = namedtuple("Program", "words expect_r expect_f expect_cr init dumps")


# ---- field layout encoders -------------------------------------------------------------

def enc_d(op, d, a, imm):
    return ((op & 63) << 26) | ((d & 31) << 21) | ((a & 31) << 16) | (imm & 0xFFFF)


def enc_x(op, d, a, b, xo10, rc=0):
    """X-form: opcode, 5-bit fields, 10-bit extended opcode at bits 1..10, Rc at bit 0."""
    return ((op & 63) << 26) | ((d & 31) << 21) | ((a & 31) << 16) | ((b & 0x3FF) << 11) | \
           ((xo10 & 0x3FF) << 1) | (rc & 1)


def enc_xo(op, d, a, b, oe, xo9, rc=0):
    """XO-form: arithmetic with the OE bit at bit 10 and a 9-bit extended opcode."""
    return ((op & 63) << 26) | ((d & 31) << 21) | ((a & 31) << 16) | ((b & 31) << 11) | \
           ((oe & 1) << 10) | ((xo9 & 0x1FF) << 1) | (rc & 1)


def enc_m(op, s, a, sh, mb, me, rc=0):
    return ((op & 63) << 26) | ((s & 31) << 21) | ((a & 31) << 16) | ((sh & 31) << 11) | \
           ((mb & 31) << 6) | ((me & 31) << 1) | (rc & 1)


def enc_a(op, d, a, b, c, xo5, rc=0):
    """A-form floating point: frD, frA, frB, frC, and a 5-bit extended opcode at bits 1..5."""
    return ((op & 63) << 26) | ((d & 31) << 21) | ((a & 31) << 16) | ((b & 31) << 11) | \
           ((c & 31) << 6) | ((xo5 & 31) << 1) | (rc & 1)


def spr_field(n):
    """SPR numbers are stored with their two 5-bit halves swapped."""
    return ((n & 31) << 5) | (n >> 5)


# ---- integer instructions --------------------------------------------------------------

def addi(d, a, imm):
    return enc_d(14, d, a, imm)


def addis(d, a, imm):
    return enc_d(15, d, a, imm)


def addic(d, a, imm, rc=False):
    return enc_d(13 if rc else 12, d, a, imm)


def subfic(d, a, imm):
    return enc_d(8, d, a, imm)


def mulli(d, a, imm):
    return enc_d(7, d, a, imm)


def ori(a, s, imm):
    return enc_d(24, s, a, imm)


def oris(a, s, imm):
    return enc_d(25, s, a, imm)


def xori(a, s, imm):
    return enc_d(26, s, a, imm)


def andi_dot(a, s, imm):
    return enc_d(28, s, a, imm)


def cmpi(crf, a, imm):
    return enc_d(11, crf << 2, a, imm)


def cmpli(crf, a, imm):
    return enc_d(10, crf << 2, a, imm)


def cmp_(crf, a, b):
    return enc_x(31, crf << 2, a, b, 0)


def cmpl(crf, a, b):
    return enc_x(31, crf << 2, a, b, 32)


def add(d, a, b, rc=0):
    return enc_xo(31, d, a, b, 0, 266, rc)


def subf(d, a, b, rc=0):
    return enc_xo(31, d, a, b, 0, 40, rc)


def neg(d, a):
    return enc_xo(31, d, a, 0, 0, 104)


def mullw(d, a, b):
    return enc_xo(31, d, a, b, 0, 235)


def divw(d, a, b):
    return enc_xo(31, d, a, b, 0, 491)


def divwu(d, a, b):
    return enc_xo(31, d, a, b, 0, 459)


def xlog(xo10, a, s, b, rc=0):
    """Logical X-form: the source register is in the rD field and the result goes to rA."""
    return enc_x(31, s, a, b, xo10, rc)


def and_(a, s, b, rc=0):
    return xlog(28, a, s, b, rc)


def andc(a, s, b):
    return xlog(60, a, s, b)


def or_(a, s, b):
    return xlog(444, a, s, b)


def orc(a, s, b):
    return xlog(412, a, s, b)


def xor(a, s, b):
    return xlog(316, a, s, b)


def nor(a, s, b):
    return xlog(124, a, s, b)


def nand(a, s, b):
    return xlog(476, a, s, b)


def eqv(a, s, b):
    return xlog(284, a, s, b)


def slw(a, s, b):
    return xlog(24, a, s, b)


def srw(a, s, b):
    return xlog(536, a, s, b)


def sraw(a, s, b):
    return xlog(792, a, s, b)


def srawi(a, s, sh):
    return enc_x(31, s, a, sh, 824)


def cntlzw(a, s):
    return enc_x(31, s, a, 0, 26)


def extsb(a, s):
    return enc_x(31, s, a, 0, 954)


def extsh(a, s):
    return enc_x(31, s, a, 0, 922)


def rlwinm(a, s, sh, mb, me, rc=0):
    return enc_m(21, s, a, sh, mb, me, rc)


def rlwimi(a, s, sh, mb, me, rc=0):
    return enc_m(20, s, a, sh, mb, me, rc)


def rlwnm(a, s, b, mb, me, rc=0):
    return enc_m(23, s, a, b, mb, me, rc)


def mfcr(d):
    return enc_x(31, d, 0, 0, 19)


def mfspr(d, n):
    return enc_x(31, d, 0, spr_field(n), 339)


def mtspr(n, s):
    return enc_x(31, s, 0, spr_field(n), 467)


def mflr(d):
    return mfspr(d, 8)


def mtlr(s):
    return mtspr(8, s)


def mtctr(s):
    return mtspr(9, s)


def mfctr(d):
    return mfspr(d, 9)


# ---- memory ----------------------------------------------------------------------------

def lwz(d, a, disp):
    return enc_d(32, d, a, disp)


def lwzu(d, a, disp):
    return enc_d(33, d, a, disp)


def lbz(d, a, disp):
    return enc_d(34, d, a, disp)


def lhz(d, a, disp):
    return enc_d(40, d, a, disp)


def lha(d, a, disp):
    return enc_d(42, d, a, disp)


def stw(s, a, disp):
    return enc_d(36, s, a, disp)


def stwu(s, a, disp):
    return enc_d(37, s, a, disp)


def stb(s, a, disp):
    return enc_d(38, s, a, disp)


def sth(s, a, disp):
    return enc_d(44, s, a, disp)


def lwzx(d, a, b):
    return enc_x(31, d, a, b, 23)


def lbzx(d, a, b):
    return enc_x(31, d, a, b, 87)


def lhzx(d, a, b):
    return enc_x(31, d, a, b, 279)


def stwx(s, a, b):
    return enc_x(31, s, a, b, 151)


def stbx(s, a, b):
    return enc_x(31, s, a, b, 215)


def sthx(s, a, b):
    return enc_x(31, s, a, b, 407)


def lfs(d, a, disp):
    return enc_d(48, d, a, disp)


def lfd(d, a, disp):
    return enc_d(50, d, a, disp)


def stfs(s, a, disp):
    return enc_d(52, s, a, disp)


def stfd(s, a, disp):
    return enc_d(54, s, a, disp)


# ---- floating point --------------------------------------------------------------------

def fadd(d, a, b):
    return enc_a(63, d, a, b, 0, 21)


def fsub(d, a, b):
    return enc_a(63, d, a, b, 0, 20)


def fmul(d, a, c):
    return enc_a(63, d, a, 0, c, 25)


def fdiv(d, a, b):
    return enc_a(63, d, a, b, 0, 18)


def fmadd(d, a, c, b):
    return enc_a(63, d, a, b, c, 29)


def fmsub(d, a, c, b):
    return enc_a(63, d, a, b, c, 28)


def fadds(d, a, b):
    return enc_a(59, d, a, b, 0, 21)


def fsubs(d, a, b):
    return enc_a(59, d, a, b, 0, 20)


def fmuls(d, a, c):
    return enc_a(59, d, a, 0, c, 25)


def fdivs(d, a, b):
    return enc_a(59, d, a, b, 0, 18)


def fcmpu(crf, a, b):
    return enc_x(63, crf << 2, a, b, 0)


def frsp(d, b):
    return enc_x(63, d, 0, b, 12)


def fctiwz(d, b):
    return enc_x(63, d, 0, b, 15)


def fmr(d, b):
    return enc_x(63, d, 0, b, 72)


def fneg(d, b):
    return enc_x(63, d, 0, b, 40)


def fabs(d, b):
    return enc_x(63, d, 0, b, 264)


# ---- branches --------------------------------------------------------------------------

def b(disp):
    return (18 << 26) | (disp & 0x03FFFFFC)


def bl(disp):
    return b(disp) | 1


def bc(bo, bi, disp):
    return (16 << 26) | ((bo & 31) << 21) | ((bi & 31) << 16) | (disp & 0xFFFC)


def bclr(bo, bi):
    return (19 << 26) | ((bo & 31) << 21) | ((bi & 31) << 16) | (16 << 1)


def bcctr(bo, bi, link=False):
    return (19 << 26) | ((bo & 31) << 21) | ((bi & 31) << 16) | (528 << 1) | (1 if link else 0)


NOP = ori(0, 0, 0)


# ---- builders --------------------------------------------------------------------------

def program_image(words, base=CODE_BASE):
    return {base + 4 * i: w for i, w in enumerate(words)}


def build_dol(text, entry, data=(), bss=(0, 0)):
    """Write a DOL file. `text` and `data` are lists of (address, [word or bytes, ...])."""
    if len(text) > 7 or len(data) > 11:
        raise ValueError("too many sections for a DOL header")
    header = bytearray(0x100)
    body = bytearray()
    for i, (address, words) in enumerate(text):
        blob = b"".join(w.to_bytes(4, "big") for w in words)
        struct.pack_into(">I", header, 0x00 + 4 * i, 0x100 + len(body))
        struct.pack_into(">I", header, 0x48 + 4 * i, address)
        struct.pack_into(">I", header, 0x90 + 4 * i, len(blob))
        body += blob
    for j, (address, blob) in enumerate(data):
        struct.pack_into(">I", header, 0x1C + 4 * j, 0x100 + len(body))
        struct.pack_into(">I", header, 0x64 + 4 * j, address)
        struct.pack_into(">I", header, 0xAC + 4 * j, len(blob))
        body += blob
    struct.pack_into(">III", header, 0xD8, bss[0], bss[1], entry)
    return bytes(header) + bytes(body)


def be32(*values):
    return b"".join(v.to_bytes(4, "big") for v in values)


def be_double(x):
    return struct.pack(">d", x)


def be_single(x):
    return struct.pack(">f", x)


DOUBLE_A = 1.5
DOUBLE_B = 2.25
FP_INIT = [(DATA_BASE, be_double(DOUBLE_A) + be_double(DOUBLE_B))]


PROGRAMS = {
    # r3 = 10 + 9 + ... + 1, via a backward bne loop.
    "sum_loop": Program(
        words=[addi(3, 0, 0), addi(4, 0, 10), add(3, 3, 4), addi(4, 4, -1), cmpi(0, 4, 0),
               bc(4, 2, -12), BLR],
        expect_r={3: 55, 4: 0}, expect_f={}, expect_cr=None, init=(), dumps=()),
    # bl into a helper; the caller adds 1 after the helper returns.
    "call_return": Program(
        words=[bl(12), addi(3, 3, 1), BLR, addi(3, 0, 41), BLR],
        expect_r={3: 42}, expect_f={}, expect_cr=None, init=(), dumps=()),
    # Store and reload through guest memory.
    "stack_memory": Program(
        words=[addis(1, 0, 0x8001), ori(4, 0, 0x1234), stw(4, 1, 8), lwz(5, 1, 8),
               add(6, 5, 5), BLR],
        expect_r={5: 0x1234, 6: 0x2468}, expect_f={}, expect_cr=None, init=(), dumps=()),
    # subf with a negative operand, a taken conditional branch on CR1, and mtlr/mflr.
    "subf_cmp_branch_lr": Program(
        words=[addi(3, 0, 100), addi(4, 0, -58), subf(5, 4, 3), cmpi(1, 5, 158),
               bc(12, 6, 8), addi(3, 0, 0), mtlr(4), mflr(7), BLR],
        expect_r={3: 100, 4: 0xFFFFFFC6, 5: 158, 7: 0xFFFFFFC6}, expect_f={},
        expect_cr=0x02000000, init=(), dumps=()),
    # CTR loop: bdnz runs five times, adding 2 each time.
    "ctr_loop": Program(
        words=[addi(3, 0, 0), addi(4, 0, 5), mtctr(4), addi(3, 3, 2), bc(16, 0, -4), BLR],
        expect_r={3: 10}, expect_f={}, expect_cr=None, init=(), dumps=()),
    # Indirect call through CTR. The callee (at +0x18) is named as a seed in the tests.
    "indirect_call": Program(
        words=[addis(5, 0, 0x8000), ori(5, 5, 0x3018), mtctr(5), bcctr(20, 0, link=True),
               addi(3, 3, 1), BLR, addi(3, 0, 41), BLR],
        expect_r={3: 42}, expect_f={}, expect_cr=None, init=(), dumps=()),
    # Logical, shift, rotate and arithmetic families, with CR0 unchanged.
    "integer_families": Program(
        words=[
            ori(4, 0, 0xF0F0), ori(5, 0, 0x0FF0),
            and_(6, 4, 5), or_(7, 4, 5), xor(8, 4, 5),
            rlwinm(9, 4, 4, 0, 27),
            addi(11, 0, -16), srawi(12, 11, 2),
            addi(14, 0, 4), slw(13, 4, 14),
            cntlzw(15, 5), extsb(16, 4), neg(17, 11),
            divw(18, 11, 14), mullw(19, 11, 14),
            BLR,
        ],
        expect_r={6: 0x00F0, 7: 0xFFF0, 8: 0xFF00, 9: 0xF0F00, 12: 0xFFFFFFFC,
                  13: 0xF0F00, 15: 20, 16: 0xFFFFFFF0, 17: 16, 18: 0xFFFFFFFC,
                  19: 0xFFFFFFC0},
        expect_f={}, expect_cr=None, init=(), dumps=()),
    # Double precision FP with a fused multiply-add, then a store.
    "fp_double": Program(
        words=[addis(3, 0, 0x8001), lfd(1, 3, 0), lfd(2, 3, 8), fadd(3, 1, 2),
               fmadd(4, 1, 3, 2), stfd(4, 3, 16), BLR],
        expect_r={}, expect_f={3: 1.5 + 2.25, 4: 1.5 * (1.5 + 2.25) + 2.25},
        expect_cr=None, init=FP_INIT, dumps=((DATA_BASE + 16, 8),)),
}
