param([int]$Port = 8321)
$ErrorActionPreference = 'Stop'
if (-not $env:MEDFABRIC_OLLAMA_MODEL) { $env:MEDFABRIC_OLLAMA_MODEL = 'gemma4:12b' }
Set-Location -LiteralPath $PSScriptRoot
$pythonExecutable = 'python'
$localPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $localPython) { $pythonExecutable = $localPython }
& $pythonExecutable -c "import fastapi,uvicorn,duckdb,sqlglot,pyarrow,requests,openpyxl,xlrd; from zoneinfo import ZoneInfo; ZoneInfo('Asia/Shanghai')"
if ($LASTEXITCODE -ne 0) { throw "Install project dependencies first: $pythonExecutable -m pip install -r requirements.txt" }
& $pythonExecutable initialize_data.py
if ($LASTEXITCODE -ne 0) { throw 'Data initialization failed; server was not started.' }
& $pythonExecutable -m uvicorn app:app --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
