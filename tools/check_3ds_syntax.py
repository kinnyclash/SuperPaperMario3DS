#!/usr/bin/env python3
"""Type-check the 3DS sources with clang for ARM, without devkitARM.

This is a fast local check, not a build: it runs `clang --target=armv6k-none-eabihf
-fsyntax-only` over every C file both apps compile, against real libctru and citro3d headers.
It catches wrong function names, wrong argument types and 32-bit portability problems.

devkitARM's newlib is not available here, so host C headers stand in for it, with a stub for
newlib's <sys/lock.h> and for the shader header the Makefile generates. Linking, picasso and
newlib-specific behaviour are NOT checked; the real build (GitHub Actions or a local devkitPro
install) is still needed.

Usage:
    python3 tools/check_3ds_syntax.py --libctru PATH/libctru/include --citro3d PATH/citro3d/include
Paths can also come from the LIBCTRU_INCLUDE and CITRO3D_INCLUDE environment variables.
Exit code 0 when every file is clean, 1 on any warning or error, 2 when a tool is missing.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORM = os.path.join(ROOT, "platform", "3ds")
RUNTIME = os.path.join(ROOT, "runtime")

LOCK_STUB = """/* Stub of devkitARM newlib <sys/lock.h>, for syntax checks only. */
#pragma once
#include <stdint.h>
typedef int32_t _LOCK_T;
typedef struct { _LOCK_T lock; uint32_t thread_tag; uint32_t counter; } _LOCK_RECURSIVE_T;
"""


def shader_stub(name):
    return (f"extern const u8 {name}_shbin_end[];\n"
            f"extern const u8 {name}_shbin[];\n"
            f"extern const u32 {name}_shbin_size;\n")


# Runtime files each app links (kept in step with platform/3ds/Makefile, RUNTIME_SRC).
RUNTIME_FILES = {
    "cpu": ("hle.c", "os.c", "mem.c", "hw.c", "dvd.c", "os_thread.c"),
    "gfx": ("hw.c", "gx_cp.c", "gx_tex.c"),
    "game": ("hle.c", "os.c", "mem.c", "hw.c", "dvd.c", "os_thread.c", "gx_cp.c", "gx_tex.c",
             "wii_boot.c"),
}
APP_DIRS = {
    "cpu": ["source/cpu", "source/generated", "source/audio"],
    "gfx": ["source/gfx", "source/render"],
    "game": ["source/game", "source/render", "source/audio"],   # plus the generated game code
}
SHADER_DIRS = {"cpu": [], "gfx": ["source/gfx", "source/render"], "game": ["source/render"]}


def app_sources(app, game_dir=None):
    """C files the Makefile compiles for an app (mirrors platform/3ds/Makefile)."""
    dirs = [os.path.join(PLATFORM, d) for d in APP_DIRS[app]]
    if app == "game" and game_dir:
        dirs.append(game_dir)
    files = []
    for full in dirs:
        for dirpath, _, names in os.walk(full):
            files += [os.path.join(dirpath, n) for n in sorted(names) if n.endswith(".c")]
    files += [os.path.join(RUNTIME, n) for n in RUNTIME_FILES[app]]
    return files


def shader_names(app):
    names = []
    for rel in SHADER_DIRS[app]:
        d = os.path.join(PLATFORM, rel)
        names += [n[: -len(".v.pica")] for n in sorted(os.listdir(d)) if n.endswith(".v.pica")]
    return names


def host_multiarch_dirs():
    out = ["/usr/include"]
    for cand in ("/usr/include/x86_64-linux-gnu", "/usr/include/aarch64-linux-gnu"):
        if os.path.isdir(cand):
            out.append(cand)
    return out


def check(libctru, citro3d, clang="clang", verbose=False):
    stub = tempfile.mkdtemp(prefix="spm3ds-armstub-")
    try:
        os.makedirs(os.path.join(stub, "sys"))
        os.makedirs(os.path.join(stub, "gnu"))
        with open(os.path.join(stub, "sys", "lock.h"), "w") as f:
            f.write(LOCK_STUB)
        # glibc's gnu/stubs.h asks for stubs-32.h on a 32-bit target; it only lists missing
        # functions, so an empty file is enough for a syntax check.
        open(os.path.join(stub, "gnu", "stubs-32.h"), "w").close()
        failures = 0
        game_dir = os.path.join(stub, "game_generated")
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import make_game_stub
        make_game_stub.build(game_dir)
        for app in ("cpu", "gfx", "game"):
            for name in shader_names(app):
                with open(os.path.join(stub, f"{name}_shbin.h"), "w") as f:
                    f.write(shader_stub(name))
            for src in app_sources(app, game_dir):
                cmd = [clang, "--target=armv6k-none-eabihf", "-mfloat-abi=hard", "-D__3DS__",
                       "-std=gnu11", "-fsyntax-only", "-Wall", "-Wextra", "-Wno-unused-label",
                       "-I", libctru, "-I", RUNTIME, "-I", stub, "-I", game_dir, "-I", citro3d]
                for rel in APP_DIRS[app]:
                    cmd += ["-I", os.path.join(PLATFORM, rel)]
                for d in host_multiarch_dirs():
                    cmd += ["-isystem", d]
                cmd.append(src)
                res = subprocess.run(cmd, capture_output=True, text=True)
                rel = os.path.relpath(src, ROOT)
                diag = (res.stdout + res.stderr).strip()
                if res.returncode != 0 or diag:
                    failures += 1
                    print(f"FAIL [{app}] {rel}")
                    print(diag)
                elif verbose:
                    print(f"ok   [{app}] {rel}")
        return failures
    finally:
        shutil.rmtree(stub, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--libctru", default=os.environ.get("LIBCTRU_INCLUDE"))
    ap.add_argument("--citro3d", default=os.environ.get("CITRO3D_INCLUDE"))
    ap.add_argument("--clang", default="clang")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if not shutil.which(args.clang):
        print("clang not found", file=sys.stderr)
        return 2
    for label, path in (("libctru", args.libctru), ("citro3d", args.citro3d)):
        if not path or not os.path.isfile(os.path.join(path, "3ds.h" if label == "libctru"
                                                        else "citro3d.h")):
            print(f"{label} include directory not found: {path!r}", file=sys.stderr)
            return 2
    failures = check(args.libctru, args.citro3d, args.clang, args.verbose)
    print(f"{failures} file(s) with problems" if failures else "all 3DS sources clean")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
