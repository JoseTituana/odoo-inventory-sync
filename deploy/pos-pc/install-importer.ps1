<#
.SYNOPSIS
    Install the odoo-sync importer (catalog counts and labels) on a Windows PC.

.DESCRIPTION
    Run in PowerShell, in the Windows account of the person who imports counts
    and prints labels (no administrator rights needed), from this folder of a
    copy of the repository. It:
      1. finds Python 3.10 or newer (install it first with
         `winget install -e --id Python.Python.3.12 --scope user`);
      2. creates a private environment in %LOCALAPPDATA%\odoo-sync\venv and
         installs the importer from this copy of the repository;
      3. adds that environment to the user's PATH, so `odoo-sync` works in any
         new PowerShell window and from any folder;
      4. creates the settings file %APPDATA%\odoo-sync\.env (readable only by
         this user) if it does not exist, and opens it in Notepad to fill in
         the Odoo URL, database, user and API key.
    Running it again updates the importer and keeps the settings.
#>
param(
    [string]$Source = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "odoo-sync"),
    [string]$SettingsDir = (Join-Path $env:APPDATA "odoo-sync"),
    [switch]$NoEditor,
    [switch]$NoPath
)

$ErrorActionPreference = "Stop"

function Find-Python {
    foreach ($cmd in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = Get-Command $cmd[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $checkArgs = @($cmd | Select-Object -Skip 1) + @("-c", "import sys; print(sys.version_info >= (3, 10))")
        $ok = & $exe.Source @checkArgs 2>$null
        if ($LASTEXITCODE -eq 0 -and $ok -eq "True") { return ,$cmd }
    }
    return $null
}

if (-not (Test-Path (Join-Path $Source "pyproject.toml"))) {
    throw "No encuentro pyproject.toml en $Source. Corre el script desde deploy\pos-pc de una copia del repositorio."
}

# 1. Python
$python = Find-Python
if (-not $python) {
    throw "Falta Python 3.10 o más nuevo. Instálalo con:  winget install -e --id Python.Python.3.12 --scope user  y abre una ventana nueva de PowerShell."
}

# 2. Private environment with the importer
$venv = Join-Path $InstallDir "venv"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    $pyArgs = @($python | Select-Object -Skip 1) + @("-m", "venv", $venv)
    & $python[0] @pyArgs
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno de Python en $venv" }
}
$venvPy = Join-Path $venv "Scripts\python.exe"
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet --upgrade $Source
if ($LASTEXITCODE -ne 0) { throw "pip no pudo instalar el importador" }
$exe = Join-Path $venv "Scripts\odoo-sync.exe"
if (-not (Test-Path $exe)) { throw "No se creó $exe" }

# 3. PATH for this user
$scripts = Join-Path $venv "Scripts"
if (-not $NoPath) {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($userPath -split ";" | Where-Object { $_ })
    if ($parts -notcontains $scripts) {
        [Environment]::SetEnvironmentVariable("Path", (($parts + $scripts) -join ";"), "User")
        Write-Host "Agregado al PATH: $scripts (abre una ventana nueva de PowerShell)"
    }
}

# 4. Settings file, private to this user
New-Item -ItemType Directory -Force -Path $SettingsDir | Out-Null
$settings = Join-Path $SettingsDir ".env"
if (-not (Test-Path $settings)) {
    $template = @(
        "# Datos de conexión del importador. No compartas este archivo.",
        "ODOO_URL=https://odoo.jatssolutions.com",
        "ODOO_DB=jats",
        "ODOO_USER=",
        "# Clave API del usuario (Odoo > Mi perfil > Seguridad de la cuenta > Nueva clave API), no la contraseña.",
        "ODOO_PASSWORD="
    )
    [IO.File]::WriteAllLines($settings, $template)
    $me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $settings /inheritance:r /grant:r "*${me}:F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls falló en $settings" }
    Write-Host "Creado $settings"
}
if (-not $NoEditor) {
    Start-Process notepad.exe $settings
}

# Check that the command starts (piping into Select-Object -First would cut it
# off and leave a failed exit code behind).
$help = & $exe catalog --help
if ($LASTEXITCODE -ne 0) { throw "odoo-sync no arranca" }
Write-Host $help[0]
Write-Host ""
Write-Host "Listo. Después de llenar $settings prueba en una ventana nueva:"
Write-Host "  odoo-sync labels --dry-run"
