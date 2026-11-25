"""Memory map, DOL loading, HLE binding and host implementations, and the GX TEV model."""
import os
import subprocess
import tempfile
import unittest

from spm3ds.dol import parse_dol
from spm3ds.gx.tev import tev_combine
from spm3ds.hle import SymbolError, external_from_symbols, parse_symbols
from spm3ds.memory import GuestFault, MemoryMap, WII_MEM1, WII_MEM2
from spm3ds.recomp import generate_c, harness_source
from tests import helpers as h
from tests.helpers import CODE_BASE, DATA_BASE, build_dol, program_image
from tests.support import HAVE_GCC, ROOT, compile_c, load_init, new_test_memory, parse_output


class MemoryMapTests(unittest.TestCase):
    def test_wii_default_layout(self):
        mem = MemoryMap.wii_default()
        self.assertEqual([(r.name, r.base, r.size) for r in mem.regions],
                         [("MEM1", WII_MEM1[0], WII_MEM1[1]), ("MEM2", WII_MEM2[0], WII_MEM2[1])])

    def test_big_endian_round_trip(self):
        mem = MemoryMap()
        mem.add_region("R", 0x80000000, 64)
        mem.write_uint(0x80000010, 4, 0x11223344)
        self.assertEqual(mem.read(0x80000010, 4), bytes([0x11, 0x22, 0x33, 0x44]))
        self.assertEqual(mem.read_uint(0x80000010, 2), 0x1122)

    def test_unmapped_access_faults(self):
        mem = MemoryMap()
        mem.add_region("R", 0x80000000, 16)
        with self.assertRaises(GuestFault):
            mem.read(0x80000010, 4)
        with self.assertRaises(GuestFault):
            mem.read(0x8000000E, 4)  # straddles the end of the region

    def test_overlapping_regions_rejected(self):
        mem = MemoryMap()
        mem.add_region("A", 0x80000000, 0x100)
        with self.assertRaises(ValueError):
            mem.add_region("B", 0x80000080, 0x100)

    def test_addresses_are_truncated_to_32_bits(self):
        mem = MemoryMap()
        mem.add_region("R", 0x80000000, 16)
        self.assertEqual(mem.read(0x80000000 | (1 << 32), 1), b"\x00")


class DolLoadingTests(unittest.TestCase):
    def test_sections_load_at_their_addresses(self):
        blob = build_dol(text=[(CODE_BASE, [h.BLR])], entry=CODE_BASE,
                         data=[(DATA_BASE, b"\xde\xad\xbe\xef")])
        mem = MemoryMap()
        mem.add_region("MEM1", 0x80000000, 0x20000)
        parse_dol(blob).load_into(mem)
        self.assertEqual(mem.read(DATA_BASE, 4), b"\xde\xad\xbe\xef")
        self.assertEqual(mem.read_uint(CODE_BASE, 4), h.BLR)

    def test_section_outside_every_region_fails_loudly(self):
        blob = build_dol(text=[(CODE_BASE, [h.BLR])], entry=CODE_BASE)
        mem = MemoryMap()
        mem.add_region("other", 0x90000000, 0x100)
        with self.assertRaises(GuestFault):
            parse_dol(blob).load_into(mem)


class SymbolFileTests(unittest.TestCase):
    def test_parse_skips_comments_and_blanks(self):
        text = "# header\n\n80003100 OSReport  # trailing comment\n80003200 mystery\n"
        self.assertEqual(parse_symbols(text), [(0x80003100, "OSReport"), (0x80003200, "mystery")])

    def test_malformed_lines_are_rejected(self):
        with self.assertRaises(SymbolError):
            parse_symbols("80003100\n")
        with self.assertRaises(SymbolError):
            parse_symbols("zzzz OSReport\n")

    def test_only_implemented_names_are_bound(self):
        bound, unimplemented = external_from_symbols("80003100 OSReport\n80003200 Foo\n")
        self.assertEqual(bound, {0x80003100: "OSReport"})
        self.assertEqual(unimplemented, ["Foo"])


