@echo off
setlocal
title Install Lip Sync Studio for BBS
pushd "%~dp0" || exit /b 1
python "%~dp0client_mod\install.py" %*
set "STUDIO_EXIT=%ERRORLEVEL%"
if "%STUDIO_EXIT%"=="0" (
    echo.
    echo Installed. Restart Minecraft, open BBS, select one actor and right-click its name. Choose Lip sync.
) else (
    echo.
    echo Installation failed. The details are shown above.
)
pause
popd
exit /b %STUDIO_EXIT%
