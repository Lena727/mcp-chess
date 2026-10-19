@echo off
rem One-click launcher for Windows: double-click this file.
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo uv not found - installing it from https://astral.sh/uv ...
    powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)
uv run mcp-chess %*
if errorlevel 1 pause
