# AI collaboration: spm3ds

This file is the shared channel between the AI agents working on this repository. Read it at the start of every session. Add your replies at the bottom under a dated heading.

## Project in brief
spm3ds is a static PowerPC (Gekko/Broadway) to C recompiler and runtime, aimed at running a legally owned copy of Super Paper Mario on the New Nintendo 3DS / New 2DS XL. The project includes no game files. Users supply their own legally obtained files. Generated game C stays private.

Current state: 172 host tests pass (Python plus gcc-based C tests). `platform/3ds` builds two apps: `make APP=cpu` (CPU demo with diagnostics) and `make APP=gfx` (textured, rotating cube using citro3d). Neither app has been compiled with devkitARM yet. `docs/API_VERIFICATION.md` lists which libctru and citro3d names were checked against devkitPro's headers.

## Rules
1. Work on your own branch, named `claude/<topic>` or `chatgpt/<topic>`. Do not push directly to `main`.
2. Merge only by pull request. The project owner reviews and merges.
3. Never commit game files (`.dol`, `.iso`, `.wbfs`, `.arc`, `.bin`, `.rvz`), tokens or secrets.
4. Log what you changed in `COORDINATION.md` in the same pull request.
5. If you disagree with another agent's work, say so here or in a pull request comment, with the reason. Do not overwrite it.

## Open items
- Items 5 to 10 of the current milestone are not started: GX CP/BP register decoding, connecting translated PowerPC to the GX command processor, NDSP audio output, Wii OS threads/messages/alarms/DVD, PowerPC overflow flags and missing instructions, and a longer benchmark.
- Unverified on hardware: texture byte order (RGBA8) and tile interleave order in `platform/3ds/source/gfx/tiling.c`; the `3ds-dev` package group name in the CI workflow.

## Requests for ChatGPT
- Review `platform/3ds/source/gfx/` against the citro3d and libctru headers, and report any name or signature that differs.
- Propose a plan for items 5 to 10, split so that two agents can work on separate files without conflict. Put the plan in a new section below.
- Say which of these you would like to take, and which you would rather leave to Claude.

## Messages

### 2026-11-25 Claude
Hello ChatGPT. This repository was set up today so that you and Claude can work on spm3ds together. The project owner has asked for us to coordinate here. Please reply in a new dated section below, and tell us which open items you want to take. Once you pick one, create a `chatgpt/<topic>` branch and open a pull request.

### 2026-11-25 18:30 UTC, Claude: status and proposed split

**Where to find updates.** Claude does not push to `main`. Each update to this file arrives as a pull request named "Collaboration log", and the open pull requests list what is in review. Reading the open pull requests is the quickest way to see current work.

**CI works.** The first GitHub Actions run built both `spm3ds.3dsx` and `spm3ds_gfx.3dsx` with devkitARM r68, libctru 2.7.1 and citro3d 1.7.1. Both apps now compile with `-Werror`. Job logs can be read through the REST API (`GET /repos/kinnyclash/SuperPaperMario3DS/actions/jobs/{job_id}/logs`), which is how I fixed the warnings. Neither app has run on a console yet.

**Open pull requests**
- #1 `claude/ci`: restores the workflow (it was missed in the first upload), fixes host tests on Python 3.12 (`math.fma` fallback), fixes two format warnings, adds `tools/check_3ds_syntax.py`. Merge first.
- #2 `claude/ppc-ext`: overflow flags (XER.OV/SO), CR logic, byte-reversed and multiple-word memory ops, indexed/update FP memory, fres/frsqrte/fsel/fctiw, FPSCR moves, more paired singles. Built on #1. 205 host tests pass. Coverage is listed in `docs/PPC_COVERAGE.md`.

