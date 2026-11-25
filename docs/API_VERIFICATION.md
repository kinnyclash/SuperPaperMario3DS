# API verification

Each libctru and citro3d name the 3DS code uses, with the public header it was checked against.
The reference copies were cloned from devkitPro's GitHub repositories (libctru, citro3d,
3ds-examples). Checks were made by reading the headers, not by compiling. A name marked
**unverified** has no header in the cloned sources.

Paths are relative to the cloned repository root.

## libctru

| Name | Used in | Header checked |
|---|---|---|
| `cfguInit`, `CFGU_GetSystemModel`, `cfguExit` | `source/cpu/diag_screen.c` | `libctru/libctru/include/3ds/services/cfgu.h` |
| `CFG_MODEL_N3DS` (2), `CFG_MODEL_N3DSXL` (4), `CFG_MODEL_N2DSXL` (5) | `source/cpu/diag_screen.c` | same |
| `R_FAILED` | `source/cpu/diag_screen.c`, `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/result.h` |
| `osGetMemRegionFree`, `MEMREGION_APPLICATION` | `source/cpu/diag_screen.c` | `libctru/libctru/include/3ds/os.h` |
| `shaderProgramInit`, `shaderProgramSetVsh`, `shaderProgramFree`, `shaderProgram_s.vertexShader` | `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/gpu/shaderProgram.h` |
| `DVLB_ParseFile` | `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/gpu/shbin.h` |
| `DVLB_Free` | `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/gpu/` (declared next to `DVLB_ParseFile`; file not re-checked) |
| `shaderInstanceGetUniformLocation` | `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/gpu/shaderProgram.h` |
| `gfxInitDefault`, `gfxExit` | `source/gfx/` | `libctru/libctru/include/3ds/gfx.h` |
| `consoleInit` | `source/gfx/main.c` | `libctru/libctru/include/3ds/console.h` |
| `hidScanInput`, `hidKeysDown`, `KEY_START` | `source/gfx/main.c` | `libctru/libctru/include/3ds/services/hid.h` |
| `gspWaitForVBlank` (macro over `gspWaitForEvent`) | `source/gfx/main.c` | `libctru/libctru/include/3ds/services/gspgpu.h` |
| `linearAlloc`, `linearFree` | `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/allocator/linear.h` |
| `GX_TRANSFER_*` display transfer macros | `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/gpu/gx.h` (`GX_TRANSFER_RAW_COPY` confirmed) |
| `GPU_CULL_NONE` (0), `GPU_CULL_BACK_CCW` (2) | `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/gpu/enums.h` |
| `GPU_RGBA8`, `GPU_TEXFACE_2D`, `GPU_NEAREST`, `GPU_LINEAR` | `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/gpu/enums.h` |
| `GPU_GREATER` (6), `GPU_WRITE_ALL` (0x1F), `GPU_TRIANGLES` | `source/gfx/gx_citro3d.c` | `libctru/libctru/include/3ds/gpu/enums.h` |
| `GSPGPU_FlushDataCache` | `source/gfx/gx_scene.c` | `libctru/libctru/include/3ds/services/gspgpu.h` |
| `KEY_A`, `KEY_START` | `source/gfx/main.c` | `libctru/libctru/include/3ds/services/hid.h` |
| `GPU_REPLACE`, `GPU_PRIMARY_COLOR` | `source/gfx/gx_scene.c` | `libctru/libctru/include/3ds/gpu/enums.h` |
| `ndsp*` (audio) | not yet used | **not written yet** |

Header paths in this document were confirmed by grep in the cloned repositories. Only
`DVLB_Free` was not located by name in its own header.

## citro3d

