# Setting up spm3ds on Windows

This guide builds the project on your own Windows computer. It has two parts:

- **Part A** runs the host-side tests and the analysis tool. It needs Python only.
- **Part B** builds the New 3DS program (`spm3ds.3dsx`). It needs devkitPro.

You can do Part A now. Part B needs devkitPro, which is a larger install.

Steps may look slightly different if devkitPro or Python has been updated. The official
pages are the authority: https://devkitpro.org/wiki/Getting_Started and https://www.python.org/downloads/.

---

## Part A: Python tests and the analysis tool

1. **Install Python 3.13 or later.** Download the installer from python.org. On the first
   screen, tick **Add python.exe to PATH**, then click **Install Now**.

2. **Get the project.** Unzip `spm3ds-project.zip` to a folder with no spaces in the path,
   for example `C:\spm3ds`.

3. **Open a terminal in that folder.** In File Explorer, open `C:\spm3ds`, click the address
   bar, type `cmd`, and press Enter.

4. **Run the tests.**

       python -m unittest discover -s tests -t .

   Every test should report `OK`. If you have a C compiler on PATH (see the optional step
   below), the native-code tests run too. Without one they are skipped, and the result still
   says `OK`.

5. **Optional: a C compiler for the native tests.** Install MSYS2 from https://www.msys2.org,
   open the **MSYS2 MINGW64** shell, and run `pacman -S mingw-w64-x86_64-gcc`. Then add
   `C:\msys64\mingw64\bin` to your PATH, open a new `cmd` window, and rerun the tests.

6. **Run the analysis tool on your own file.** Put your own `main.dol` (see
   `docs/REQUIRED_FILES.md`) in a folder you choose, then run:

       python -m spm3ds.recomp analyze "C:\path\to\main.dol"

   This lists every instruction the project does not support yet. It changes nothing.

---

## Part B: Building the New 3DS program with devkitPro

devkitPro is the toolchain for Nintendo handheld homebrew. It includes the compiler for the
3DS, the 3DS library (libctru), and the tool that packs the program into a `.3dsx` file.

1. **Download the devkitPro installer.** Go to https://devkitpro.org/wiki/Getting_Started,
   and download the Windows installer (named `devkitProUpdate` on that page).

2. **Run the installer.** Accept the defaults. The installer puts devkitPro in
   `C:\devkitPro` and sets the `DEVKITPRO` environment variable for you. Keep the default
   location; the build relies on it.

3. **Install the 3DS development group.** When the installer finishes, it may offer to open
   a package manager window. Otherwise, from the Start menu open **devkitPro MSYS2**, and
   run:

       dkp-pacman -Syu

   Accept any prompts. If the shell asks you to close the window, close it, reopen
   **devkitPro MSYS2**, and run `dkp-pacman -Syu` again until it reports nothing to do. Then:

       dkp-pacman -S 3ds-dev

   This installs the 3DS compiler, libctru, citro3d and the `3dsxtool` packer. The package
   name is the one devkitPro documents for 3DS work; if it has changed, the Getting Started
   page lists the current one.

4. **Check the install.** In the same shell, run:

       echo $DEVKITPRO
       arm-none-eabi-gcc --version

   The first should print a path such as `/c/devkitPro`. The second should print a compiler
   version. If the second fails, the 3DS group did not install. Rerun step 3.

5. **Find the project from the MSYS2 shell.** Windows drive `C:` is `/c` in this shell, so
   `C:\spm3ds` is `/c/spm3ds`. Move to the 3DS folder:

       cd /c/spm3ds/platform/3ds

6. **Build.**

       make

   A successful build ends with a line that mentions `spm3ds.3dsx`, and the file appears in
   `platform/3ds`.

   **Caution:** the Makefile in this project was written from the standard devkitPro template
   and has not yet been built by its author. If `make` fails, copy the complete error text
   and send it to the project for a fix. Do not change flags until we have the error.

7. **Run on the console.** Copy `spm3ds.3dsx` to the SD card at `3ds/spm3ds/spm3ds.3dsx`.
   On a New 3DS, open **Homebrew Launcher** (it must be installed on the console already) and
   start `spm3ds`. The current build runs a synthetic PowerPC demo that was translated to C on
   the PC. The top screen shows `PASS` if the translated code produced the expected values,
   and `FAIL` otherwise. Press START to exit. The game itself is not part of this build.

---

## Troubleshooting

- **`DEVKITPRO must be set`**: the environment variable is missing. Reinstall devkitPro, or
  set `DEVKITPRO=/c/devkitPro` in the MSYS2 shell and retry.
- **`python` not found**: reopen the terminal after installing Python, or use `py -3` in place
  of `python`.
- **Tests fail after a change**: run `python -m unittest discover -s tests -t . -v` to see which
  test failed, and keep the full output when reporting it.
