<#
.SYNOPSIS
    Install the janus-print inspected queue on a Windows machine. Built for unattended
    deployment via Group Policy startup script or Intune.

.DESCRIPTION
    Windows cannot discover CUPS queues — it uses WS-Discovery, which CUPS does not speak,
    and no daemon changes that (wsdd advertises hosts for file sharing, not printers). So
    the printer is deployed rather than found.

    Written for the constraints of running as SYSTEM at boot, every boot:

      * Idempotent. Existing and correct means do nothing. Safe to run on every startup.
      * Never interactive, never throws into the boot sequence. Failures are logged and
        the script exits non-zero, so GPO reports them without blocking login.
      * Logs to a file and the Application event log, because nobody is watching a console.
      * Uses the in-box Microsoft IPP Class Driver, so no driver package is needed. This
        matters after the PrintNightmare hardening: non-admin driver installation is
        blocked by default, but SYSTEM at startup is not affected.

    The queue installed points at the print SERVER, not at the printer. That is the whole
    point — jobs must reach the spooler to be inspected. A workstation with a direct
    TCP/IP port to the device bypasses inspection entirely, which -RemoveDirectPorts
    cleans up.

.PARAMETER Server
    Print server hostname or IP, e.g. 10.0.1.5

.PARAMETER Queue
    CUPS queue name, e.g. local-office

.PARAMETER Port
    CUPS port. 631 with host networking, 6631 with default bridge networking.

.PARAMETER PrinterName
    Name shown to users. This is what they pick in the print dialog, so name it by
    location or department, not by model.

.PARAMETER SetDefault
    Make it the default printer for new user profiles.

.PARAMETER RemoveDirectPorts
    Remove printer ports pointing straight at PrinterAddress, which bypass inspection.

.PARAMETER PrinterAddress
    The physical printer's IP. Required with -RemoveDirectPorts.

.EXAMPLE
    .\Deploy-JanusPrinter.ps1 -Server 10.0.1.5 -Queue local-office -PrinterName "Office (Inspected)"

.EXAMPLE
    .\Deploy-JanusPrinter.ps1 -Server 10.0.1.5 -Queue local-office `
        -PrinterName "Office (Inspected)" -SetDefault `
        -RemoveDirectPorts -PrinterAddress 10.0.1.80
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [Parameter(Mandatory = $true)][string]$Queue,
    [int]$Port = 631,
    [string]$PrinterName = "Office Printer (Inspected)",
    [switch]$SetDefault,
    [switch]$RemoveDirectPorts,
    [string]$PrinterAddress,
    [string]$LogPath = "$env:ProgramData\janus-print\deploy.log"
)

$ErrorActionPreference = "Stop"
$script:ExitCode = 0

# --- logging -----------------------------------------------------------------

