param(
    [string]$PythonCommand = "py"
)

$ErrorActionPreference = "Stop"

Write-Host "== Assistente de Decupagem: setup de teste Windows =="
Write-Host ""

function Test-Command {
    param([string]$Command)
    $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

if (-not (Test-Command $PythonCommand)) {
    throw "Python nao encontrado pelo comando '$PythonCommand'. Instale Python 3.11+ ou rode: .\setup_teste_windows.ps1 -PythonCommand python"
}

if (-not (Test-Command "ffmpeg")) {
    Write-Warning "ffmpeg nao encontrado no PATH. Instale FFmpeg manualmente antes do teste real."
}

if (-not (Test-Command "ffprobe")) {
    Write-Warning "ffprobe nao encontrado no PATH. Instale FFmpeg manualmente antes do teste real."
}

if (-not (Test-Path ".\backend\requirements.txt")) {
    throw "Execute este script a partir da raiz do projeto."
}

if (-not (Test-Path ".\.venv")) {
    Write-Host "Criando ambiente virtual em .venv..."
    & $PythonCommand -m venv .venv
}

$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Ambiente virtual criado, mas Python do venv nao foi encontrado em $venvPython"
}

Write-Host "Atualizando pip..."
& $venvPython -m pip install --upgrade pip

Write-Host "Instalando dependencias Python do backend..."
& $venvPython -m pip install -r .\backend\requirements.txt

Write-Host "Validando testes automatizados..."
& $venvPython -m pytest backend

Write-Host ""
Write-Host "Setup concluido."
Write-Host "Para iniciar o backend:"
Write-Host "  .\.venv\Scripts\python.exe .\backend\main.py"
Write-Host ""
Write-Host "Health-check:"
Write-Host "  Invoke-RestMethod http://127.0.0.1:8000/health"
