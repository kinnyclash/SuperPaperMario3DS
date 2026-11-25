# Files you need, and why

The project does not contain any Nintendo game files, and it never will. Everything the
project needs from the game must come from a disc you own, read on your own computer.

## What you need

Your own, legally obtained retail copy of **Super Paper Mario** for the Wii. The project's
first target is the **PAL** release, the **revision 0** build (matching the existing research
in `ROADMAP.md`). Check the region on the disc case or label before you start.

## Which files, and when each is needed

Your disc can be read into a folder on your PC with standard disc tools. The folder has
a layout like this (names follow the usual Wii disc layout):

| File or folder | Needed for | When |
|---|---|---|
| `sys/boot.bin` | Reading the game ID and region, so we can confirm the version | First |
| `sys/main.dol` | The game's executable code. This is the input to the recompiler | First, required |
| `sys/fst.bin` | The disc's file table: lets the game open files by number | With `files/` |
| `files/` (the whole folder) | Game data: models, textures, sounds, levels | Later milestones |

We do **not** need: the apploader, the disc's system partition beyond the files above,
save data, or any other console files.

### Where the files go on the SD card

The CPU app reads disc files from these locations. Copy them yourself from your own
extracted disc; the project never ships them.

```
sdmc:/spm3ds/disc/files/     <- the whole files/ folder
sdmc:/spm3ds/disc/sys/fst.bin
```

`fst.bin` is optional: without it, file paths are looked up directly in `files/`, but
opening files by number does not work. Paths that try to leave `files/` are refused.

## What to tell us (without sending the files)

To confirm you have the right version, run `python -m spm3ds.recomp analyze` on your
`main.dol` as described in `WINDOWS_SETUP.md`. Then share only these two facts with us:

1. The six-character game ID from the first six bytes of `sys/boot.bin` (for example, the
   text you see at the start of that file).
2. The SHA-1 checksum of `main.dol`. In the Windows command prompt, run
   `certutil -hashfile "C:\path\to\main.dol" SHA1`.

Neither fact reveals game content. Do not upload the files, the disc image or the game
folder to us or to any online service.

## Keeping things legal and private

- The recompiler output (C source generated from your `main.dol`) contains Nintendo's
  code in translated form. Keep it on your own computer. Do not publish or share it.
- The project's repository must contain no `.dol`, `.iso`, `.wbfs`, `.arc`, `.bin` game file,
  or generated game source. `.gitignore` excludes the common extensions, but you are the
  final check.
- Dumping a disc, and any tool used for it, is your responsibility. Check the law where
  you live before you dump anything. The project does not handle disc decryption or
  console keys, and it does not describe how to do either.
- Redistribution of the finished port is a separate legal question that we have not
  settled. Nothing in this project grants rights to Nintendo's material.
