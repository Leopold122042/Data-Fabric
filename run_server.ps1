param([int]$Port = 8322)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonExecutable = 'python'
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $localPython) { $pythonExecutable = $localPython }
& $pythonExecutable -c 'import fastapi,uvicorn,duckdb,sqlglot,pyarrow,requests,openpyxl,xlrd'
if ($LASTEXITCODE -ne 0) { throw "Install project dependencies first: $pythonExecutable -m pip install -r requirements.txt" }
& $pythonExecutable initialize_data.py
if ($LASTEXITCODE -ne 0) { throw 'Data initialization failed; server was not started.' }
Start-Process -FilePath "http://127.0.0.1:$Port/" | Out-Null
& $pythonExecutable -m uvicorn app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
