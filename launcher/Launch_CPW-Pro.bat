@echo off
setlocal ENABLEDELAYEDEXPANSION
cd /d "%~dp0.."

set "_PY=myenv\Scripts\python.exe"
if exist "%_PY%" (
  "%_PY%" -m cpwpro
  set _EC=!ERRORLEVEL!
) else (
  python -m cpwpro
  set _EC=!ERRORLEVEL!
)

if not "!_EC!"=="0" (
  echo.
  echo [CPW-Pro] 退出码 !_EC!. 若双击闪退，请用终端运行本 bat 查看报错。
  pause
)
exit /b !_EC!