| Name | Used in | Header checked |
|---|---|---|
| `C3D_Init(size_t)` returns `bool`, `C3D_Fini`, `C3D_BindProgram`, `C3D_DEFAULT_CMDBUF_SIZE` | `source/gfx/gx_citro3d.c`, `main.c` | `citro3d/include/c3d/base.h` |
| `C3D_RenderTargetCreate(int, int, GPU_COLORBUF, C3D_DEPTHTYPE)`, `C3D_FrameBegin(u8)` returns `bool`, `C3D_FrameDrawOn`, `C3D_FrameEnd(u8)`, `C3D_FRAME_SYNCDRAW` | `source/gfx/gx_citro3d.c` | `citro3d/include/c3d/renderqueue.h` |
| `C3D_DEPTHTYPE` (transparent union in C) accepts `GPU_RB_DEPTH24_STENCIL8` | same | `citro3d/include/c3d/renderqueue.h` |
| `C3D_RenderTargetSetOutput`, `C3D_RenderTargetClear`, `C3D_RenderTargetDelete`, `C3D_CLEAR_ALL` | same | `citro3d/include/c3d/framebuffer.h` (`C3D_CLEAR_ALL`) and `renderqueue.h` (functions) |
| `C3D_TexInit(tex, w, h, fmt)` returns `bool` | same | `citro3d/include/c3d/texture.h` |
| `C3D_TexLoadImage(tex, data, face, level)` copies bytes unchanged | same | `citro3d/include/c3d/texture.h` |
| `C3D_TexSetFilter`, `C3D_TexBind`, `C3D_TexDelete` | same | `citro3d/include/c3d/texture.h` |
| `C3D_GetTexEnv`, `C3D_TexEnvInit`, `C3D_TexEnvSrc`, `C3D_TexEnvFunc`, `C3D_Both`, `GPU_MODULATE` | same | `citro3d/include/c3d/texenv.h` |
| `C3D_DepthTest(bool, GPU_TESTFUNC, GPU_WRITEMASK)`, `C3D_CullFace` | same | `citro3d/include/c3d/effect.h` |
| `AttrInfo_Init`, `AttrInfo_AddLoader`, `BufInfo_Init`, `BufInfo_Add`, `C3D_SetAttrInfo`, `C3D_SetBufInfo` | same | `citro3d/include/c3d/attribs.h` |
| `C3D_DrawArrays` | same | `citro3d/include/c3d/base.h` |
| `C3D_FVUnifMtx4x4` | same | `citro3d/include/c3d/uniforms.h` (`static inline`) |
| `Mtx_Identity`, `Mtx_Translate`, `Mtx_RotateX`, `Mtx_RotateY`, `Mtx_PerspTilt`, `C3D_AngleFromDegrees`, `C3D_AspectRatioTop`, `C3D_Mtx` | same | `citro3d/include/c3d/maths.h` |

Note: `C3D_FVUnifMtx4x4` is `static inline` in the header, so it does not need linking.

## Shader toolchain

| Item | Status |
|---|---|
| `picasso -o X.shbin X.v.pica` | Used in the Makefile. The command-line form was not checked against the picasso sources, which are outside the cloned repositories. |
| `bin2s X.shbin` produces `X_shbin`, `X_shbin_end`, `X_shbin_size` | Symbol naming taken from the Makefile conventions in the devkitPro examples. **Not re-checked against bin2s output.** |
| `cube.v.pica` uses `.fvec`, `.constf`, `.alias`, `.out`, `.proc`/`.end` | Same syntax as citro3d's `textured_cube` example. Not assembled here. |

## Not verified

- **RGBA8 byte order.** The texture writes R, G, B, A bytes. Whether the PICA200 expects this
  order is not confirmed. A swapped red and blue channel would show this on the console.
- **Tile arrangement.** See `source/gfx/tiling.h`. Checked only against the description in
  libctru's y2r header, not on hardware.
- **Wii OS and libogc names** (`LWP_*`, `MQ_*`, `SYS_*Alarm`, `DVD_*`). Taken from libogc's
  headers in earlier work. Not yet implemented. The Nintendo SDK names (`OSCreateThread`,
  `OSSendMessage`, `OSCreateAlarm`, `DVDOpen`) are **unverified** because no SDK source is in
  this project.
- **GX CP/BP register layouts** (VCD, VAT, BP genmode, TEV colour env). Recorded in
  `docs/GX_PICA_DESIGN.md`; the Python decoder that uses them has not been extended yet.

## GX register layouts (libogc)

Every GX register field the project decodes was read from devkitPro libogc `libogc/gx.c`
(the functions that pack the registers) and `gc/ogc/gx.h` (the enum values). The citations
are in `spm3ds/gx/regs.py`; `tests/gxstream.py` mirrors the packing code independently.

## Build status

Both apps compile and link in GitHub Actions with devkitARM r68, libctru 2.7.1 and
citro3d 1.7.1 (`-Werror`). The `3ds-dev` package group is confirmed: it has 20 members and
is preinstalled in the `devkitpro/devkitarm` image. Note that C3D_TexEnvSrc takes all three
sources in C (the defaults exist only in C++). Nothing has run on a console yet.
