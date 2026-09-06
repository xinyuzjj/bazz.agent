@echo off
REM ============================================================
REM  BAZZ.AGENT — Dev mode: FastAPI backend + Vite HMR (browser)
REM ============================================================
setlocal
cd /d %~dp0

echo [1/2] Starting backend (FastAPI :8080)...
start "" cmd /c "call .venv\Scripts\python.exe -m uvicorn desktop_app:app --host 127.0.0.1 --port 8080"
timeout /t 3 >nul

echo [2/2] Starting Vite dev server (5173)...
cd frontend
if not exist node_modules (call npm install)
call npm run dev

echo.
echo Open http://localhost:5173 in your browser.
pause