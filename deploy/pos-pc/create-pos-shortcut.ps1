<#
.SYNOPSIS
    Create the "Caja Josheps" desktop shortcut on the store's POS PC.

.DESCRIPTION
    Run once in the Windows account the cashiers use (a standard account; no
    administrator rights needed). The shortcut opens the point of sale in its
    own Chrome window with a separate Chrome profile, so:
      - clearing the history of the everyday Chrome never deletes sales that
        are waiting to be sent while the internet is down;
      - the till has no tabs, address bar or incognito button.
    Running it again replaces the shortcut and keeps the profile.
#>
param(
    [string]$Url = "https://odoo.jatssolutions.com/pos/ui",
    [string]$Name = "Caja Josheps",
    [string]$ShortcutDir = [Environment]::GetFolderPath("Desktop"),
    [string]$ProfileDir = (Join-Path $env:LOCALAPPDATA "CajaPOS\Chrome")
)

$ErrorActionPreference = "Stop"

$candidates = @(
    (Join-Path $env:ProgramFiles "Google\Chrome\Application\chrome.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Google\Chrome\Application\chrome.exe"),
    (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
)
$chrome = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $chrome) {
    throw "No encuentro Google Chrome. Instálalo desde https://www.google.com/chrome y vuelve a correr este script."
}

New-Item -ItemType Directory -Force -Path $ProfileDir, $ShortcutDir | Out-Null

$link = Join-Path $ShortcutDir "$Name.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $chrome
$shortcut.Arguments = "--user-data-dir=`"$ProfileDir`" --no-first-run --no-default-browser-check --app=$Url"
$shortcut.WorkingDirectory = Split-Path $chrome
$shortcut.IconLocation = "$chrome,0"
$shortcut.Description = "Punto de venta"
$shortcut.Save()

Write-Host "Listo: acceso directo '$Name' en $ShortcutDir"
Write-Host "Ábrelo, inicia sesión una vez con el usuario de la caja y deja la sesión abierta."
Write-Host "No borres los datos de este perfil: $ProfileDir"
