# spm3ds Progress

Status is written as tested, untested, or unverified. Nothing here has run on a New 3DS or New 2DS XL yet. The build for those consoles is written but has not been compiled against devkitARM.

## Status at a glance

- Host tests: 172 tests, all passing in our environment. Two groups need optional tools (gcc for equivalence tests; llvm-mc for the LLVM encoding test). See Tests.
- PowerPC: integer, carry, multiply-high, rotates, FP, update-form memory, CTR loops, paired singles including all quantization types (0, 4, 5, 6, 7), GQR field layout checked against WiiBrew.
- Jump tables: declared (`JUMPTABLE` lines) and automatically detected from code.
- Guest memory: flat and sparse paged regions.
- Diagnostics: guest program counter and instruction counter in generated code; crash report with registers and memory use; shareable `main.dol` report.
- GX: portable command processor, software rasterizer, PNG writer, texture decoders. Host-only. Sample images in `docs/samples/`.
- 3DS app: runs the synthetic demo; a hardware diagnostic screen (press Y) reports free memory, New 3DS detection, tick rate, and translated instructions per second. Both are UNCOMPILED on hardware.
- 3DS graphics app (`make APP=gfx`): textured, rotating cube with a citro3d backend and a picasso vertex shader. Every API name checked against devkitPro's headers (`docs/API_VERIFICATION.md`). UNCOMPILED.
- Input and audio: Wii Remote to 3DS key mapping; PCM conversion and ring buffer. Host-tested in C and Python. Not on hardware.
- CI: GitHub Actions workflow for host tests and both 3DS apps. UNTESTED (not run yet); the `3ds-dev` package group name is unverified.
- Not done: running translated code on hardware, analysis of the real Super Paper Mario `main.dol`, a rendered frame on a console.

## Completed in this milestone

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

- 172 tests. Run with `python3 -m unittest discover -s tests -t .`
- Equivalence tests need a host `gcc`. The LLVM encoding test needs `llvm-mc` and is skipped without it. The C headers test (input and audio) also needs gcc.
- Your run of the previous build reported 142 passing and 1 skipped (`test_decoder_agrees_with_llvm_assembler`, because `llvm-mc` is not installed on your machine). That matches our expectation for a machine without `llvm-mc`.

## Known issues and limits

- The 3DS build is unverified: no devkitARM compile has been run here. The Makefile dry run succeeded against a stub `3ds_rules`.
- New in this milestone and unverified on hardware: the diagnostic screen (libctru names), the citro3d backend (not built), the clock tick rate, the file root path.
- Quantization store rounding: truncation is our choice, not a verified rule.
- GX: CP and BP register decoding is not implemented from hardware bit layouts; vertex formats are set by API.
- Jump-table detection is pattern-based; a switch compiled differently needs a declared table.
- Windows batch scripts are untested.

## Blockers

- devkitPro cannot be installed in this sandbox (HTTP 403). The CI workflow is the planned route; your local install is the other.
- The real `main.dol` is needed for any analysis of Super Paper Mario. You supply it; the project does not contain or distribute it.

## Next steps

1. Run `tools\windows\build.bat` and `tools\windows\diagnose.bat` on your machine. Send us the `spm3ds-report.md` it creates (it contains no game bytes) and any errors from the build.
2. Push the repository to GitHub so the CI workflow runs the 3DS build. Send us the first error log if the build fails.
3. On the console, press Y on the synthetic demo screen and send us the diagnostic output (memory, tick rate, instructions per second).
4. Analyse your `main.dol` with the diagnostic tool and send the report.
5. Build both apps (`make APP=cpu` and `make APP=gfx`) and send the first compiler error if any. Expect to adjust the texture byte order if the cube's colours look wrong.
