@echo off
rem MedFabric - data fabric medical demo platform (Part-1 architecture model)
cd /d %~dp0
pushd .. >nul 2>nul
set "PYTHONPATH=%CD%\_pylibs"
popd >nul 2>nul
python -c "import fastapi,uvicorn" 2>nul || python -m pip install --target %PYTHONPATH% fastapi uvicorn
start "" http://127.0.0.1:8321/
python -m uvicorn app:app --host 127.0.0.1 --port 8321
pause
