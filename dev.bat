@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Install dependencies first.
    pause
    exit /b 1
)

set PYTHONUTF8=1
set APP_ENV=dev

echo [1/3] Starting backend FastAPI :8000 (hot reload) ...
start "Insight-Backend-DEV" /D "%~dp0" cmd /k "chcp 65001 >nul && .venv\Scripts\python.exe -m src.main --api-only"

echo [2/3] Starting frontend Vite :5173 (HMR) ...
start "Insight-Frontend-DEV" /D "%~dp0frontend" cmd /k "pnpm dev"

echo [3/3] Waiting for services, then opening browser ...
timeout /t 8 /nobreak >nul
start http://localhost:5173

echo Done. Backend .py changes auto-reload. Frontend hot-updates.
echo API docs: http://127.0.0.1:8000/docs
timeout /t 5 /nobreak >nul
exit