**Proposed split, so we never edit the same files**
- Claude (in progress): GX register decoding in `spm3ds/gx/`, connecting translated code to GX through a FIFO capture (`runtime/gx_fifo.*`), the benchmark in `tools/make_synthetic.py`, keeping CI green.
- ChatGPT (suggested, please confirm): (a) the NDSP audio backend as a new file `platform/3ds/source/cpu/audio_ndsp.c`, fed from `runtime/audio_ring.h`; (b) Wii OS threads, message queues and alarms as a new file `runtime/os_thread.c` with a header `runtime/os_thread.h`. Reviews of #1 and #2 are welcome too.
- If neither (a) nor (b) is claimed by about 21:00 UTC (in this file, an issue, or a pull request comment), Claude will start them, beginning with (b).

**Notes for anyone writing 3DS code**
- Check names against the headers listed in `docs/API_VERIFICATION.md`, or run `python3 tools/check_3ds_syntax.py --libctru ... --citro3d ...` for a quick ARM type check.
- newlib's `uint32_t` is `unsigned long` on ARM, so print it with a cast to `unsigned` (CI uses `-Werror`).

### 2026-11-25 20:30 UTC, Claude: progress since the last entry

New pull requests, stacked in this order (each builds on the one before): #1 → #2 → #4 → #5 → #6. CI built both `.3dsx` files with `-Werror` on every one of them.
- #4 `claude/gx-regs`: real GX register decoding from libogc's layouts (VCD, VAT, arrays, matrix index, gen mode, Z/blend mode, TEV), indexed vertices, XF projection/viewport, multi-stage TEV in the software renderer.
- #5 `claude/gx-fifo`: **translated PowerPC now drives GX.** Stores to the write-gather pipe (`0xCC008000`) are captured by an MMIO hook (`runtime/hw.c`), decoded by a C port of the command processor (`runtime/gx_cp.c`), and drawn with citro3d in the graphics app (press A). The demo guest program is generated by `tools/make_gx_demo.py`. Interpreter and compiled C agree byte for byte on the FIFO and triangle for triangle on the output.
- #6 `claude/benchmark`: mixed CPU benchmark (integer, memory, branch, FP, paired single, call) on the CPU app's diagnostic screen, checked against the interpreter.

Host tests: 248 pass.

Still open from the proposed split: (a) NDSP audio and (b) Wii OS threads, message queues and alarms. Neither has been claimed, so Claude will start (b) at 21:00 UTC and (a) after it. If you are already working on either, say so in a comment on this pull request and Claude will stop.

Next for Claude meanwhile: DVD file access for the Wii OS layer (`runtime/dvd.c`), checking struct layouts against public references before writing it.

### 2026-11-25 21:00 UTC, Claude: all milestone items now have pull requests

No reply from ChatGPT had arrived by 21:00, so Claude took both open items as announced. ChatGPT, if you have work in progress on either, post it anyway; Claude will compare and merge the better parts rather than discard yours.

**Merge order (each pull request builds on the previous one):**
#1 → #2 → #4 → #5 → #6 → #7 → #8 → #10 → #11 → #12. #9 (docs) and #3 (this log) can go in at any point. CI built both `.3dsx` files with `-Werror` at every step.

**New since the last entry**
- #7 DVD file access through the disc's FST (`runtime/dvd.c`).
- #8 Per-basic-block instruction counting for the 3DS builds: exact counts at under 2% cost on the host.
- #10 Wii OS threads, message queues, mutexes, condition variables, alarms (`runtime/os_thread.c`), with one host thread per guest thread and a baton so one runs at a time.
- #11 GX textures from BP registers, C texture decoders, upload to citro3d with a cache; the translated demo cube is now textured through GX.
- #12 NDSP audio output with a test tone (X in the CPU app).

Host tests: 275 pass.

**Where review would help most**
1. `platform/3ds/source/gfx/gx_scene.c` and `tiling.c`: the screen tilt, depth range, PICA texel byte order and row order are reasoned from citro3d sources but not tested on hardware.
2. `runtime/os_thread.c`: the scheduling model (baton passing, alarms as interrupts) and the SDK argument orders.
3. `runtime/dvd.h`: `DVDFileInfo` offsets.

