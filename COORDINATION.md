# Coordination log

Read this file first at the start of every session. Append a dated entry for each change.

## Rules
- Work on your own branch: `claude/<topic>` or `other-agent/<topic>`. Merge into `main` only by pull request.
- Never commit game files (.dol, .iso, .wbfs, .arc, .bin, .rvz) or tokens.
- Leave a note here for anything the other agent must know or decide.

## Log

### 2026-11-25 (Claude)
- Added `.gitignore` (game files, build output) and this file.
- Project state: 172 host tests passing. `platform/3ds` has two apps (`APP=cpu`, `APP=gfx`); neither has been compiled with devkitARM yet.
- Open for the other agent: confirm the citro3d texture byte order (RGBA8) and the tiling interleave order on hardware before trusting them.
