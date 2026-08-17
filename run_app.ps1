$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# 自动检测虚拟环境：按优先级查找，都没有则自动创建 .venv313 并安装依赖
$venvCandidates = @(".venv313", ".venv", "venv")
$venvPython = $null
foreach ($name in $venvCandidates) {
    $candidate = Join-Path $root "$name\Scripts\python.exe"
    if (Test-Path $candidate) {
        $venvPython = $candidate
        Write-Host "使用虚拟环境: $name" -ForegroundColor Green
        break
    }
}
if (-not $venvPython) {
    Write-Host "未找到虚拟环境，自动创建 .venv313 ..." -ForegroundColor Yellow
    $venvName = ".venv313"
    $venvDir = Join-Path $root $venvName
    python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Error "创建虚拟环境失败，请确认系统已安装 Python，或手动执行: python -m venv .venv313"
    }
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    Write-Host "安装依赖 requirements.txt ..." -ForegroundColor Yellow
    & $venvPython -m pip install --upgrade pip -q
    & $venvPython -m pip install -r (Join-Path $root "requirements.txt") -q
    if ($LASTEXITCODE -ne 0) {
        Write-Error "依赖安装失败，请检查 requirements.txt 或网络后重试"
    }
    Write-Host "虚拟环境与依赖就绪" -ForegroundColor Green
}

& $venvPython -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501
