"""Split recompiler output (spm3ds.recomp.generate_units / `compile --split N`).

Every test program in tests/helpers.PROGRAMS is compiled as several C files (one function
per file, so calls and the dispatch table cross files) and must give the same final state
as the single-file output and the interpreter.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

from spm3ds.recomp import generate_c, generate_units, harness_source
from tests.helpers import CODE_BASE, PROGRAMS, program_image
from tests.support import (HAVE_GCC, RUNTIME, assert_same_state, interpret, load_init,
                           new_test_memory, parse_output)


# The indirect_call callee must be seeded so the dispatch table has it (as in test_ppc.py).
SEEDS = {"indirect_call": (0x80003018,)}


def run_split(words, init, dumps, per_unit=1, seeds=()):
    image = program_image(words, CODE_BASE)
    memory = new_test_memory()
    load_init(memory, init)
    files = generate_units(image, CODE_BASE, functions_per_unit=per_unit, prefix="game",
                           seeds=seeds)
    harness = harness_source(CODE_BASE, memory, init, dumps, includes=("game_decls.h",))
    with tempfile.TemporaryDirectory() as workdir:
        sources = []
        for name, text in files.items():
            path = os.path.join(workdir, name)
            with open(path, "w") as f:
                f.write(text)
            if name.endswith(".c"):
                sources.append(path)
        main = os.path.join(workdir, "main.c")
        with open(main, "w") as f:
            f.write(harness)
        exe = os.path.join(workdir, "prog")
        subprocess.run(["gcc", "-std=c99", "-O0", "-Wall", "-Wno-unused-label", "-I", RUNTIME,
                        "-I", workdir, main, *sources, "-o", exe, "-lm"], check=True,
                       capture_output=True, text=True)
        out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
    return parse_output(out), files


class UnitLayout(unittest.TestCase):
    def test_files_and_counts(self):
        words = PROGRAMS["call_return"].words if "call_return" in PROGRAMS else \
            next(p.words for p in PROGRAMS.values())
        image = program_image(words, CODE_BASE)
        files = generate_units(image, CODE_BASE, functions_per_unit=1, prefix="game")
        units = sorted(n for n in files if n.startswith("game_0"))
        self.assertIn("game_decls.h", files)
        self.assertIn("game_dispatch.c", files)
        self.assertEqual(units, [f"game_{i:04d}.c" for i in range(len(units))])
        single = generate_c(image, CODE_BASE)
        # Every function definition in the single file appears in exactly one unit.
        for line in single.splitlines():
            if line.startswith("void fn_") and line.endswith("{"):
                self.assertEqual(sum(line in files[u] for u in units), 1, line)

    def test_rejects_zero_functions_per_unit(self):
        with self.assertRaises(ValueError):
            generate_units(program_image(next(iter(PROGRAMS.values())).words, CODE_BASE),
                           CODE_BASE, functions_per_unit=0)


@unittest.skipUnless(HAVE_GCC and shutil.which("gcc"), "host gcc not available")
class SplitMatchesInterpreter(unittest.TestCase):
    def test_every_program(self):
        for name, prog in PROGRAMS.items():
            with self.subTest(program=name):
                expected = interpret(prog.words, prog.init, prog.dumps)
                actual, files = run_split(prog.words, prog.init, prog.dumps,
                                          seeds=SEEDS.get(name, ()))
                assert_same_state(self, expected, actual, name)


if __name__ == "__main__":
    unittest.main()
