$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python 3.10+ no está en PATH. Instala Python y vuelve a ejecutar este script.'
}
if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'Node.js 20.19+ no está en PATH. Instala Node.js LTS y vuelve a ejecutar este script.'
}
$nodeVersion = [version]((node --version).TrimStart('v').Split('-')[0])
if ($nodeVersion -lt [version]'20.19') { throw "Node.js $nodeVersion es antiguo; RigM requiere 20.19+ LTS." }

if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'No pude crear el entorno virtual de Python.' }
}
$env:TEMP = Join-Path $projectRoot 'work\pip-temp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
& .\.venv\Scripts\python.exe -m pip install --upgrade pip 'setuptools>=68' wheel
if ($LASTEXITCODE -ne 0) { throw 'No pude instalar pip/setuptools en el entorno virtual.' }
& .\.venv\Scripts\python.exe -m pip install --no-build-isolation -e '.[rigging,avatar3d]'
if ($LASTEXITCODE -ne 0) { throw 'No pude instalar RigM y sus dependencias 3D en el entorno virtual.' }

Push-Location viewer3d
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci no pudo instalar las dependencias del visor.' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar el visor 3D.' }
} finally {
    Pop-Location
}

Write-Host ''
Write-Host 'Instalación lista. Ejecuta:'
Write-Host '  .\.venv\Scripts\rigm.exe'
Write-Host '  .\.venv\Scripts\rigmctl.exe --help'
Write-Host 'El auto-rig y los clips 3D del visor funcionan sin Blender.'
