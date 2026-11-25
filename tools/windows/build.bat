@echo off
rem spm3ds Windows build helper.
rem Steps: 1 find Python, 2 run unit tests, 3 check devkitPro, 4 build the 3DS app.
rem Works from any folder: the project root is found from this script's location.
setlocal EnableExtensions
set "ROOT=%~dp0..\.."
set "PY="

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
%PY% --version || goto :fail

echo.
echo [2/4] Running unit tests...
%PY% -m unittest discover -s tests -t . || (
  echo Unit tests failed. Fix them before building the 3DS app.
  goto :fail
)

echo.
echo [3/4] Checking devkitPro...
if not defined DEVKITPRO (
  echo DEVKITPRO is not set. Install devkitPro ^(devkitARM and libctru^) from devkitpro.org,
  echo then open a new terminal. The 3DS build is skipped; the unit tests above still ran.
  goto :done
)
if not exist "%DEVKITPRO%\devkitARM" (
  echo devkitARM was not found under %DEVKITPRO%. Re-run the devkitPro installer and select devkitARM.
  goto :fail
)

echo.
echo [4/4] Building the 3DS app with devkitPro make...
set "MAKE_EXE=%DEVKITPRO%\msys2\usr\bin\make.exe"
if not exist "%MAKE_EXE%" (
  echo make.exe was not found at "%MAKE_EXE%". Check the devkitPro MSYS2 folder.
  goto :fail
)
rem devkitPro's make expects forward slashes in these variables.
set "DP_FWD=%DEVKITPRO:\=/%"
set "DEVKITARM=%DEVKITPRO%\devkitARM"
set "DKA_FWD=%DP_FWD%/devkitARM"

pushd platform\3ds || goto :fail
"%MAKE_EXE%" DEVKITPRO=%DP_FWD% DEVKITARM=%DKA_FWD%
if errorlevel 1 (
  echo The 3DS build failed. The first error above is usually the one to fix.
  popd
  goto :fail
)
popd
echo.
echo Built: platform\3ds\spm3ds.3dsx

:done
popd
echo.
echo Done.
endlocal
exit /b 0

:fail
popd 2>nul
echo.
echo Build stopped with an error.
endlocal
exit /b 1
