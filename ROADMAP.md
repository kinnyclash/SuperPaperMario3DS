# Super Paper Mario → New Nintendo 3DS: Feasibility & Roadmap

Status: research phase. No Nintendo code, assets, ROMs or keys are included in or planned for this repository.

## 1. Legal and clean-room rules (apply to every phase)

- The repository contains only original code, build scripts and documentation.
- Users supply their own game data, extracted from a disc or dump they own. The project never ships `main.dol`, archives, models, textures, audio, or disc images.
- Disc decryption must happen on the user's own hardware or with tools the user already has. The project will not implement or document key extraction or encryption circumvention.
- Decompiled or matched source from third-party projects (see §2) is not copied into this repo unless its license clearly allows it. Each dependency is recorded with its license.
- Generated output (recompiled C, translated shaders, converted assets) stays in the user's local build directory and is never committed or published.
- Nintendo SDK headers and libraries are not copied. Interfaces are reimplemented from observed behaviour and public hardware documentation.
- Dolphin may be used as a reference oracle for testing only. Its code is not copied unless the license is checked and compatible.

## 2. Existing work surveyed

| Area | Finding | Implication |
|---|---|---|
| Super Paper Mario decomp | `SeekyCt/spm-decomp` is a WIP 1:1 C decompilation of the PAL revision 0 build. Its README says it will never cover the full game and "certainly won't lead to ports to any other platform." | Strong reference for symbols and struct layout. Its licence and the author's intent must be checked before any reuse. We should not depend on it without permission. |
| Other Paper Mario decomps | N64 Paper Mario (`pmret/papermario`) and Paper Mario: TTYD (`doldecomp/ttyd`) are complete or in progress. | Useful for methodology and tooling patterns, not for Wii code. |
| Wii/GC tooling | `encounter/decomp-toolkit` (DOL/REL splitting, analysis), `SeekyCt/ppcdis`, `NWiiRecomp`, `DolRecomp`. | Good candidates for analysis and splitting. Check licences first. |
| Wii static recompilation | Mario Kart Wiicompiled is described as the first static recompilation of a Wii game, targeting PC. Wii Party Recomp is in alpha. | Proves the approach works on Wii code. Neither targets ARM11 or 3DS. |
| 3DS-targeted recompilation | None found in this search. | We would be among the first. This is a risk and a research opportunity. |

## 3. Hardware gap (headline numbers)

| Resource | Wii | New 3DS |
|---|---|---|
| CPU | PowerPC Broadway/Gekko, 729 MHz, single core | ARM11 MPCore (ARMv6K), 804 MHz, quad core |
| Main RAM | 88 MB (MEM1 + MEM2) | 256 MB FCRAM, with part reserved for the OS |
| GPU | Hollywood, fixed-function TEV pipeline (GX) | PICA200, programmable shaders, 10 MB VRAM |
| Display | 480p-class | 400×240 top and 320×240 bottom |
| Audio | Hollywood DSP with its own microcode | Separate DSP with its own firmware (via libctru `ndsp`) |
| Input | Wii Remote (IR pointer, motion), Nunchuk, Classic | Face buttons, D-pad, touch, gyro, circle pad, C-stick (New 3DS) |

Sources for the 3DS memory figures: 3dbrew and Wikipedia note 256 MB on the New 3DS and that 64 MB is dedicated to the OS. The Wii's 88 MB figure comes from community specification sheets. Both should be re-checked against 3dbrew before design decisions are fixed.

## 4. Feasibility assessment

Verdict: **plausible but very large.** Estimated as a multi-year project for a small team. Work divides into four hard problems and several moderate ones.

**Hard problems**

1. **Memory.** Wii game code and data likely exceed a naive 3DS port's budget, and 256 MB includes the OS reserve. Expect to stream assets from SD storage, keep only the active level in RAM, and compress or reformat data. Fixed-address Wii memory maps need virtual remapping.
2. **GX → PICA200 graphics.** The Wii uses fixed-function TEV combiners. The 3DS needs translated shaders that reproduce TEV stages, blend modes, texture formats and the EFB behaviour. This is the single largest technical risk.
3. **Audio DSP.** Wii sound runs as DSP microcode. Either HLE the microcode or reimplement the audio pipeline on ndsp. Accuracy matters for music timing.
4. **Performance.** Recompiled code should run faster than Dolphin on the same hardware, but the ARM11 in-order pipeline with less cache and lower clocks than a modern PC will make big frame-time costs visible. Target 30 fps first.

**Moderate problems**

- OS calls used by the game (threads, timers, heap, DVD and filesystem, messaging). Reimplement the subset the game uses.
- Input mapping. Wii Remote pointer and motion controls need touch or gyro equivalents.
- Display. 400×240 top screen versus a 4:3 or widescreen Wii image. Decide on scaling, letterboxing, and what goes on the bottom screen.
- Build and test pipeline on devkitARM with `-march=armv6k`, VFP, and hard-float ABI.

