<#
.SYNOPSIS
    Set up the off-server backup copy on a Windows PC (the store's POS PC).

.DESCRIPTION
    Run once, in PowerShell "Run as administrator", from the folder that
    contains this script and pull-backups.ps1. It:
      1. copies pull-backups.ps1 to C:\OdooBackup;
      2. restricts C:\OdooBackup and the backup folder to SYSTEM and
         Administrators, so standard (staff) accounts cannot read them;
      3. creates a dedicated SSH key that only this PC uses;
      4. records the server's host key after you confirm its fingerprint;
      5. registers a scheduled task that runs as SYSTEM at startup and
         every 3 hours.
    Running it again keeps the existing key and replaces the task.
#>
param(
    [string]$HostName = "odoo.jatssolutions.com",
    [int]$Port = 2222,
    [string]$Destination = "D:\Respaldos\droplet",
    [string]$InstallDir = "C:\OdooBackup",
    # Published fingerprint of the server's ED25519 host key.
    [string]$ExpectedFingerprint = "SHA256:Cqexbydo4RZGUG6gcNrFrhFMFPyaFHVQcUG4xI1dHTA"
)

$ErrorActionPreference = "Stop"
$TaskName = "Odoo backup download"

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).
    IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Run this script in PowerShell as administrator." }
foreach ($tool in "ssh-keygen.exe", "ssh-keyscan.exe", "sftp.exe") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool not found. Install the Windows feature 'OpenSSH Client'."
    }
}

function Set-PrivateAcl([string]$Path) {
    # Remove inherited permissions; only SYSTEM and Administrators keep access.
    & icacls.exe $Path /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed on $Path" }
}

# 1-2. Folders and permissions.
New-Item -ItemType Directory -Force -Path $InstallDir, $Destination | Out-Null
Set-PrivateAcl $InstallDir
Set-PrivateAcl $Destination
Copy-Item -Force (Join-Path $PSScriptRoot "pull-backups.ps1") $InstallDir

# 3. Dedicated key without a passphrase (the folder ACL protects it,
#    and the server only lets it read backups).
$key = Join-Path $InstallDir "bkpull_ed25519"
if (-not (Test-Path $key)) {
    $comment = "bkpull@$env:COMPUTERNAME"
    & cmd.exe /c "ssh-keygen.exe -q -t ed25519 -N `"`" -C $comment -f `"$key`""
    if ($LASTEXITCODE -ne 0) { throw "ssh-keygen failed" }
}

# 4. Server host key, checked against the expected fingerprint.
# (ssh-keyscan on Windows is too old for the server's key exchange, so we
# let ssh record the key on a first connection and verify it right after.)
$known = Join-Path $InstallDir "known_hosts"
$candidate = "$known.new"
Remove-Item $candidate -ErrorAction SilentlyContinue
& ssh.exe -F none -p $Port -i $key -o IdentitiesOnly=yes -o BatchMode=yes `
    -o HostKeyAlgorithms=ssh-ed25519 -o StrictHostKeyChecking=accept-new `
    -o "UserKnownHostsFile=$candidate" "bkpull@$HostName" exit 2>$null | Out-Null
if (-not (Test-Path $candidate)) { throw "could not reach $HostName on port $Port" }
$fingerprint = ((& ssh-keygen.exe -lf $candidate) -split ' ')[1]
if ($fingerprint -ne $ExpectedFingerprint) {
    Remove-Item $candidate
    throw "host key fingerprint $fingerprint does not match $ExpectedFingerprint. Do not continue."
}
Move-Item -Force $candidate $known

# 5. Scheduled task as SYSTEM.
$script = Join-Path $InstallDir "pull-backups.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Destination `"$Destination`" -HostName $HostName -Port $Port"
$atStartup = New-ScheduledTaskTrigger -AtStartup
$atStartup.Delay = "PT10M"
$every3h = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours(9) `
    -RepetitionInterval (New-TimeSpan -Hours 3)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $atStartup, $every3h `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Output ""
Write-Output "Installed. Add this public key on the server with setup-pull-user.sh:"
Write-Output ""
Get-Content "$key.pub"
Write-Output ""
Write-Output "Then test with:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Output "and read the log: $Destination\pull.log"
