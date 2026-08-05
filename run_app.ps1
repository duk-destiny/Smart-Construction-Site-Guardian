$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path ".venv313\Scripts\python.exe")) {
    Write-Error "未找到 .venv313，请先创建虚拟环境并安装 requirements.txt"
}

& ".\.venv313\Scripts\python.exe" -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
