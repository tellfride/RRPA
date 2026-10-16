# R-RPA - homologacao local, sem Docker.
# Sobe o sistema em http://localhost:8765 com dados ficticios e acessos de teste.
# Uso (PowerShell, na pasta do projeto):
#   powershell -ExecutionPolicy Bypass -File .\homologar.ps1
# Nao use com dados reais: o modo demonstracao cria candidatos e senhas de teste.
# Para parar: Ctrl+C. Para recomecar do zero: apague a pasta "data".

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$porta = 8765

if (Get-NetTCPConnection -LocalPort $porta -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "A porta $porta ja esta em uso. Se for um R-RPA anterior, feche-o; este script nao encerra processos."
    exit 1
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Criando o ambiente Python (so na primeira vez)..."
    py -3.13 -m venv .venv
    if ($LASTEXITCODE -ne 0) { py -3 -m venv .venv }
    & .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt
}

$env:DJANGO_DEBUG = "1"
$env:RRPA_SEED_DEMO = "1"
$env:RRPA_DEMO_SENHA = "demo1234"
$env:RRPA_BASE_URL = "http://localhost:$porta"
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1"
$env:PYTHONIOENCODING = "utf-8"

& .venv\Scripts\python.exe manage.py migrate --noinput
& .venv\Scripts\python.exe manage.py createcachetable
& .venv\Scripts\python.exe manage.py bootstrap

Write-Host ""
Write-Host "Pronto: abra http://localhost:$porta e use um dos botoes de acesso de teste (senha demo1234)."
Write-Host ""
# Segundo plano: localiza no mapa os candidatos novos pelo CEP (so este script o encerra).
New-Item -ItemType Directory -Force -Path data | Out-Null
$worker = Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList "manage.py", "disparar", "--continuo", "--localizar" `
    -PassThru -WindowStyle Hidden -RedirectStandardOutput "data\worker.log" -RedirectStandardError "data\worker.err.log"
try {
    & .venv\Scripts\python.exe manage.py runserver "127.0.0.1:$porta" --noreload
} finally {
    if ($worker -and -not $worker.HasExited) { Stop-Process -Id $worker.Id -Force }
}
