# spm3ds Progress

Status is written as tested, untested, or unverified. **Both 3DS apps now compile with devkitARM in GitHub Actions** (first run 2026-11-25), but nothing has run on a New 3DS or New 2DS XL yet.

## Status at a glance

- Host tests: 289, all passing (Python 3.12 in CI, 3.13 locally). The LLVM encoding test now runs where `llvm-mc` is installed.
- 3DS build: CI builds `spm3ds.3dsx` (CPU app) and `spm3ds_gfx.3dsx` (graphics app) with devkitARM r68, libctru 2.7.1 and citro3d 1.7.1, with `-Werror`. Download them from the run's artifacts.
- PowerPC: the base set plus overflow (OE) forms with XER.OV/SO, CR logic, `mtcrf`/`mcrf`/`mcrxr`, byte-reversed and multiple-word memory ops, every indexed/update form, FP `fres`/`frsqrte`/`fsel`/`fctiw`/FPSCR moves, and the remaining paired singles. See `docs/PPC_COVERAGE.md`.
- GX: real register decoding from libogc's layouts (VCD, VAT, arrays, matrices, projection, viewport, cull, Z/blend mode, TEV), indexed vertices and display lists, in Python and in C.
- **Translated code drives the GPU**: stores to the write-gather pipe are captured (`runtime/hw.c`), decoded by the C command processor (`runtime/gx_cp.c`) and drawn with citro3d. The graphics app shows a cube drawn this way (press A to switch to the native cube).
- GX textures: texture maps from BP registers, C decoders for every non-palette format, upload to citro3d with a cache. The translated demo cube is textured through GX.
- Wii OS: heap, timers, sandboxed files, DVD file access through the disc's file table (`runtime/dvd.c`), and threads, message queues, mutexes, condition variables and alarms (`runtime/os_thread.c`).
- Audio: NDSP output at 32 kHz from a ring buffer; X in the CPU app plays a test tone.
- Diagnostics (CPU app, press Y): memory, New 3DS detection, tick rate, the synthetic benchmark and a mixed integer/memory/branch/FP/paired-single benchmark checked against the interpreter. The 3DS builds count instructions per basic block (exact totals, cheap).
- Not done: running on hardware; analysis of the real Super Paper Mario `main.dol`; TEV beyond stage 0 on the 3DS; hardware status registers; Wii Remote input in the app.

## Milestone 2026-11-25: first real 3DS builds and the GX pipeline

Work is in stacked pull requests: #1 → #2 → #4 → #5 → #6 → #7 → #8 → #10 → #11 → #12 → #13 → #14 → #15 (and this documentation).

