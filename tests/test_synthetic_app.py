"""End-to-end check of the synthetic demo that the 3DS app runs.

The generated C is compiled on the host with the same runtime sources the 3DS build uses,
then run. The 3DS build only differs in the console and the entry point. The checked-in copy
must match the generator output, so the 3DS build always runs what the tests cover.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.support import HAVE_GCC, ROOT, RUNTIME

sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_synthetic  # noqa: E402

CHECKED_IN = os.path.join(ROOT, "platform", "3ds", "source", "generated", "synthetic.c")
HOST_MAIN = """\
#include <stdio.h>
int spm3ds_synthetic_run(void);
int main(void) { int ok = spm3ds_synthetic_run(); printf(ok ? "PASS\\n" : "FAIL\\n"); return ok ? 0 : 1; }
"""


class SyntheticDemo(unittest.TestCase):
    def test_checked_in_source_matches_generator(self):
        with open(CHECKED_IN, encoding="utf-8") as f:
            self.assertEqual(f.read(), make_synthetic.build_c(),
                             "run: python3 tools/make_synthetic.py")

    @unittest.skipUnless(HAVE_GCC, "gcc is not installed")
    def test_demo_runs_and_reports_pass(self):
        with tempfile.TemporaryDirectory() as workdir:
            source = os.path.join(workdir, "synthetic.c")
            main = os.path.join(workdir, "main.c")
            exe = os.path.join(workdir, "app")
            with open(source, "w", encoding="utf-8") as f:
                f.write(make_synthetic.build_c())
            with open(main, "w", encoding="utf-8") as f:
                f.write(HOST_MAIN)
            subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Wno-unused-label",
                            "-I", RUNTIME, "-o", exe, source,
                            os.path.join(RUNTIME, "hle.c"), main],
                           check=True, capture_output=True, text=True)
            proc = subprocess.run([exe], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("PASS", proc.stdout)
            self.assertIn("synthetic: r12=2468acf1", proc.stderr)  # OSReport output


if __name__ == "__main__":
    unittest.main()