function Write-Log {
    param([string]$Message, [ValidateSet("Info", "Warning", "Error")][string]$Level = "Info")

    $line = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line

    try {
        $directory = Split-Path -Parent $LogPath
        if (-not (Test-Path $directory)) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
        Add-Content -Path $LogPath -Value $line -ErrorAction SilentlyContinue
    } catch { }

    if ($Level -ne "Info") {
        try {
            if (-not [System.Diagnostics.EventLog]::SourceExists("janus-print")) {
                New-EventLog -LogName Application -Source "janus-print" -ErrorAction SilentlyContinue
            }
            $entryType = if ($Level -eq "Error") { "Error" } else { "Warning" }
            Write-EventLog -LogName Application -Source "janus-print" -EventId 9001 `
                -EntryType $entryType -Message $Message -ErrorAction SilentlyContinue
        } catch { }
    }
}

function Fail {
    param([string]$Message)
    Write-Log $Message -Level Error
    $script:ExitCode = 1
}

Write-Log "=== janus-print printer deployment starting ==="
Write-Log "server=$Server queue=$Queue port=$Port name='$PrinterName'"

# --- preconditions -----------------------------------------------------------

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Fail "Not running elevated. Deploy via GPO startup script (runs as SYSTEM) or run as Administrator."
    exit $script:ExitCode
}

# IPP printing is an optional feature and is absent on many images. Without it the
# Microsoft IPP Class Driver is missing and the install fails with an unhelpful error.
try {
    $feature = Get-WindowsOptionalFeature -Online `
        -FeatureName "Printing-Foundation-InternetPrinting-Client" -ErrorAction SilentlyContinue
    if ($feature -and $feature.State -ne "Enabled") {
        Write-Log "Enabling Internet Printing Client feature"
        Enable-WindowsOptionalFeature -Online `
            -FeatureName "Printing-Foundation-InternetPrinting-Client" -NoRestart -ErrorAction Stop | Out-Null
        Write-Log "Feature enabled (a reboot may be required before the driver appears)" -Level Warning
    }
} catch {
    Write-Log "Could not verify the Internet Printing Client feature: $($_.Exception.Message)" -Level Warning
}

# --- reachability ------------------------------------------------------------

$uri = "http://${Server}:${Port}/printers/${Queue}"

try {
    $test = Test-NetConnection -ComputerName $Server -Port $Port -WarningAction SilentlyContinue
    if (-not $test.TcpTestSucceeded) {
        # Not fatal: a laptop off the network at boot should retry next time, not error
        # loudly every morning.
        Write-Log "Print server ${Server}:${Port} is not reachable right now; will retry at next startup" -Level Warning
        exit 0
    }
} catch {
    Write-Log "Could not test connectivity to ${Server}:${Port}: $($_.Exception.Message)" -Level Warning
}

# --- driver ------------------------------------------------------------------

$driver = "Microsoft IPP Class Driver"
try {
    if (-not (Get-PrinterDriver -Name $driver -ErrorAction SilentlyContinue)) {
        Write-Log "Adding printer driver '$driver'"
        Add-PrinterDriver -Name $driver -ErrorAction Stop
    }
} catch {
    Write-Log "Could not add '$driver': $($_.Exception.Message). Falling back to Generic / Text Only is NOT safe for this queue." -Level Warning
    Fail "No usable IPP driver. Ensure the Internet Printing Client feature is enabled and reboot."
    exit $script:ExitCode
}

# --- port --------------------------------------------------------------------

try {
    if (-not (Get-PrinterPort -Name $uri -ErrorAction SilentlyContinue)) {
        Write-Log "Adding printer port $uri"
        Add-PrinterPort -Name $uri -ErrorAction Stop
    } else {
        Write-Log "Port already present"
    }
} catch {
    Write-Log "Add-PrinterPort failed ($($_.Exception.Message)); trying printui fallback" -Level Warning
    # Older builds will not create an internet port through Add-PrinterPort.
    $arguments = "printui.dll,PrintUIEntry /if /b `"$PrinterName`" /f `"$env:windir\inf\ntprint.inf`" /r `"$uri`" /m `"$driver`""
    $process = Start-Process -FilePath rundll32.exe -ArgumentList $arguments -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -ne 0) {
        Fail "printui fallback failed with exit code $($process.ExitCode)"
        exit $script:ExitCode
    }
}

# --- printer -----------------------------------------------------------------

try {
    $existing = Get-Printer -Name $PrinterName -ErrorAction SilentlyContinue
    if ($existing) {
        if ($existing.PortName -ne $uri) {
            Write-Log "Repointing '$PrinterName' from $($existing.PortName) to $uri"
            Set-Printer -Name $PrinterName -PortName $uri -ErrorAction Stop
        } else {
            Write-Log "Printer already installed and correct"
        }
    } else {
        Write-Log "Installing printer '$PrinterName'"
        Add-Printer -Name $PrinterName -DriverName $driver -PortName $uri -ErrorAction Stop
    }
} catch {
    Fail "Could not install the printer: $($_.Exception.Message)"
    exit $script:ExitCode
}

if ($SetDefault) {
    try {
        (New-Object -ComObject WScript.Network).SetDefaultPrinter($PrinterName)
        Write-Log "Set as default printer"
    } catch {
        Write-Log "Could not set default printer: $($_.Exception.Message)" -Level Warning
    }
}

# --- close the bypass --------------------------------------------------------

if ($RemoveDirectPorts) {
    if (-not $PrinterAddress) {
        Write-Log "-RemoveDirectPorts needs -PrinterAddress; skipping" -Level Warning
    } else {
        Write-Log "Looking for direct ports to $PrinterAddress that would bypass inspection"
        try {
            $bypass = Get-PrinterPort | Where-Object {
                $_.PrinterHostAddress -eq $PrinterAddress -or
                ($_.Name -like "*$PrinterAddress*" -and $_.Name -ne $uri)
            }
            foreach ($port in $bypass) {
                foreach ($printer in (Get-Printer | Where-Object { $_.PortName -eq $port.Name })) {
                    Write-Log "Removing printer '$($printer.Name)' - printed directly to the device, bypassing inspection" -Level Warning
                    Remove-Printer -Name $printer.Name -ErrorAction SilentlyContinue
                }
                Remove-PrinterPort -Name $port.Name -ErrorAction SilentlyContinue
                Write-Log "Removed bypass port $($port.Name)" -Level Warning
            }
            if (-not $bypass) { Write-Log "No direct ports found" }
        } catch {
            Write-Log "Could not clean up direct ports: $($_.Exception.Message)" -Level Warning
        }
    }
}

Write-Log "=== done (exit $script:ExitCode) ==="
exit $script:ExitCode
