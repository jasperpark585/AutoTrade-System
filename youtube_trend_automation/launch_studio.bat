@echo off
setlocal
set PROJECT_ROOT=%~dp0
if not exist "%PROJECT_ROOT%\.venv\Scripts\python.exe" (
  python -m venv "%PROJECT_ROOT%\.venv"
)
"%PROJECT_ROOT%\.venv\Scripts\python.exe" -m pip install --upgrade pip
"%PROJECT_ROOT%\.venv\Scripts\python.exe" -m pip install -r "%PROJECT_ROOT%\requirements.txt"
"%PROJECT_ROOT%\.venv\Scripts\python.exe" "%PROJECT_ROOT%\studio_launcher.py"
