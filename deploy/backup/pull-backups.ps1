<#
.SYNOPSIS
    Download new backup bundles from the server and keep a local history.

.DESCRIPTION
    Runs on the off-server backup PC (the store's POS PC) from a scheduled
    task. It connects as the read-only "bkpull" SFTP user, downloads every
    complete bundle (one that has its .sha256 file) it does not have yet,
    verifies the checksum, and deletes local bundles older than -KeepDays.

    Exit codes: 0 = ok, 1 = error, 2 = ok but the newest backup is too old.

    On 1 or 2 it also sends a JSON POST {"text": ..., "host": ...} to
    -AlertUrl, read by default from alert-url.txt next to this script (the
    same webhook the server uses, for example an n8n workflow).
#>
param(
    [string]$HostName = "odoo.jatssolutions.com",
    [int]$Port = 2222,
    [string]$User = "bkpull",
    [string]$KeyFile = (Join-Path $PSScriptRoot "bkpull_ed25519"),
    [string]$KnownHosts = (Join-Path $PSScriptRoot "known_hosts"),
    [string]$Destination = "D:\Respaldos\droplet",
    [int]$KeepDays = 60,
    [int]$KeepAtLeast = 7,
    [int]$MaxAgeHours = 48,
    [string]$AlertUrl = ""
)

$ErrorActionPreference = "Stop"
$NamePattern = '^backup-(\d{4}-\d{2}-\d{2})_(\d{4})\.tar\.gz$'
$LogFile = Join-Path $Destination "pull.log"

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
    Write-Output $line
}

$AlertFile = Join-Path $PSScriptRoot "alert-url.txt"
if (-not $AlertUrl -and (Test-Path $AlertFile)) {
    $AlertUrl = (Get-Content $AlertFile -TotalCount 1).Trim()
}

function Send-Alert([string]$Text) {
    if (-not $AlertUrl) { return }
    try {
        $body = @{ text = $Text; host = $env:COMPUTERNAME } | ConvertTo-Json -Compress
        $bytes = [Text.Encoding]::UTF8.GetBytes($body)
        Invoke-RestMethod -Uri $AlertUrl -Method Post -Body $bytes `
            -ContentType "application/json; charset=utf-8" -TimeoutSec 20 | Out-Null
    } catch {
        Write-Log "WARNING: could not send the alert: $($_.Exception.Message)"
    }
}

function Stop-Run([int]$Code, [string]$Message) {
    Write-Log $Message
    if ($Code -ne 0) {
        Send-Alert "Copia de respaldos en $($env:COMPUTERNAME): $Message. Revisa $LogFile"
    }
    exit $Code
}

function Invoke-Sftp([string[]]$Commands) {
    $batch = New-TemporaryFile
    try {
        Set-Content -Path $batch -Value $Commands -Encoding ascii
        $sftpArgs = @(
            "-F", "none", "-b", $batch.FullName, "-P", $Port, "-i", $KeyFile,
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=$KnownHosts",
            "-o", "ConnectTimeout=30", "$User@$HostName"
        )
        $output = & sftp.exe @sftpArgs 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "sftp failed (exit $LASTEXITCODE): $($output -join ' | ')"
        }
        return $output
    } finally {
        Remove-Item $batch -Force -ErrorAction SilentlyContinue
    }
}

function Get-BundleDate([string]$Name) {
    if ($Name -match $NamePattern) {
        return [datetime]::ParseExact("$($Matches[1]) $($Matches[2])", "yyyy-MM-dd HHmm", $null)
    }
    return $null
}

function Test-Bundle([string]$Bundle, [string]$ShaFile) {
    $expected = ((Get-Content $ShaFile -TotalCount 1) -split '\s+')[0].ToLower()
    $actual = (Get-FileHash -Algorithm SHA256 -Path $Bundle).Hash.ToLower()
    return $expected -eq $actual
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$incoming = Join-Path $Destination ".incoming"
New-Item -ItemType Directory -Force -Path $incoming | Out-Null

try {
    # 1. List complete bundles on the server.
    $listing = Invoke-Sftp @("ls -1") | ForEach-Object { ($_ -split '/')[-1].Trim() }
    $remote = $listing | Where-Object { $_ -match $NamePattern -and ($listing -contains "$_.sha256") }
    $missing = @($remote | Where-Object { -not (Test-Path (Join-Path $Destination $_)) } | Sort-Object)
    Write-Log "server has $(@($remote).Count) complete bundles, $($missing.Count) new"

    # 2. Download each new one into .incoming, verify, then move into place.
    $failed = 0
    foreach ($name in $missing) {
        $ok = $false
        foreach ($attempt in 1..2) {
            $bundle = Join-Path $incoming $name
            $sha = "$bundle.sha256"
            # sftp batch files treat "\" as an escape character; use "/".
            $toBundle = $bundle -replace '\\', '/'
            $toSha = $sha -replace '\\', '/'
            Invoke-Sftp @("get `"$name`" `"$toBundle`"", "get `"$name.sha256`" `"$toSha`"") | Out-Null
            if (Test-Bundle $bundle $sha) {
                Move-Item -Force $sha (Join-Path $Destination "$name.sha256")
                Move-Item -Force $bundle (Join-Path $Destination $name)
                $size = [math]::Round((Get-Item (Join-Path $Destination $name)).Length / 1MB, 1)
                Write-Log "downloaded $name ($size MB, checksum ok)"
                $ok = $true
                break
            }
            Write-Log "checksum mismatch for $name (attempt $attempt)"
            Remove-Item -Force $bundle, $sha -ErrorAction SilentlyContinue
        }
        if (-not $ok) { $failed++ }
    }

    # 3. Retention: drop bundles older than KeepDays, but always keep the
    #    newest KeepAtLeast so a long outage never empties the folder.
    $local = Get-ChildItem $Destination -File | Where-Object { $_.Name -match $NamePattern } |
        Sort-Object { Get-BundleDate $_.Name } -Descending
    $cutoff = (Get-Date).AddDays(-$KeepDays)
    $local | Select-Object -Skip $KeepAtLeast | Where-Object { (Get-BundleDate $_.Name) -lt $cutoff } |
        ForEach-Object {
            Remove-Item -Force $_.FullName, "$($_.FullName).sha256" -ErrorAction SilentlyContinue
            Write-Log "deleted old $($_.Name)"
        }

    # 4. Health check: is the newest backup recent enough?
    $newest = Get-ChildItem $Destination -File | Where-Object { $_.Name -match $NamePattern } |
        Sort-Object { Get-BundleDate $_.Name } -Descending | Select-Object -First 1
    if ($failed -gt 0) {
        Stop-Run 1 "ERROR: $failed bundle(s) failed verification"
    }
    if (-not $newest) {
        Stop-Run 2 "WARNING: no backups on this PC yet"
    }
    # Bundle names use server time (UTC).
    $ageHours = [math]::Round(((Get-Date).ToUniversalTime() - (Get-BundleDate $newest.Name)).TotalHours)
    if ($ageHours -gt $MaxAgeHours) {
        Stop-Run 2 "WARNING: newest backup $($newest.Name) is $ageHours hours old"
    }
    Stop-Run 0 "ok: newest backup $($newest.Name) ($ageHours h old)"
} catch {
    Stop-Run 1 "ERROR: $($_.Exception.Message)"
}
