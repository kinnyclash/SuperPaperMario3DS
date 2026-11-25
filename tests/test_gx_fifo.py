"""Translated PowerPC code driving the GX command processor through the write-gather pipe.

A hand-assembled guest program stores a GX command stream (built with tests/gxstream.py,
which packs registers the way libogc does) to 0xCC008000 with byte, halfword, word and
double stores. The interpreter (spm3ds/hw.py) and the compiled C (runtime/hw.c) must capture
the same bytes, and the command processor must draw the triangle they describe.
"""
import os
import subprocess
import tempfile
import unittest

from spm3ds.gx.cp import CommandProcessor
from spm3ds.gx.raster import Framebuffer
from spm3ds.hw import Hardware
from spm3ds.memory import GuestFault
from spm3ds.ppc import Machine
from spm3ds.recomp import generate_c, harness_source
from tests import gxstream as g
from tests import helpers as h
from tests.gxstream import GXStream
from tests.helpers import BLR, CODE_BASE, DATA_BASE, program_image
from tests.support import HAVE_GCC, RUNTIME, compile_c, load_init, new_test_memory

WGPIPE = 0xCC008000


def li32(reg, value):
    value &= 0xFFFFFFFF
    return [h.addis(reg, 0, value >> 16), h.ori(reg, reg, value & 0xFFFF)]


def triangle_stream():
    s = GXStream()
    mt = [[2.0 / 4, 0, 0, -1.0], [0, 2.0 / 4, 0, -1.0], [0, 0, -1.0, -1.0], [0, 0, 0, 1]]
    s.LoadProjectionMtx(mt, g.GX_ORTHOGRAPHIC)
    s.LoadPosMtxImm([[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 0]], 0)
    s.SetCurrentMtx(0)
    s.SetCullMode(g.GX_CULL_NONE)
    s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
    s.SetVtxDesc(g.GX_VA_CLR0, g.GX_DIRECT)
    s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
    s.SetVtxAttrFmt(0, g.GX_VA_CLR0, g.GX_CLR_RGBA, g.GX_RGBA8, 0)
    s.Begin(g.GX_TRIANGLES, 0, 3)
    for x, y in [(0, 0), (4, 0), (0, 4)]:
        s.raw("3f", x, y, -0.5)
        s.raw("4B", 0, 255, 0, 255)
    return s.data()


def store_program(stream):
    """Guest code that writes `stream` to the pipe: 8 NOP bytes with stfd first, then the
    stream in chunks of 1, 2 and 4 bytes (stb, sth, stw)."""
    words = li32(3, WGPIPE) + li32(5, DATA_BASE) + [h.lfd(1, 5, 0), h.stfd(1, 3, 0)]
    sizes = [4, 1, 2, 4, 4, 1]
    pos, k = 0, 0
    while pos < len(stream):
        size = min(sizes[k % len(sizes)], len(stream) - pos)
        if size == 3:
            size = 2
        value = int.from_bytes(stream[pos:pos + size], "big")
        words += li32(4, value)
        words.append({1: h.stb, 2: h.sth, 4: h.stw}[size](4, 3, 0))
        pos += size
        k += 1
    words.append(BLR)
    init = [(DATA_BASE, bytes(8))]  # 0.0 as a double: eight zero bytes = eight GX NOPs
    return words, init


def interpret_with_hw(words, init):
    memory = new_test_memory()
    hw = Hardware()
    memory.mmio = hw
    load_init(memory, init)
    m = Machine(program_image(words, CODE_BASE), memory)
    m.run(CODE_BASE)
    return m, hw