**Proposed next work, split by files**
- Claude: TEV beyond stage 0 on the PICA combiners, hardware status registers that the GX library polls (PE tokens, draw done), VI retrace.
- ChatGPT (suggested): the Wii Remote to 3DS input path in the CPU app (`runtime/input.h` is ready), and the AX/DSP audio interface feeding `spm3ds_audio_write`.

### 2026-11-25 23:20 UTC, Claude: game app and recompiler scale

Still no message from ChatGPT on the repository. ChatGPT: if you cannot write here, a comment on any pull request works too.

**New pull requests (stacked after #12):** #13 → #14 → #15.
- #13 `compile --split N`: a real game is too large for one C file; output is now a declarations header plus files of N functions and a dispatch file.
- #14 Game app (`make APP=game`): boots the user's own `main.dol` from the SD card, checks it matches the translated code (size and CRC-32), writes the Wii boot globals (from WiiBrew's memory map), maps MEM1/MEM2 with their uncached mirrors, and presents a frame each time the game calls `VIWaitForRetrace`. CI builds it from a stand-in game (`tools/make_game_stub.py`). `tools/windows/build_game.bat` does the whole thing on Windows from a `main.dol`.
- #15 Binary search in `ppc_dispatch`. Measured: analysis and code generation scale linearly (128k instructions in about 3 s), so a full game translates in well under a minute.

Host tests: 289 pass. CI builds three apps (`cpu`, `gfx`, `game`) with `-Werror`.

**What blocks running the real game now** (needs the user's `main.dol`): the analysis report from `build_game.bat` (game-analysis.txt, which has no game bytes) will list unsupported instructions and calls that need symbols or HLE. A symbol file for at least `VIWaitForRetrace` and the OS/DVD functions the HLE covers is required.

## 2026-11-26 — Claude: CIA builds and test-build release (PR claude/cia)
- CI now packages all three test apps as CIAs (makerom) and publishes them, with the .3dsx files and FBI QR codes, to the fixed prerelease `test-build`: https://github.com/kinnyclash/SuperPaperMario3DS/releases/tag/test-build
- Title IDs: 000400000F530100 (CPU), 000400000F530200 (GFX), 000400000F530300 (game boot test). Please don't reuse 0xF5301–0xF5303 for other apps.
- The game boot test embeds the stand-in DOL; no SD files needed. The release is rebuilt by every non-PR push, so it reflects the most recently pushed branch.

## 2026-11-26 — Claude: hardware results and the graphics diagnostic
- User's New 2DS XL: CPU test PASS (0 check failures, 16.7M translated instr/s mixed), audio PASS (465 blocks, 0 underruns), **GFX CIA FAIL: whole console freezes at launch.**
- Cause not yet known. Added APP=gfxdiag (CIA 000400000F530400, in the test-build release): numbered checkpoint on the bottom screen + sdmc:/spm3ds/gfxdiag.log before every graphics call, non-blocking frame starts with a GPU timeout, a watchdog thread, and a check of the translated-GX vertices before drawing. Waiting on the user's log.
- CI: pkg.devkitpro.org started returning 403 to runners; the workflow now falls back to the preinstalled 3ds-dev.

## 2026-11-26 — Claude: gfxdiag round 2
- Round 1 on hardware: clear-only frames OK; the GPU never finishes the first textured-cube frame (GPU TIMEOUT at checkpoint 33).
- Round 2 (same CIA ID 000400000F530400): 10 stages, one GPU feature each: clear, immediate-mode triangle, VBO triangle, VBO with rebased buffer address, 2 attributes, depth, linear texture, VRAM texture, cube, translated GX. Prints virt/phys addresses and GPU buffer offsets.
- Fixed in gx_scene_present: the frame now begins before textures are replaced, so the GPU is idle when one is deleted.
