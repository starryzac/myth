@echo off
python "%~dp0scripts\tasks.py" %*
exit /b %ERRORLEVEL%
