@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Install dependencies first.
    pause
    exit /b 1
)

if not exist ".env" (
    echo [WARN] .env not found. Chat needs DEEPSEEK_API_KEY.
    echo        See .env.example
    pause
)

set PYTHONUTF8=1
set APP_ENV=prod

echo [1/3] Starting backend FastAPI :8000 ...
start "Insight-Backend" /D "%~dp0" cmd /k "chcp 65001 >nul && .venv\Scripts\python.exe -m src.main --api-only"

echo [2/3] Starting frontend Vite :5173 ...
start "Insight-Frontend" /D "%~dp0frontend" cmd /k "pnpm dev"

echo [3/3] Waiting for services, then opening browser ...
timeout /t 8 /nobreak >nul
start http://localhost:5173

echo Done. Close the two service windows to stop.
timeout /t 3 /nobreak >nul
exit
