@echo off
setlocal
cd /d "%~dp0" || goto :failed

if not exist ".venv\Scripts\python.exe" (
    echo Creating the Python environment...
    py -m venv .venv
    if errorlevel 1 goto :failed
)

set "PROJECT_PYTHON=.venv\Scripts\python.exe"
"%PROJECT_PYTHON%" -c "import fastapi, uvicorn, pydantic, httpx, dotenv" >nul 2>&1
if errorlevel 1 (
    echo Installing project dependencies...
    "%PROJECT_PYTHON%" -m pip install -r requirements.txt
    if errorlevel 1 goto :failed
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    if errorlevel 1 goto :failed
    echo Created .env. Set CQA_API_URL before importing a dataset.
)

echo Open http://127.0.0.1:8000 after Uvicorn starts.
echo Press Ctrl+C to stop the server.
"%PROJECT_PYTHON%" -m uvicorn app.api:app --env-file .env --host 127.0.0.1 --port 8000
if errorlevel 1 goto :failed
exit /b 0

:failed
echo Startup failed. Check the message above.
pause
exit /b 1