def run_c_with_hw(words, init):
    """Compile with runtime/hw.c and return (stdout lines, captured FIFO bytes)."""
    image = program_image(words, CODE_BASE)
    memory = new_test_memory()
    load_init(memory, init)
    source = generate_c(image, CODE_BASE)
    source += harness_source(
        CODE_BASE, memory, init,
        includes=("hw.h",),
        before=("static spm3ds_hw hw;", "spm3ds_hw_init(&hw);", "spm3ds_hw_attach(&c, &hw);"),
        after=('printf("gxfifo");',
               'for (size_t i = 0; i < hw.fifo_size; i++) printf(" %02x", hw.fifo[i]);',
               'printf("\\n");',
               'printf("gxdropped %llu\\n", (unsigned long long)hw.fifo_dropped);'))
    with tempfile.TemporaryDirectory() as workdir:
        exe = compile_c(source, workdir, [os.path.join(RUNTIME, "hw.c")])
        out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
    lines = out.splitlines()
    fifo_line = next(line for line in lines if line.startswith("gxfifo"))
    fifo = bytes(int(x, 16) for x in fifo_line.split()[1:])
    dropped = int(next(line for line in lines if line.startswith("gxdropped")).split()[1])
    return lines, fifo, dropped


class FifoCapture(unittest.TestCase):
    def test_interpreter_captures_the_stream_in_order(self):
        stream = triangle_stream()
        words, init = store_program(stream)
        _, hw = interpret_with_hw(words, init)
        self.assertEqual(bytes(hw.fifo), bytes(8) + stream)

    def test_captured_stream_draws_the_triangle(self):
        stream = triangle_stream()
        words, init = store_program(stream)
        _, hw = interpret_with_hw(words, init)
        cp = CommandProcessor(4, 4).run(hw.take_fifo())
        # 8 NOPs, projection (1 XF), position matrix (1 XF), current matrix (CP + XF),
        # cull mode (1 BP), two vertex descriptors (2 CP each), two attribute formats
        # (3 CP each), one draw.
        self.assertEqual(cp.commands, 8 + 1 + 1 + 2 + 1 + 2 * 2 + 2 * 3 + 1)
        fb = Framebuffer(4, 4)
        # Right triangle with legs of 4 pixels: 10 pixel centres covered (see test_gx_render).
        self.assertEqual(cp.render(fb), 10)
        self.assertEqual(fb.pixel(0, 3), (0.0, 1.0, 0.0, 1.0))

    def test_hardware_registers_are_storage(self):
        words = (li32(3, 0xCC00100A) + li32(4, 0xBEEF) + [h.sth(4, 3, 0), h.lhz(5, 3, 0)]
                 + li32(6, 0xCD000000) + li32(7, 0x12345678) + [h.stw(7, 6, 0), h.lbz(8, 6, 1),
                                                               BLR])
        m, _ = interpret_with_hw(words, [])
        self.assertEqual(m.r[5], 0xBEEF)
        self.assertEqual(m.r[8], 0x34)

    def test_unmapped_address_still_faults(self):
        words = li32(3, 0xC0000000) + [h.stw(3, 3, 0), BLR]
        with self.assertRaises(GuestFault):
            interpret_with_hw(words, [])


@unittest.skipUnless(HAVE_GCC, "host gcc not available")
class NativeFifo(unittest.TestCase):
    def test_compiled_code_captures_the_same_bytes(self):
        stream = triangle_stream()
        words, init = store_program(stream)
        _, hw = interpret_with_hw(words, init)
        _, fifo, dropped = run_c_with_hw(words, init)
        self.assertEqual(dropped, 0)
        self.assertEqual(fifo, bytes(hw.fifo))

    def test_compiled_hardware_registers(self):
        words = (li32(3, 0xCC00100A) + li32(4, 0xBEEF) + [h.sth(4, 3, 0), h.lhz(5, 3, 0)]
                 + li32(6, 0xCD000000) + li32(7, 0x12345678) + [h.stw(7, 6, 0), h.lbz(8, 6, 1),
                                                               BLR])
        lines, _, _ = run_c_with_hw(words, [])
        regs = {line.split()[0]: int(line.split()[1], 16) for line in lines
                if line.startswith("r")}
        self.assertEqual(regs["r5"], 0xBEEF)
        self.assertEqual(regs["r8"], 0x34)


if __name__ == "__main__":
    unittest.main()
