"""Build the translated CPU benchmark for the 3DS diagnostic screen.

The benchmark is a PowerPC loop written here as instruction words. Each iteration mixes
the kinds of work game code does:
  * integer hashing: mullw, rlwinm (shift), xor, addi;
  * memory: a random read-modify-write (lwzx/stwx) and a sequential read in a 1 KiB table;
  * branching: a data-dependent conditional branch, plus bdnz for the loop;
  * floating point: fmadds and fadd; paired singles: ps_madd;
  * a function call (bl/blr) to a leaf.
The result in r3 is a checksum of the integer accumulator, the FP accumulator and the
paired-single state, so the compiler cannot drop any of the work.

The expected checksum for CHECK_ITERATIONS is computed here by the reference interpreter,
so the diagnostic screen can verify the translated code on the console.

Usage:
  python3 tools/make_benchmark.py [OUTPUT_DIR]

Writes OUTPUT_DIR/benchmark.c (default: platform/3ds/source/generated), which defines
    uint32_t spm3ds_benchmark_run(uint32_t iterations);
    uint64_t spm3ds_benchmark_icount;   /* translated instructions in the last run */
    const uint32_t spm3ds_benchmark_check_iterations, spm3ds_benchmark_check_value;
"""
import os
import random
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from spm3ds.memory import MemoryMap  # noqa: E402
from spm3ds.ppc import Machine  # noqa: E402
from spm3ds.recomp import generate_c  # noqa: E402
from tests.helpers import (BLR, add, addi, addis, andi_dot, b, bc, bl, enc_a, fadd, lfd,  # noqa: E402
                           lfs, lwz, lwzx, mflr, mtctr, mtlr, mullw, or_, ori, program_image,
                           rlwinm, stfd, stwx, xor)

CODE_BASE = 0x80200000
DATA_BASE = 0x80210000
DATA_SIZE = 4096
TABLE = 0          # 256 words
CONSTS = 1100      # FP constants
OUT = 1200         # FP results stored here at the end
CHECK_ITERATIONS = 1000
HASH_MUL = 0x9E3779B1


def li32(reg, value):
    value &= 0xFFFFFFFF
    return [addis(reg, 0, value >> 16), ori(reg, reg, value & 0xFFFF)]


def fmadds(d, a, c, b_):
    return enc_a(59, d, a, b_, c, 29)


def ps_madd(d, a, c, b_):
    return enc_a(4, d, a, b_, c, 29)


def program():
    """Return the words. r3 = iterations, r4 = data base (both set by the caller)."""
    w = []
    w += [mflr(0)]                                  # keep the return address across bl
    w += li32(10, HASH_MUL)
    w += li32(5, 0x12345678)                        # hash state
    w += [addi(6, 0, 0), addi(9, 0, 0)]             # accumulator, counter
    w += [lfs(1, 4, CONSTS + 0), lfs(2, 4, CONSTS + 4), lfs(3, 4, CONSTS + 8),
          lfd(7, 4, CONSTS + 16),                   # FP accumulator = 0.0
          lfd(4, 4, CONSTS + 24), lfd(5, 4, CONSTS + 32), lfd(6, 4, CONSTS + 40)]
    w += [mtctr(3)]
    loop = len(w)
    w += [mullw(5, 5, 10),
          rlwinm(11, 5, 15, 17, 31),                # r11 = r5 >> 17
          xor(5, 5, 11),
          addi(5, 5, 0x3B),
          rlwinm(12, 5, 2, 22, 29),                 # r12 = (r5 & 0xFF) * 4
          lwzx(11, 4, 12), add(11, 11, 5), stwx(11, 4, 12),
          rlwinm(12, 9, 2, 22, 29),                 # sequential index
          lwzx(11, 4, 12), add(6, 6, 11), addi(9, 9, 1),
          andi_dot(11, 5, 1)]
    beq_at = len(w)
    w.append(None)                                  # beq skip (patched)
    w.append(add(6, 6, 5))
    b_at = len(w)
    w.append(None)                                  # b join (patched)
    skip = len(w)
    w.append(xor(6, 6, 5))
    join = len(w)
    w += [fmadds(1, 1, 2, 3),                       # f1 = f1 * 0.999 + 0.001
          fadd(7, 7, 1),
          ps_madd(4, 4, 5, 6)]
    bl_at = len(w)
    w.append(None)                                  # bl leaf (patched)
    bdnz_at = len(w)
    w.append(None)                                  # bdnz loop (patched)
    w += [stfd(7, 4, OUT), stfd(4, 4, OUT + 8),
          or_(3, 6, 6)]                             # r3 = accumulator
    for off in (0, 4, 8, 12):
        w += [lwz(11, 4, OUT + off), xor(3, 3, 11)]
    w += [mtlr(0), BLR]
    leaf = len(w)
    w += [rlwinm(6, 6, 1, 0, 31), BLR]              # rotate the accumulator left by one

    w[beq_at] = bc(12, 2, 4 * (skip - beq_at))      # branch if CR0[EQ]
    w[b_at] = b(4 * (join - b_at))
    w[bl_at] = bl(4 * (leaf - bl_at))
    w[bdnz_at] = bc(16, 0, 4 * (loop - bdnz_at))
    return w


