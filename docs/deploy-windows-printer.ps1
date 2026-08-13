<#
.SYNOPSIS
    Add the janus-print inspected queue to a Windows workstation.

.DESCRIPTION
    Windows does not discover CUPS queues over Bonjour/DNS-SD — it uses WSD, which CUPS
    does not speak. macOS and Linux clients find the queue automatically once
    docker-compose.discovery.yml is running; Windows clients need this push.

    Run per-machine as Administrator, or deploy through Group Policy (Computer
    Configuration > Preferences > Control Panel Settings > Printers) or Intune as a
    platform script.

    The queue installed here points at the CUPS server, NOT at the printer. That is the
    whole point: jobs must reach the spooler to be inspected. If a workstation also has a
    direct TCP/IP port to the printer on 9100, it will bypass inspection entirely — see
    -RemoveDirectPorts.

.PARAMETER Server
    Print server hostname or IP.

.PARAMETER Port
    CUPS port. 631 with host networking (docker-compose.discovery.yml), 6631 with the
    default bridge networking.

.PARAMETER Queue
    CUPS queue name, e.g. office-printer.

.PARAMETER PrinterName
    Display name shown to the user.

.PARAMETER RemoveDirectPorts
    Also remove any existing direct TCP/IP printer ports pointing at PrinterAddress,
    which would otherwise let the user print around the inspector.

.PARAMETER PrinterAddress
    The physical printer's IP, used only with -RemoveDirectPorts.

.EXAMPLE
    .\deploy-windows-printer.ps1 -Server 172.18.100.3 -Port 631 -Queue office-printer

.EXAMPLE
    .\deploy-windows-printer.ps1 -Server print.corp.local -Queue office-printer `
        -RemoveDirectPorts -PrinterAddress 172.18.104.250
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [int]$Port = 631,
    [Parameter(Mandatory = $true)][string]$Queue,
    [string]$PrinterName = "Office Printer (Inspected)",
    [switch]$RemoveDirectPorts,
    [string]$PrinterAddress
)

$ErrorActionPreference = "Stop"

function Write-Step($message) { Write-Host "==> $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "!!  $message" -ForegroundColor Yellow }

if (-not ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this as Administrator."
}

# IPP printing is an optional Windows feature and is off by default on many images.
Write-Step "Ensuring the Internet Printing Client feature is enabled"
$feature = Get-WindowsOptionalFeature -Online -FeatureName "Printing-Foundation-InternetPrinting-Client" `
    -ErrorAction SilentlyContinue
if ($feature -and $feature.State -ne "Enabled") {
    Enable-WindowsOptionalFeature -Online -FeatureName "Printing-Foundation-InternetPrinting-Client" `
        -NoRestart | Out-Null
    Write-Host "    enabled (a reboot may be required)"
} else {
    Write-Host "    already enabled"
}

$uri = "http://${Server}:${Port}/printers/${Queue}"

Write-Step "Checking the queue is reachable at $uri"
try {
    $response = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 10
    Write-Host "    server responded $($response.StatusCode)"
} catch {
    # CUPS answers 426/401 to a plain GET on some builds; only a connection failure is fatal.
    if ($_.Exception.Response) {
        Write-Host "    server responded $([int]$_.Exception.Response.StatusCode) (reachable)"
    } else {
        throw "Cannot reach $uri — check firewall and that CUPS allows this subnet: $($_.Exception.Message)"
    }
}

Write-Step "Adding printer port"
if (-not (Get-PrinterPort -Name $uri -ErrorAction SilentlyContinue)) {
    Add-PrinterPort -Name $uri
    Write-Host "    added $uri"
} else {
    Write-Host "    port already present"
}

Write-Step "Adding printer '$PrinterName'"
$driver = "Microsoft IPP Class Driver"
if (-not (Get-PrinterDriver -Name $driver -ErrorAction SilentlyContinue)) {
    Add-PrinterDriver -Name $driver
}
if (Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue) {
    Set-Printer -Name $PrinterName -PortName $uri
    Write-Host "    updated existing printer"
} else {
    Add-Printer -Name $PrinterName -DriverName $driver -PortName $uri
    Write-Host "    added"
}

if ($RemoveDirectPorts) {
    if (-not $PrinterAddress) { throw "-RemoveDirectPorts requires -PrinterAddress" }

    Write-Step "Removing direct ports to $PrinterAddress that would bypass inspection"
    $bypass = Get-PrinterPort | Where-Object {
        $_.PrinterHostAddress -eq $PrinterAddress -or $_.Name -like "*$PrinterAddress*"
    }
    foreach ($port in $bypass) {
        $users = Get-Printer | Where-Object { $_.PortName -eq $port.Name }
        foreach ($printer in $users) {
            Write-Warn "removing printer '$($printer.Name)' — it printed directly to the device"
            Remove-Printer -Name $printer.Name -ErrorAction SilentlyContinue
        }
        Remove-PrinterPort -Name $port.Name -ErrorAction SilentlyContinue
        Write-Host "    removed port $($port.Name)"
    }
    if (-not $bypass) { Write-Host "    none found" }
}

Write-Step "Done"
Write-Host "Printer '$PrinterName' now routes through the inspector at $uri"
Write-Warn "Removing local direct ports does not stop a user re-adding one. Block TCP 9100"
Write-Warn "to printers at the switch — that is the only durable control."
