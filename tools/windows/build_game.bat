@echo off
rem spm3ds: build the 3DS game app from YOUR OWN main.dol.
rem
rem Usage:  tools\windows\build_game.bat "C:\path\to\main.dol" [symbols.txt] [jumptables.txt]
rem symbols.txt may be "ADDRESS NAME" lines or a decomp-toolkit symbols.txt (for example from a
rem community decompilation project for your game version); only function symbols are used.
rem
rem Steps: 1 find Python, 2 analyse main.dol (report only), 3 translate it to C in
rem platform\3ds\source\game_generated (stays on your computer, never upload it),
rem 4 build platform\3ds\spm3ds_game.3dsx with devkitPro.
rem
rem Then copy to the SD card: spm3ds_game.3dsx to sdmc:/3ds/, and from your own extracted
rem disc sys\main.dol, sys\boot.bin, sys\fst.bin to sdmc:/spm3ds/disc/sys/ and the files\
rem folder to sdmc:/spm3ds/disc/files/. The app checks that main.dol is the same file.
setlocal EnableExtensions
set "ROOT=%~dp0..\.."
set "PY="
set "DOL=%~f1"
set "SYMBOLS="
set "TABLES="
if not "%~2"=="" set "SYMBOLS=--symbols "%~f2""
if not "%~3"=="" set "TABLES=--jump-tables "%~f3""

if "%~1"=="" (
  echo Usage: tools\windows\build_game.bat "C:\path\to\main.dol" [symbols.txt] [jumptables.txt]
  exit /b 1
)
if not exist "%DOL%" (
  echo main.dol was not found at "%DOL%".
  exit /b 1
)

pushd "%ROOT%" || (echo Cannot open project folder "%ROOT%". & goto :fail)

echo.
echo [1/4] Looking for Python...
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo Python was not found. Install Python 3.10 or newer from python.org and tick "Add to PATH".
  goto :fail
)

echo.
echo [2/4] Analysing main.dol (this lists anything the recompiler cannot translate yet)...
%PY% -m spm3ds.recomp analyze "%DOL%" %SYMBOLS% %TABLES% --auto-jump-tables > game-analysis.txt
echo The report is in game-analysis.txt. It contains addresses and opcodes only, no game bytes.

echo.
echo [3/4] Translating main.dol to C (platform\3ds\source\game_generated)...
%PY% -m spm3ds.recomp compile "%DOL%" platform\3ds\source\game_generated --split 400 --game-info %SYMBOLS% %TABLES% --auto-jump-tables
if errorlevel 1 (
  echo Translation stopped. The messages above list each instruction or call that blocks it;
  echo send us game-analysis.txt ^(it has no game bytes^) and we will add what is missing.
  goto :fail
)

echo.
echo [4/4] Building the game app with devkitPro make...
if not defined DEVKITPRO (
  echo DEVKITPRO is not set. Install devkitPro ^(3ds-dev^) from devkitpro.org and open a new terminal.
  goto :fail
)
set "MAKE_EXE=%DEVKITPRO%\msys2\usr\bin\make.exe"
if not exist "%MAKE_EXE%" (
  echo make.exe was not found at "%MAKE_EXE%". Check the devkitPro MSYS2 folder.
  goto :fail
)
set "DP_FWD=%DEVKITPRO:\=/%"
set "DEVKITARM=%DEVKITPRO%\devkitARM"
set "DKA_FWD=%DP_FWD%/devkitARM"
pushd platform\3ds || goto :fail
"%MAKE_EXE%" APP=game DEVKITPRO=%DP_FWD% DEVKITARM=%DKA_FWD%
if errorlevel 1 (
  echo The game build failed. The first error above is usually the one to fix.
  popd
  goto :fail
)
popd
echo.
echo Built: platform\3ds\spm3ds_game.3dsx

popd
echo.
echo Done. Do not share platform\3ds\source\game_generated: it is translated game code.
endlocal
exit /b 0

:fail
popd 2>nul
echo.
echo Build stopped with an error.
endlocal
exit /b 1
