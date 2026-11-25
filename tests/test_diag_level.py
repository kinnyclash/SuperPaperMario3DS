"""Diagnostic levels in generated code (runtime/ppc_ctx.h SPM3DS_DIAG_LEVEL).

Level 1 counts instructions once per basic block instead of once per instruction. The
total must be identical to level 2 for any program, and the guest results must not change.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

from spm3ds.recomp import generate_c, harness_source
from tests.helpers import CODE_BASE, program_image
from tests.randprog import random_case
from tests.support import RUNTIME, load_init, new_test_memory
from tests.test_ppc_ext import random_ext_case

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def compile_and_run(source, level, workdir, tag):
    src = os.path.join(workdir, f"{tag}.c")
    exe = os.path.join(workdir, f"{tag}_{level}")
    with open(src, "w") as f:
        f.write(source)
    subprocess.run(["gcc", "-std=c99", "-O1", "-Wall", "-Wno-unused-label",
                    f"-DSPM3DS_DIAG_LEVEL={level}", "-I", RUNTIME, src, "-o", exe, "-lm"],
                   check=True, capture_output=True)
    return subprocess.run([exe], check=True, capture_output=True, text=True).stdout


def split(out):
    icount = None
    rest = []
    for line in out.splitlines():
        if line.startswith("icount "):
            icount = int(line.split()[1])
        else:
            rest.append(line)
    return icount, rest


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class BlockCounting(unittest.TestCase):
    def run_levels(self, words, init, dumps, tag):
        image = program_image(words, CODE_BASE)
        memory = new_test_memory()
        load_init(memory, init)
        source = generate_c(image, CODE_BASE) + harness_source(CODE_BASE, memory, init, dumps)
        with tempfile.TemporaryDirectory() as workdir:
            return {level: split(compile_and_run(source, level, workdir, tag))
                    for level in (0, 1, 2)}

    def check(self, words, init, dumps, tag):
        res = self.run_levels(words, init, dumps, tag)
        self.assertGreater(res[2][0], 0)
        self.assertEqual(res[1][0], res[2][0], f"{tag}: block count differs")
        self.assertEqual(res[0][0], 0)
        self.assertEqual(res[0][1], res[2][1], f"{tag}: results depend on the level")
        self.assertEqual(res[1][1], res[2][1], f"{tag}: results depend on the level")

    def test_random_base_programs(self):
        for seed in (1, 2, 3):
            words, init, dumps = random_case(seed)
            self.check(words, init, dumps, f"base{seed}")

    def test_random_extended_programs(self):
        for seed in (1, 2):
            words, init, dumps = random_ext_case(seed)
            self.check(words, init, dumps, f"ext{seed}")

    def test_benchmark_loop_with_branches_and_calls(self):
        import sys
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import make_benchmark
        src = os.path.join(ROOT, "platform", "3ds", "source", "generated", "benchmark.c")
        driver = r"""
#include <stdio.h>
#include <stdint.h>
uint32_t spm3ds_benchmark_run(uint32_t iterations);
extern uint64_t spm3ds_benchmark_icount;
int main(void) {
    uint32_t r = spm3ds_benchmark_run(500);
    printf("%08x %llu\n", (unsigned)r, (unsigned long long)spm3ds_benchmark_icount);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as workdir:
            drv = os.path.join(workdir, "d.c")
            with open(drv, "w") as f:
                f.write(driver)
            outs = {}
            for level in (1, 2):
                exe = os.path.join(workdir, f"b{level}")
                subprocess.run(["gcc", "-std=c99", "-O1", f"-DSPM3DS_DIAG_LEVEL={level}",
                                "-I", RUNTIME, drv, src, "-o", exe, "-lm"], check=True)
                outs[level] = subprocess.run([exe], check=True, capture_output=True,
                                             text=True).stdout.split()
        self.assertEqual(outs[1], outs[2])
        self.assertEqual(int(outs[1][0], 16), make_benchmark.interpret(500))


if __name__ == "__main__":
    unittest.main()
