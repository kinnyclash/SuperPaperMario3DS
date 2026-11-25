@echo off
rem Writes a shareable diagnostic report for a main.dol file.
rem Usage: tools\windows\diagnose.bat "C:\path\to\main.dol" [optional symbols file]
rem The report contains no game bytes. Do not share the .dol file itself.
setlocal
if "%~1"=="" (
  echo Usage: diagnose.bat "path\to\main.dol" [symbols.txt]
  exit /b 1
)
rem Resolve paths the user gave before changing folder to the project root.
set "DOL=%~f1"
set "SYM=%~f2"
set "OUT=%CD%\spm3ds-report.md"
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo Python was not found. Install Python 3.10 or newer from python.org and tick "Add to PATH".
  goto :fail
)
pushd "%~dp0..\.." || goto :fail
if "%~2"=="" (
  %PY% -m spm3ds.diagnose "%DOL%" --out "%OUT%" || goto :fail_pop
) else (
  %PY% -m spm3ds.diagnose "%DOL%" --symbols "%SYM%" --out "%OUT%" || goto :fail_pop
)
popd
echo.
echo Report written to: %OUT%
echo You can share spm3ds-report.md. Do not share the .dol file.
endlocal
exit /b 0
:fail_pop
popd
:fail
echo Diagnosis failed. Check that Python is installed and the path to main.dol is correct.
endlocal
exit /b 1
