$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath 'E:\数据编织\medfabric'
$env:PYTHONPATH = 'E:\数据编织\_pylibs'
$env:MEDFABRIC_DATA_DIR = (Join-Path $PWD '_live')
python -m uvicorn app:app --host 127.0.0.1 --port 8321 *>&1 | Out-File -FilePath (Join-Path $PWD '_server.log') -Encoding utf8
# probe
