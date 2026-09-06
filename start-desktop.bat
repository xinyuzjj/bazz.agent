@echo off
REM ============================================================
REM  BAZZ.AGENT · Binance Agent OS Alpha Scout — Desktop launcher
REM  Electron shell + React build + FastAPI backend (auto-started)
REM ============================================================
setlocal
cd /d %~dp0

REM --- 1. Python backend deps ---------------------------------
if not exist .venv\Scripts\python.exe (
  echo [1/4] Creating Python venv...
  python -m venv .venv || goto :fail
)
if not exist .venv\Lib\site-packages\fastapi (
  echo [1/4] Installing backend deps...
  .venv\Scripts\python.exe -m pip install -r requirements.txt -q || goto :fail
) else (
  echo [1/4] Backend deps OK ^(skip^)
)

REM --- 2. Frontend deps (Electron binary) ---------------------
cd frontend
if not exist node_modules\electron\dist\electron.exe (
  echo [2/4] Installing frontend deps ^(this may take a while^)...
  call npm install || goto :fail
) else (
  echo [2/4] Frontend deps OK ^(skip^)
)

REM --- 3. React build -----------------------------------------
if not exist dist\index.html (
  echo [3/4] Building React app...
  call npm run build || goto :fail
) else (
  echo [3/4] React build OK ^(skip^)
)

REM --- 4. Launch Electron -------------------------------------
echo [4/4] Launching BAZZ.AGENT desktop app...
REM  确保不被外部 ELECTRON_RUN_AS_NODE 环境干扰（某些宿主 shell 会注入）
set ELECTRON_RUN_AS_NODE=
set NODE_OPTIONS=
call "%~dp0frontend\node_modules\.bin\electron.cmd" .
exit /b %errorlevel%

:fail
echo.
echo *** Setup failed. See messages above. ***
pause
exit /b 1