@echo off
rem Opens the tandem_dj window. This console stays open as a backup log.
cd /d "%~dp0"
uv run tandem ui
if errorlevel 1 pause