@unittest.skipUnless(HAVE_GCC, "host gcc not available")
class HleNative(unittest.TestCase):
    """Guest code calls host implementations through the generated bindings.

    HLE addresses are outside the loaded text, so each `bl` to one becomes a host call.
    Each call's displacement is computed from the index of the instruction that makes it.
    """

    def run_with_hle(self, words, names, init=(), dumps=()):
        memory = new_test_memory()
        load_init(memory, init)
        source = generate_c(program_image(words, CODE_BASE), CODE_BASE, names)
        source += harness_source(CODE_BASE, memory, init, dumps)
        hle_c = os.path.join(ROOT, "runtime", "hle.c")
        with tempfile.TemporaryDirectory() as workdir:
            exe = compile_c(source, workdir, extra_sources=[hle_c])
            proc = subprocess.run([exe], check=True, capture_output=True, text=True)
        return parse_output(proc.stdout), proc.stderr

    def test_osreport_formats_guest_arguments(self):
        hle_addr = CODE_BASE + 0x100
        fmt = b"value=%d hex=%x str=%s pct=%%\n\x00"
        msg = DATA_BASE + 0x40
        text = msg + 0x20
        words = [
            h.addis(3, 0, msg >> 16), h.ori(3, 3, msg & 0xFFFF),          # 0,1: format
            h.addi(4, 0, 42),                                             # 2: %d
            h.addi(5, 0, 255),                                            # 3: %x
            h.addis(6, 0, text >> 16), h.ori(6, 6, text & 0xFFFF),        # 4,5: %s
            h.bl(hle_addr - (CODE_BASE + 4 * 6)),                         # 6: call
            h.BLR,
        ]
        init = [(msg, fmt), (text, b"hi\x00")]
        state, stderr = self.run_with_hle(words, {hle_addr: "OSReport"}, init=init)
        self.assertEqual(stderr, "value=42 hex=ff str=hi pct=%\n")
        self.assertEqual(state["r"][3], 0)

    def test_memcpy_and_memset_through_hle(self):
        src, dst = DATA_BASE, DATA_BASE + 0x80
        memcpy_addr = CODE_BASE + 0x100
        memset_addr = CODE_BASE + 0x120
        words = [
            h.addis(3, 0, dst >> 16), h.ori(3, 3, dst & 0xFFFF),          # 0,1
            h.addis(4, 0, src >> 16), h.ori(4, 4, src & 0xFFFF),          # 2,3
            h.addi(5, 0, 8),                                              # 4
            h.bl(memcpy_addr - (CODE_BASE + 4 * 5)),                      # 5: memcpy
            h.addis(3, 0, (dst + 8) >> 16), h.ori(3, 3, (dst + 8) & 0xFFFF),  # 6,7
            h.addi(4, 0, 0x7F), h.addi(5, 0, 4),                          # 8,9
            h.bl(memset_addr - (CODE_BASE + 4 * 10)),                     # 10: memset
            h.BLR,
        ]
        init = [(src, bytes(range(1, 9)))]
        names = {memcpy_addr: "memcpy", memset_addr: "memset"}
        state, _ = self.run_with_hle(words, names, init=init, dumps=[(dst, 12)])
        self.assertEqual(state["mem"][dst], bytes(range(1, 9)) + b"\x7f\x7f\x7f\x7f")


class GxTevModel(unittest.TestCase):
    """Hand-computed vectors for the reference combiner (scale index 0 means x1)."""

    def test_lerp_selects_a_or_b_by_c(self):
        self.assertEqual(tev_combine(0, 1, 0, 0), 1.0)
        self.assertEqual(tev_combine(0, 1, 0, 1), 0.0)

    def test_subtract_mode(self):
        self.assertEqual(tev_combine(1, 0.5, 0.5, 0.5, subtract=True), 0.5)

    def test_bias_and_scale(self):
        self.assertEqual(tev_combine(0.25, 0, 0, 0, bias=1), 0.75)
        self.assertEqual(tev_combine(0.25, 0, 0, 0, scale=1), 0.5)   # scale index 1 is x2
        self.assertEqual(tev_combine(1, 0, 0, 0, scale=3), 0.5)      # scale index 3 is x1/2

    def test_clamp_is_applied_only_when_requested(self):
        self.assertEqual(tev_combine(1, 1, 1, 0), 1.0)
        self.assertEqual(tev_combine(1, 1, 1, 0, clamp=False), 2.0)
        self.assertEqual(tev_combine(0, 0, 0, 0, bias=2), 0.0)


if __name__ == "__main__":
    unittest.main()