1. **CI (#1).** The workflow was missing from the first upload; restored. Host tests failed on Python 3.12 because `math.fma` is new in 3.13; an exact fallback (fractions, one rounding) matches `math.fma` bit for bit. The first devkitARM build reported two format warnings (`%u` with newlib's `uint32_t`, which is `unsigned long`); fixed, and CI now uses `-Werror`. `tools/check_3ds_syntax.py` type-checks the 3DS sources with clang for ARM against the real libctru/citro3d headers when devkitARM is not installed.
2. **PowerPC (#2).** `spm3ds/ppc_ext.py` holds decoder, interpreter rule and C translation side by side for each new instruction. Tests: hand-computed results plus interpreter-versus-C on random programs and FP/paired operand vectors.
3. **GX decoding (#4).** `spm3ds/gx/regs.py` decodes every register field this project uses, each cited to the libogc function that packs it. `tests/gxstream.py` builds FIFO streams the way libogc does, independently of the decoder.
4. **PowerPC to GX (#5).** MMIO hook, FIFO capture, C command processor, generated guest demo (`tools/make_gx_demo.py`), citro3d scene. The whole chain is checked on the host: interpreter and compiled C agree on every FIFO byte and every triangle.
5. **Benchmark (#6).** `tools/make_benchmark.py`; about 22.5 guest instructions per iteration; the console checks the result before timing it.
6. **DVD (#7).** `DVDOpen`, `DVDFastOpen`, `DVDConvertPathToEntrynum`, `DVDReadPrio`, `DVDReadAsyncPrio` and friends, with FST lookup and a sandboxed root. The `DVDFileInfo` offsets are unverified and kept in one table.
7. **Diagnostic levels (#8).** `SPM3DS_DIAG_LEVEL`: per instruction (host default), per basic block (3DS default; identical counts, under 2% cost on the host), or off.
8. **Threads (#10).** One host thread per guest thread, a baton so one runs at a time, highest priority first; messages, recursive mutexes, condition variables, one-shot and periodic alarms (fired like interrupts); deadlocks reported. Event order is tested against the scheduling rules.
9. **Textures (#11).** BP texture registers, C texture decoders matching Python byte for byte, material batches from TEV stage 0, upload to citro3d. PICA RGBA8 byte order is now A,B,G,R (reasoned from citro3d's render-to-texture support).
10. **Audio (#12).** NDSP stereo PCM16 at 32 kHz; test tone on X. Needs `sdmc:/3ds/dspfirm.cdc` on the console.
11. **Split output (#13).** `compile --split N` writes many C files for a game-sized program.
12. **Game app (#14).** `make APP=game` boots your own `main.dol` from the SD card: checks it matches the translated code, writes the Wii boot globals, maps memory with the uncached mirrors, and presents a frame on every `VIWaitForRetrace`. `tools/windows/build_game.bat` does every step on Windows. CI builds it from a stand-in game.
13. **Dispatch (#15).** Binary search for indirect calls; the recompiler scales linearly (a full game in well under a minute).

Assumptions to check on the console (all listed where they are used):
- Graphics: GX clip-space depth and the top-screen tilt in `gx_scene.c`; texture byte order, row order and tiling in `tiling.c`.
- DVD: `DVDFileInfo` offsets in `runtime/dvd.h`.
- Threads: SDK argument orders in `runtime/os_thread.h`.
- Audio: whether NDSP starts (needs the DSP firmware dump) and whether the tone is clean.

## Earlier milestone: practical execution

### 1. Windows build script
- `tools/windows/build.bat` was missing its `ROOT` definition. Fixed.
- Also fixed: devkitPro make now gets `DEVKITPRO` and `DEVKITARM` with forward slashes, which its makefiles expect; the script falls back to the `py` launcher when `python` is not on the PATH; each step stops with a message on failure.
- `tools/windows/diagnose.bat`: resolves the `.dol` path before changing folders (the old version ran Python from the wrong folder); uses the same Python detection.
- Not run: these are Windows batch files and this environment is Linux. Please run them and tell us what happens.

### 2. Hardware diagnostic screen (`platform/3ds/source/diag_screen.c`)
- Press Y on the synthetic demo screen. It shows: free application memory (`osGetMemRegionFree(MEMREGION_APPLICATION)`), whether the console is New 3DS class (`isN3DS()`), the tick rate used for timing, a benchmark of 2000 runs of the translated demo, execution time, and translated instructions per second.
- The instruction count comes from the generated code itself (see item 8), not an estimate.
- UNCOMPILED on hardware: libctru names are written from memory. If the build reports an unknown identifier, check the libctru headers.
- The New 2DS XL uses the New 3DS hardware family, so the New 3DS branch of the screen is the one that applies to it. Verify on the device.
- Update: the `isN3DS()` call (not a libctru function) is replaced by `cfguInit`, `CFGU_GetSystemModel` and `cfguExit`. Model values 2, 4 and 5 count as New 3DS class. Header-checked.

### 3. Portable GX command processor (`spm3ds/gx/cp.py`)
- Command stream parsing: NOP, CP register write, XF write, BP register write, and primitive draws (`0x80`–`0xBF`).
- Opcodes come from Dolphin's GX opcode table (`Source/Core/VideoCommon/OpcodeDecoding.h`): BP `0x61`, CP `0x08`, XF `0x10`; primitive is `(op >> 3) & 15`, VAT index is `op & 7`.
- Vertex decoding: direct positions as f32 or s16 with fractional bits; colour RGBA8; texture coordinates f32.
- Transform: 3x4 position matrix (XF words 0..11, row-major, default identity) then 4x4 projection, then viewport.
- Primitive assembly: triangles, strips, fans, quads. Lines and points are parsed and recorded as unsupported.
- Render state: cull mode (back by default; counter-clockwise in NDC is front), depth test, alpha blend, MODULATE texturing.
- Limits (honest): the CP vertex-descriptor and BP register bit fields are NOT decoded from real hardware registers. Vertex formats are set through an API. Indexed vertices are not implemented. Matrices are read from XF words, but the projection is set through the API.

### 4. Host-side software renderer (`spm3ds/gx/raster.py`, `spm3ds/gx/png.py`)
- Pixel-centre coverage with inclusive edges; perspective-correct colour and texture coordinates; linear depth; nearest-texel sampling with clamp or repeat.
- `tools/render_samples.py` writes PNGs without citro3d: `docs/samples/triangle.png` and `docs/samples/textured_quad.png`.
- Tests (`tests/test_gx_render.py`, 16 tests) use hand-worked coverage (a right triangle covers 10 pixels on a 4x4 screen), texel positions, depth ordering, blending, and PNG structure.

### 5. citro3d rendering backend (UNCOMPILED)
- `platform/3ds/source/experimental/gx_citro3d.c`: init, draw, shutdown, written from citro3d's public examples as recalled.
- `platform/3ds/shaders/passthrough.v.pica`: passthrough vertex shader for picasso. Not assembled.
- Excluded from the default build by the Makefile (`-not -path '*/experimental/*'`). Adding it needs a picasso rule (not written yet), so it cannot be built as-is.
- Expect to correct API signatures on the first real build.

### 6. Paired singles: all quantization types, GQR layout verified
- GQR layout verified against WiiBrew's "Paired single" page: store type bits 0..2, store scale bits 8..13, load type bits 16..18, load scale bits 24..29. Scales are signed 6-bit values.
- Types: 0 float; 4 unsigned 8-bit; 5 unsigned 16-bit; 6 signed 8-bit; 7 signed 16-bit. Loads divide by 2^scale; stores multiply by 2^scale (WiiBrew).
- UNVERIFIED: the store rounding rule. The sources checked give the scale rule but not the rounding. We truncate toward zero, saturate to the type's range, and store 0 for NaN. Written in `spm3ds/quant.py` and `runtime/ppc_ctx.h`, and tested against each other.
- Reserved load and store types (1, 2, 3) are refused.
- `tests/test_paired.py` (11 tests): quantized loads and stores with hand-computed values, saturation, truncation, scale, W bit on load and store, update forms, GQR round-trip through mtspr and mfspr, reserved types, and C-versus-interpreter agreement.

### 7. Input and audio foundations
- Input: `spm3ds/input.py` and `runtime/input.h` map Wii Remote buttons to libctru `KEY_*` bits (A, B, One to X, Two to Y, Plus to START, Minus to SELECT, d-pad). Home is reported as unmapped, not guessed. Classic Controller and Nunchuk mappings are not written, because their bit layouts need a verified source.
- Audio: `runtime/audio_ring.h` converts big-endian PCM16 to little-endian with volume scaling and clamping, and provides a ring buffer that zero-fills on underrun. Hand-tested in C.
- `tests/test_input_audio.py` (5 tests).
- Not on hardware: no 3DS audio output is connected yet.

### 8. Crash diagnostics
- Generated code emits `PPC_DIAG(addr)` before each translated instruction. It sets `c->pc` and increments `c->icount`. Define `SPM3DS_NO_DIAG` to remove the cost (the 3DS build compiles without it only if you add that define).
- `ppc_crash_report` prints the program counter, LR, CTR, CR, XER, r0 to r31, instructions executed, and committed guest memory per region. It is called on a guest memory fault and on an indirect branch to an unknown address.
- The synthetic demo was regenerated and still passes. The generator exports `spm3ds_synthetic_icount` and `spm3ds_synthetic_quiet`.
- Not on hardware: the crash path has only been exercised in host tests where a fault is triggered.

### 9. Automated 3DS build (UNTESTED)
- `.github/workflows/build-3ds.yml`: job 1 runs the host tests on Ubuntu; job 2 builds `platform/3ds` inside the `devkitpro/devkitarm` container and uploads `spm3ds.3dsx`.
- Why this route: GitHub's runners can reach devkitPro's package servers. Our sandbox could not (HTTP 403).
- Not run. The image name and `make` target are from memory of devkitPro's public images; check them on the first run.

## Latest milestone: integration and the graphics demo

Done this milestone:

- **Makefile** (`platform/3ds/Makefile`): two apps, `APP=cpu` (`spm3ds.3dsx`) and `APP=gfx` (`spm3ds_gfx.3dsx`). Structure follows devkitPro's examples. The duplicate picasso/bin2s rule is removed. Not compiled with devkitARM.
- **libctru and citro3d names**: each name used by the 3DS code was checked against the public headers in devkitPro's repositories. Results are in `docs/API_VERIFICATION.md`. Two earlier guesses were wrong and are fixed: `isN3DS()` does not exist, and `C3D_Init` takes a size argument. The old passthrough shader reference is gone.
- **Graphics demo** (`platform/3ds/source/gfx/`): `main.c` runs a textured cube with a depth test and CPU-side rotation; `gx_citro3d.c` / `.h` hold the citro3d backend (80 degree perspective, linear-allocated vertex buffer with a 2-attribute layout, procedural texture); `cube.v.pica` is the picasso vertex shader, assembled into `cube_shbin.h`/`.o` by the Makefile. Culling is off, so winding does not matter. The texture is generated in code, so the demo ships no art.
- **Texture tiling** (`source/gfx/tiling.c`, pure C): 8x8 tiles, Morton order inside a tile. Tested on the host (`tests/test_gfx_tiling.py`, 5 tests). Unverified on hardware: the interleave order and the RGBA byte order.
- **CI** (`.github/workflows/build-3ds.yml`): installs `3ds-dev` (name unverified), builds both apps, uploads both `.3dsx` files.

Not done in this milestone (listed in the request, not started):

- GX CP/BP register decoding from hardware bit layouts, indexed vertices, projection register writes, more TEV functions.
- Connecting translated PowerPC execution to the GX command processor (HLE or memory-mapped hardware).
- NDSP audio output backend on the 3DS.
- Wii OS threads, message queues, alarms and DVD file reads. The Nintendo SDK names are unverified.
- PowerPC overflow flags (XER.OV/SO, OE forms), Rc updates for the forms that lack them, and the list of missing instructions.
- A longer benchmark with sustained arithmetic, memory, floating point and branching.

## Tests

- 254 tests. Run with `python3 -m unittest discover -s tests -t .`
- Equivalence tests need a host `gcc`. The LLVM encoding test needs `llvm-mc` and is skipped without it. The C headers test (input and audio) also needs gcc.
- Your run of the previous build reported 142 passing and 1 skipped (`test_decoder_agrees_with_llvm_assembler`, because `llvm-mc` is not installed on your machine). That matches our expectation for a machine without `llvm-mc`.

## Known issues and limits

- Nothing has run on a console. CI compiles and links both apps; behaviour on hardware is untested.
- Quantization store rounding: truncation is our choice, not a verified rule.
- `fres`/`frsqrte`/`ps_res`/`ps_rsqrte` return exact values, not the hardware estimates; FPSCR exception bits and rounding modes are not modelled.
- GX: lighting, texgen, konst colours, TEV compare modes, indirect textures, alpha test, fog, lines and points are not modelled. The C command processor has no TEV yet (vertex colour only).
- Hardware registers at 0xCC000000/0xCD000000 are plain storage; status bits the real hardware updates (GPU idle, video beam) are not modelled, so code that polls them may wait forever.
- Jump-table detection is pattern-based; a switch compiled differently needs a declared table.
- Windows batch scripts are untested.

## Blockers

- The real `main.dol` is needed for any analysis of Super Paper Mario. You supply it; the project does not contain or distribute it.
- Hardware testing needs you: the apps are built, but only a console can show whether they run.

## Next steps

1. Download `spm3ds-3dsx` and `spm3ds-gfx-3dsx` from the latest successful GitHub Actions run (or build with `tools\windows\build.bat`), copy them to `sdmc:/3ds/`, and start them from the Homebrew Launcher.
2. Graphics app: say whether both cubes appear, rotate, look solid (not inside out) and have the right colours. Press A to switch.
3. CPU app: press Y and send the diagnostic output (memory, tick rate, both benchmarks and their PASS/FAIL lines). Press X and say whether you hear a steady tone.
4. Analyse your `main.dol` with `tools\windows\diagnose.bat` and send the report (it contains no game bytes).
5. Try the boot path without the game: from the CI artifact `spm3ds-game-stub`, copy `stub_main.dol` to `sdmc:/spm3ds/disc/sys/main.dol` and run `spm3ds_game.3dsx`. You should see a coloured triangle and a frame counter.
6. Then run `tools\windows\build_game.bat "path\to\your\main.dol"`. It will most likely stop at step 3 and list what is missing; send `game-analysis.txt` (no game bytes in it).
