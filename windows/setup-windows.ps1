# Homify: Verknuepfungen (Desktop, Startmenue, optional Autostart) und Firewall-Freigabe
param(
    [switch]$Autostart,
    [switch]$RemoveAutostart
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$vbs = Join-Path $PSScriptRoot 'Homify.vbs'
$icon = Join-Path $root 'homify\static\icons\icon.ico'
$shell = New-Object -ComObject WScript.Shell

function New-HomifyLink([string]$path, [string]$arguments) {
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
    $lnk.Arguments = ('"{0}" {1}' -f $vbs, $arguments).Trim()
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = $icon
    $lnk.Description = 'Homify - dein eigenes Spotify'
    $lnk.Save()
}

$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Homify.lnk'

if ($RemoveAutostart) {
    Remove-Item $startupLink -ErrorAction SilentlyContinue
    Write-Host ' Autostart entfernt.'
    exit 0
}

if ($Autostart) {
    New-HomifyLink $startupLink 'background'
    Write-Host ' Autostart eingerichtet: Homify startet ab jetzt unsichtbar bei der Windows-Anmeldung.'
    exit 0
}

New-HomifyLink (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Homify.lnk') ''
New-HomifyLink (Join-Path ([Environment]::GetFolderPath('Programs')) 'Homify.lnk') ''
Write-Host ' Verknuepfung auf dem Desktop und im Startmenue erstellt.'

# Port aus der Konfiguration lesen (Standard 8484)
$port = 8484
$configFile = Join-Path $root 'data\config.json'
if (Test-Path $configFile) {
    try {
        $cfg = Get-Content $configFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($cfg.port) { $port = [int]$cfg.port }
    } catch { }
}

# Firewall: Zugriff aus dem Heimnetz erlauben (fragt einmal nach Admin-Rechten)
$exists = $false
try { $exists = [bool](Get-NetFirewallRule -DisplayName 'Homify' -ErrorAction SilentlyContinue) } catch { }
if (-not $exists) {
    Write-Host " Firewall-Freigabe fuer Port $port (Heimnetz) - bitte die Admin-Abfrage bestaetigen ..."
    $netshArgs = "advfirewall firewall add rule name=Homify dir=in action=allow protocol=TCP localport=$port profile=private,domain"
    try {
        Start-Process -FilePath 'netsh.exe' -ArgumentList $netshArgs -Verb RunAs -Wait -WindowStyle Hidden
        Write-Host ' Firewall-Freigabe eingerichtet.'
    } catch {
        Write-Host ' Firewall-Freigabe uebersprungen. Falls Handy/Laptop Homify nicht erreichen:'
        Write-Host " In der Windows-Firewall eingehend TCP-Port $port fuer private Netzwerke erlauben."
    }
} else {
    Write-Host ' Firewall-Freigabe existiert bereits.'
}