def data_init():
    rng = random.Random(2026)
    data = bytearray(DATA_SIZE)
    for i in range(256):
        struct.pack_into(">I", data, TABLE + 4 * i, rng.getrandbits(32))
    struct.pack_into(">fff", data, CONSTS, 0.5, 0.999, 0.001)
    struct.pack_into(">d", data, CONSTS + 16, 0.0)
    struct.pack_into(">ff", data, CONSTS + 24, 0.5, 0.25)    # ps state
    struct.pack_into(">ff", data, CONSTS + 32, 0.99, 0.98)   # ps multiplier
    struct.pack_into(">ff", data, CONSTS + 40, 0.01, 0.02)   # ps addend
    return bytes(data)


def interpret(iterations):
    """Run the benchmark in the reference interpreter; returns r3."""
    memory = MemoryMap()
    memory.add_region("DATA", DATA_BASE, DATA_SIZE)
    memory.write(DATA_BASE, data_init())
    m = Machine(program_image(program(), CODE_BASE), memory)
    m.r[3] = iterations
    m.r[4] = DATA_BASE
    m.run(CODE_BASE)
    return m.r[3]


def build_c():
    source = generate_c(program_image(program(), CODE_BASE), CODE_BASE)
    # The CPU app links this next to synthetic.c, which also defines ppc_dispatch.
    source = source.replace("ppc_dispatch", "benchmark_dispatch")
    init = data_init()
    check = interpret(CHECK_ITERATIONS)
    lines = [
        "",
        "/* Entry point. Generated by tools/make_benchmark.py. */",
        "#include <string.h>",
        "",
        f"static const uint8_t benchmark_init[{len(init)}] = {{",
    ]
    for i in range(0, len(init), 24):
        lines.append("    " + ", ".join(f"0x{x:02X}" for x in init[i:i + 24]) + ",")
    lines += [
        "};",
        "",
        "uint64_t spm3ds_benchmark_icount;",
        f"const uint32_t spm3ds_benchmark_check_iterations = {CHECK_ITERATIONS}u;",
        f"const uint32_t spm3ds_benchmark_check_value = 0x{check:08X}u;",
        "",
        "uint32_t spm3ds_benchmark_run(uint32_t iterations);",
        "uint32_t spm3ds_benchmark_run(uint32_t iterations) {",
        f"    static uint8_t mem[{DATA_SIZE}];",
        "    static ppc_ctx c;",
        "    memset(&c, 0, sizeof c);",
        "    memcpy(mem, benchmark_init, sizeof benchmark_init);",
        f"    c.regions[0].base = 0x{DATA_BASE:08X}u;",
        "    c.regions[0].size = sizeof mem;",
        "    c.regions[0].data = mem;",
        "    c.nregions = 1;",
        "    c.r[3] = iterations;",
        f"    c.r[4] = 0x{DATA_BASE:08X}u;",
        f"    fn_{CODE_BASE:08X}(&c);",
        "    spm3ds_benchmark_icount = c.icount;",
        "    return c.r[3];",
        "}",
        "",
    ]
    return source + "\n".join(lines)


def main(argv):
    out_dir = argv[1] if len(argv) > 1 else os.path.join(ROOT, "platform", "3ds", "source",
                                                         "generated")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "benchmark.c")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_c())
    print(f"wrote {path} ({len(program())} guest instructions)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
