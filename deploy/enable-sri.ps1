<#
.SYNOPSIS
    Add SRI electronic invoicing to the Odoo server, from the store PC.

.DESCRIPTION
    Run in PowerShell from this folder of a copy of the repository, on a PC
    where `ssh vps` already reaches the server (the same as for the backups).
    It copies the invoicing files to /opt/odoo on the server and runs
    enable-sri.sh there, which:
      1. adds the OCA modules to Odoo's settings,
      2. builds the Odoo image with the invoicing libraries,
      3. (production only) makes a backup of the jats database,
      4. installs or updates the invoicing modules,
      5. configures the till for invoices (journal 001-001, SRI payment codes,
         no self-invoicing QR on receipts),
      6. starts Odoo again.
    The server may ask for your sudo password.

    Do staging first and test there. Production stops the store's Odoo for a
    few minutes: run it after closing the till.

    It never asks for, copies or stores the electronic signature (.p12) or its
    password: you upload them yourself in Odoo afterwards.

.EXAMPLE
    .\deploy\enable-sri.ps1 -Target staging
.EXAMPLE
    .\deploy\enable-sri.ps1 -Target production
#>
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("staging", "production")]
    [string]$Target,
    [string]$Server = "vps",
    [string]$RemoteDir = "/opt/odoo",
    [string]$Source = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$ErrorActionPreference = "Stop"

function Invoke-Native {
    param([string]$What, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

$files = @("deploy\Dockerfile", "deploy\compose.yaml", "deploy\enable-sri.sh", "deploy\sri-configure.py")
foreach ($f in $files + @("addons\l10n_ec_pos_sri\__manifest__.py")) {
    if (-not (Test-Path (Join-Path $Source $f))) { throw "Missing $f in $Source. Use a full copy of the repository." }
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) { throw "ssh was not found. Install the Windows OpenSSH client." }

if ($Target -eq "production") {
    Write-Host "This installs invoicing in the store's Odoo (jats)." -ForegroundColor Yellow
    Write-Host "Odoo stops for a few minutes. A backup is made first."
    $answer = Read-Host "Type SI to continue"
    if ($answer -cne "SI") { Write-Host "Cancelled."; exit 1 }
}

Write-Host "== Copying the invoicing files to ${Server}:${RemoteDir}"
$paths = $files | ForEach-Object { Join-Path $Source $_ }
Invoke-Native "Copying files" { scp -q @paths "${Server}:${RemoteDir}/" }
Invoke-Native "Copying the module" { scp -q -r (Join-Path $Source "addons\l10n_ec_pos_sri") "${Server}:${RemoteDir}/addons/" }

Write-Host "== Running enable-sri.sh $Target on the server"
# Files copied from Windows may carry CRLF line endings: strip them first.
$remote = "cd $RemoteDir && sed -i 's/\r`$//' enable-sri.sh sri-configure.py Dockerfile compose.yaml " +
          "&& find addons/l10n_ec_pos_sri -type f \( -name '*.py' -o -name '*.xml' -o -name '*.js' \) -exec sed -i 's/\r`$//' {} + " +
          "&& bash enable-sri.sh $Target"
Invoke-Native "enable-sri.sh" { ssh -t $Server $remote }

Write-Host ""
Write-Host "Done. Follow the guide: upload the .p12 in Odoo and run the test sales." -ForegroundColor Green
