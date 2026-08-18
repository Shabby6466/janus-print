# Deploying the inspected printer to Windows via Group Policy

Windows cannot discover CUPS queues. It uses WS-Discovery; CUPS speaks DNS-SD/Bonjour, and
no daemon bridges the two for printers — `wsdd`, the usual suggestion, advertises *hosts*
for file-sharing browsing, not printers. So on Windows the printer is deployed, not found.

That is the better outcome anyway. Discovery is the wrong mechanism for a queue that must
be *the* route to the printer: you want it present on every machine, not offered as one
option beside the raw device.

Values used throughout — substitute your own:

| | |
|---|---|
| Print server | `10.0.1.5` |
| CUPS port | `631` (host networking) or `6631` (bridge) |
| Queue | `local-office` |
| Printer (device) | `10.0.1.80` |
| Connection URI | `http://10.0.1.5:631/printers/local-office` |

---

## Option A — GPO Preferences (no scripting)

Best when you want the printer managed as policy and removed cleanly when the GPO no
longer applies.

1. **Group Policy Management** → your OU → **Create a GPO in this domain** → name it
   `janus-print inspected printer`.
2. Edit → **Computer Configuration → Preferences → Control Panel Settings → Printers**.
3. Right-click → **New → TCP/IP Printer**.
4. Set:
   - Action: **Update** (creates if missing, corrects if changed — idempotent)
   - Path/Port: `http://10.0.1.5:631/printers/local-office`
   - Local Name: `Office Printer (Inspected)`
   - Tick **Set this printer as the default printer** if wanted
5. Under **Common**, tick **Remove this item when it is no longer applied**.
6. Link the GPO to the OU containing the workstations.

Machine-level preferences run as SYSTEM, so driver installation is unaffected by the
PrintNightmare restrictions that block non-admin installs.

## Option B — startup script (more control, better logging)

Use this when you also want the bypass cleanup, or want a log to point at when someone says
"the printer disappeared".

1. Copy `Deploy-JanusPrinter.ps1` into the GPO's script store:
   ```
   \\<domain>\SYSVOL\<domain>\Policies\<GPO-GUID>\Machine\Scripts\Startup\
   ```
2. Edit the GPO → **Computer Configuration → Policies → Windows Settings → Scripts
   (Startup/Shutdown) → Startup → PowerShell Scripts → Add**.
3. Script Name: `Deploy-JanusPrinter.ps1`
4. Script Parameters:
   ```
   -Server 10.0.1.5 -Queue local-office -PrinterName "Office Printer (Inspected)" -SetDefault -RemoveDirectPorts -PrinterAddress 10.0.1.80
   ```
5. Ensure **Computer Configuration → Policies → Administrative Templates → System →
   Scripts → Run Windows PowerShell scripts first** is Enabled (or leave default; the
   script does not depend on it).

The script is written for this: idempotent, silent when already correct, and it exits 0
when the print server is simply unreachable — a laptop booting off-site retries next time
rather than logging an error every morning.

Logs land in `C:\ProgramData\janus-print\deploy.log` and, for warnings and errors, the
Application event log under source `janus-print`.

## Option C — Intune

Devices → Scripts and remediations → **Platform scripts** → Add → Windows 10 and later.

- Upload `Deploy-JanusPrinter.ps1`
- **Run this script using the logged-on credentials: No** (must be SYSTEM)
- **Run script in 64-bit PowerShell: Yes**

Intune platform scripts take no parameters, so edit the `param()` defaults in the file
before uploading, or wrap it in a one-line caller script.

---

## Verify

On a workstation, after `gpupdate /force` and a reboot:

```powershell
Get-Printer | Where-Object Name -like "*Inspected*" | Format-List Name, PortName, DriverName
Get-Content C:\ProgramData\janus-print\deploy.log -Tail 20
```

`PortName` must be the **server** URI. If it points at `10.0.1.80`, that machine is
printing straight to the device and nothing is being inspected.

Then print something and confirm the server saw it:

```bash
sudo docker compose logs api --tail 10 | grep "job "
sudo docker compose logs siem --tail 5 | grep -o "suser=[^ ]* shost=[^ ]*"
```

`shost=` should show the Windows machine's name. If it is blank, attribution is broken; if
there is no log line at all, the job never reached the spooler.

---

## Two things GPO does not fix

**Users can still add the printer directly.** The device advertises itself over AirPrint,
so it appears in Windows' "Add a printer" list. `-RemoveDirectPorts` cleans up existing
direct ports at every boot, but nothing stops someone re-adding one during the day.

Block TCP **9100**, **631** and **515** to `10.0.1.80` from every source except the print
server. That is the only durable control; everything above is convenience so that the
inspected path is also the easy path.

Optionally also disable AirPrint/Bonjour/WS-Discovery in the printer's own web admin so it
stops advertising itself in the first place.

**Driver choice affects detection.** The Microsoft IPP Class Driver sends a format the
inspector can read. If you substitute a vendor driver, check the first few jobs:

```bash
sudo docker compose logs api --tail 10 | grep "tier="
```

`tier=text` is healthy. `tier=unreadable`, or every job going to OCR, means the driver is
sending raster or a mis-encoded font — the inspector then cannot match any rule, and it
will report those jobs as cleanly inspected while seeing nothing.
