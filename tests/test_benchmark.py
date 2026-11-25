"""The translated CPU benchmark (tools/make_benchmark.py -> generated/benchmark.c)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.support import RUNTIME

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_benchmark  # noqa: E402

BENCH_C = os.path.join(ROOT, "platform", "3ds", "source", "generated", "benchmark.c")

DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
uint32_t spm3ds_benchmark_run(uint32_t iterations);
extern uint64_t spm3ds_benchmark_icount;
extern const uint32_t spm3ds_benchmark_check_iterations, spm3ds_benchmark_check_value;
int main(int argc, char **argv) {
    uint32_t n = (uint32_t)strtoul(argv[1], NULL, 0);
    uint32_t r = spm3ds_benchmark_run(n);
    (void)argc;
    printf("%08x %llu %u %08x\n", (unsigned)r, (unsigned long long)spm3ds_benchmark_icount,
           (unsigned)spm3ds_benchmark_check_iterations, (unsigned)spm3ds_benchmark_check_value);
    return 0;
}
"""


class Generated(unittest.TestCase):
    def test_checked_in_source_matches_generator(self):
        with open(BENCH_C, encoding="utf-8") as f:
            self.assertEqual(f.read(), make_benchmark.build_c())

    def test_loop_covers_the_intended_families(self):
        from spm3ds.ppc import decode
        ops = {decode(w, make_benchmark.CODE_BASE + 4 * i).op
               for i, w in enumerate(make_benchmark.program())}
        for op in ("mullw", "rlwinm", "xor", "lwzx", "stwx", "bc", "b", "bl", "fmadds",
                   "fadd", "ps_madd", "lfs", "lfd", "stfd"):
            with self.subTest(op=op):
                self.assertIn(op, ops)


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class NativeMatchesInterpreter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="spm3ds-bench-")
        drv = os.path.join(cls.tmp, "driver.c")
        with open(drv, "w") as f:
            f.write(DRIVER)
        cls.exe = os.path.join(cls.tmp, "bench")
        subprocess.run(["gcc", "-std=c99", "-O2", "-Wall", "-Wno-unused-label", "-Werror",
                        "-I", RUNTIME, drv, BENCH_C, "-o", cls.exe, "-lm"], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_native(self, n):
        out = subprocess.run([self.exe, str(n)], check=True, capture_output=True,
                             text=True).stdout.split()
        return int(out[0], 16), int(out[1]), int(out[2]), int(out[3], 16)

    def test_check_value(self):
        r, _, n, check = self.run_native(make_benchmark.CHECK_ITERATIONS)
        self.assertEqual(n, make_benchmark.CHECK_ITERATIONS)
        self.assertEqual(r, check)

    def test_small_counts_match_the_interpreter(self):
        for n in (1, 2, 37, 256):
            with self.subTest(n=n):
                self.assertEqual(self.run_native(n)[0], make_benchmark.interpret(n))

    def test_instruction_count_per_iteration(self):
        # Each iteration runs the loop body plus one of two branch paths: taken (beq, xor)
        # or not taken (beq, add, b), so the count per iteration is one of two values.
        # 13 integer and memory instructions (mullw .. andi.), 2 or 3 on the branch path,
        # 3 FP (fmadds, fadd, ps_madd), 3 for the call (bl, rlwinm, blr) and bdnz.
        short, long_ = 13 + 2 + 3 + 3 + 1, 13 + 3 + 3 + 3 + 1
        a = self.run_native(100)[1]
        b = self.run_native(1100)[1]
        self.assertTrue(1000 * short <= b - a <= 1000 * long_, (b - a))


if __name__ == "__main__":
    unittest.main()
