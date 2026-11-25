"""Shared test plumbing: run a program through the interpreter and as compiled C, then compare."""
import os
import shutil
import subprocess
import tempfile

from spm3ds.memory import MemoryMap
from spm3ds.ppc import Machine
from spm3ds.recomp import generate_c, harness_source
from tests.helpers import CODE_BASE, program_image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
MEM_BASE = 0x80000000
MEM_SIZE = 0x20000  # 128 KiB test region, large enough for the data area at 0x80010000

HAVE_GCC = shutil.which("gcc") is not None


def new_test_memory(size=MEM_SIZE):
    memory = MemoryMap()
    memory.add_region("MEM1", MEM_BASE, size)
    return memory


def load_init(memory, init):
    for address, data in init:
        memory.write(address, data)


def interpret(words, init=(), dumps=(), base=CODE_BASE, external=frozenset(), jump_targets=None):
    memory = new_test_memory()
    load_init(memory, init)
    machine = Machine(program_image(words, base), memory, external, jump_targets)
    machine.run(base)
    state = machine.state()
    state["mem"] = {address: memory.read(address, length) for address, length in dumps}
    return state


def compile_c(source, workdir, extra_sources=()):
    src = os.path.join(workdir, "prog.c")
    exe = os.path.join(workdir, "prog")
    with open(src, "w", encoding="utf-8") as f:
        f.write(source)
    cmd = ["gcc", "-std=c99", "-O0", "-Wall", "-Wno-unused-label", "-I", RUNTIME, "-o", exe,
           src, *extra_sources, "-lm"]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return exe


def run_c(words, init=(), dumps=(), base=CODE_BASE, external_names=None, seeds=(),
          extra_sources=(), extra_source_text=None, jump_targets=None):
    """Recompile `words`, run the native binary and parse its final state."""
    image = program_image(words, base)
    memory = new_test_memory()
    load_init(memory, init)
    source = generate_c(image, base, external_names, seeds=seeds, jump_targets=jump_targets)
    source += harness_source(base, memory, init, dumps)
    with tempfile.TemporaryDirectory() as workdir:
        exe = compile_c(source, workdir, extra_sources)
        proc = subprocess.run([exe], check=True, capture_output=True, text=True)
    return parse_output(proc.stdout), proc.stderr


def parse_output(text):
    state = {"r": [0] * 32, "f": [0] * 32, "lr": None, "ctr": None, "cr": None, "xer": None,
             "mem": {}}
    for line in text.splitlines():
        parts = line.split()
        name = parts[0]
        if name in ("lr", "ctr", "cr", "xer"):
            state[name] = int(parts[1], 16)
        elif name == "m":
            address = int(parts[1], 16)
            state["mem"][address] = bytes(int(x, 16) for x in parts[2:])
        elif name[0] == "r":
            state["r"][int(name[1:])] = int(parts[1], 16)
        elif name[0] == "f":
            state["f"][int(name[1:])] = int(parts[1], 16)
    return state


def assert_same_state(test, expected, actual, label=""):
    test.assertEqual(actual["r"], expected["r"], f"{label} general registers")
    test.assertEqual(actual["f"], expected["f"], f"{label} FP register bits")
    test.assertEqual(actual["lr"], expected["lr"], f"{label} link register")
    test.assertEqual(actual["ctr"], expected["ctr"], f"{label} CTR")
    test.assertEqual(actual["cr"], expected["cr"], f"{label} condition register")
    test.assertEqual(actual["xer"], expected["xer"], f"{label} XER (carry)")
    test.assertEqual(actual["mem"], expected["mem"], f"{label} memory")