**Approach comparison**

| Approach | Pros | Cons |
|---|---|---|
| A. Static recompile PPC to C, cross-compile with devkitARM (recommended) | Native speed; can use C-level optimization; matches what the Wii recomp community has done | Needs accurate function boundaries, indirect branch resolution, and data typing; requires a decomp or heavy analysis |
| B. Interpreter or JIT on 3DS | Fastest to a first boot; no need for full analysis | Far too slow on ARM11 for a game of this scale |
| C. Hybrid: recompile hot functions, interpret the rest | Pragmatic during bring-up | Two code paths to maintain; interpreter speed still a concern |

Recommendation: start with **Approach C as a bring-up scaffold**, moving to **Approach A** as the decomp coverage grows. This keeps early boot achievable while the recompiler matures.

## 5. Roadmap

**Phase 0 — Foundations (weeks)**
- Repo layout, licence (choose one compatible with dependencies), CI.
- Install and pin devkitPPC, devkitARM, libctru, citro3d.
- Write the legal/clean-room policy from §1 into CONTRIBUTING.md.
- Verify licences of spm-decomp, decomp-toolkit, ppcdis, NWiiRecomp, DolRecomp.

**Phase 1 — Toolchain and recompiler core (months)**
- Parse DOL and REL files from user-supplied data (a parser for the format, tested on our own PPC binaries built with devkitPPC).
- Instruction decoder and C code generator for the Gekko integer and FP ISA. Include paired-singles and the Gekko-specific instructions the game uses.
- Test harness: small original PPC programs with known outputs, checked on the host and on a 3DS.

**Phase 2 — Runtime shim and OS HLE (months)**
- Memory: map MEM1/MEM2 into 3DS FCRAM with a paging layer; benchmark allocation patterns.
- Threads, heap, timers and synchronisation for the subset the game uses.
- Filesystem: read from a user-provided extracted directory on SD via libctru FS.
- Input: Wii Remote, Nunchuk and Classic to 3DS input, with a remappable config.
- Audio: a minimal sound path on ndsp first, then the DSP microcode strategy.

**Phase 3 — Graphics (largest risk; months)**
- Minimal GX→PICA translator for fixed-function TEV: textures, vertices, blend, depth.
- Compare frames against a reference (Dolphin, used only as an oracle) for regression tests.
- Display layout and scaling decisions.

**Phase 4 — Boot to title screen, then gameplay**
- Iterate on missing OS calls and recompilation gaps until the title screen runs.
- Load one level. Measure frame time on New 3DS and old 3DS hardware.

**Phase 5 — Optimisation and polish**
- Use all four ARM11 cores where the game's structure allows.
- Use the New 3DS's extra RAM for larger caches; keep the base model (original 3DS, 128 MB) as a target if feasible.
- Tune the translator, cache hot paths, reduce draw calls.

## 6. Incremental testing policy

- Every phase has an automated test (host-side where possible, on hardware where needed).
- Regression frames and audio hashes are stored locally, not committed.
- Performance budgets are recorded per milestone and checked in CI on a self-hosted 3DS runner.

## 7. Open decisions for you

1. **Base for function boundaries and symbols.** Use an existing decomp (licence and author consent needed), or do independent analysis with decomp-toolkit? Independent analysis is slower but has no licence dependency.
2. **Which game revision to target.** The spm-decomp work targets PAL revision 0. Pick one revision for the first milestone.
3. **Hardware target.** New 3DS only, or original 3DS too? The original's 128 MB rules out many techniques.
4. **Licence for the new code.** GPL or MIT or similar, depending on what the dependencies require.

## 8. Next immediate steps

- Confirm the licences of the surveyed projects.
- Build a minimal DOL parser and PPC→C emitter against our own test binaries.
- Build a hello-world for New 3DS with devkitARM and libctru, measuring frame and memory headroom.
- Draft the GX→PICA scope for citro3d.

## Sources

- https://github.com/SeekyCt/spm-decomp
- https://github.com/SeekyCt/spm-decomp/blob/master/README.md
- https://github.com/encounter/decomp-toolkit
- https://github.com/SeekyCt/ppcdis
- https://github.com/BlackLineInteractive/NWiiRecomp
- https://www.tomshardware.com/video-games/retro-gaming/mario-kart-wii-recompiled-for-pc-using-ai-with-4k-potential-and-uncapped-frame-rates-first-static-recompilation-of-a-wii-game-supports-over-200-tracks-thanks-to-retro-rewind-compatibility
- https://raw.githubusercontent.com/arelkair/wiiparty-recomp/main/README.md
- https://en.wikipedia.org/wiki/New_Nintendo_3DS (via wikipedia.com mirror)
- https://www.3dbrew.org (FCRAM and hardware specs; re-verify before use)
