@echo off
setlocal
cd /d "%~dp0"
set "MEDFABRIC_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "MEDFABRIC_PYTHON=.venv\Scripts\python.exe"
echo Using Python: %MEDFABRIC_PYTHON%
"%MEDFABRIC_PYTHON%" -c "import fastapi,uvicorn,duckdb,sqlglot,pyarrow,requests,openpyxl,xlrd"
if errorlevel 1 (
    echo.
    echo Install dependencies using the selected Python:
    echo "%MEDFABRIC_PYTHON%" -m pip install -r requirements.txt
    goto :failed
)
"%MEDFABRIC_PYTHON%" initialize_data.py
if errorlevel 1 goto :failed
start "" http://127.0.0.1:8321/
"%MEDFABRIC_PYTHON%" -m uvicorn app:app --host 127.0.0.1 --port 8321
if errorlevel 1 goto :failed
pause
exit /b 0

:failed
echo.
echo MedFabric did not start. Review the error above; existing data has been preserved.
pause
exit /b 1
