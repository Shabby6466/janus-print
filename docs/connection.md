# Connecting workstations and printers

How clients reach the inspected queue, why the printer auto-discovers but janus-print does
not, and what has to be true on the network for any of this to actually enforce anything.

---

## 1. The topology

As deployed:

| Host | Address | Subnet |
|---|---|---|
| janus-print server (CUPS + API + console) | `172.18.100.3` | `172.18.100.0/24` |
| Physical printer | `172.18.104.250` | `172.18.104.0/24` |
| Workstations | `172.18.104.x` | `172.18.104.0/24` |

Netmask is `/24` (`255.255.255.0`), so `172.18.100.x` and `172.18.104.x` are **different
subnets** with a router between them. Workstations and the printer share a subnet; the
print server does not.

The intended path:

```
workstation ──IPP:631──> janus-print (172.18.100.3) ──inspect──> printer (172.18.104.250)
```

The path that exists by default, and that the network currently permits:

```
workstation ──9100/631──────────────────────────────> printer (172.18.104.250)
```

Everything below exists to make the first path convenient and the second one impossible.

## 2. Why the printer auto-appears but janus-print does not

The printer advertises **itself**. Modern printers broadcast over mDNS/Bonjour (this is
what AirPrint is) and WS-Discovery for Windows. No server is involved — plug a printer in
and every machine on that link sees it within seconds.

mDNS is **link-local multicast** (UDP 5353, TTL 1). It does not cross a router.

* Printer on `172.18.104.0/24` → seen automatically by every workstation on
  `172.18.104.0/24`. This is why it "just worked" before janus-print existed.
* janus-print on `172.18.100.0/24` → its advertisements never reach `172.18.104.x`, no
  matter how the container is configured.

So on this network the inspected queue must be added explicitly, or pushed. Automatic
discovery of the janus queue only works for machines on `172.18.100.x`.

To get automatic discovery across the boundary you need an **mDNS reflector** on the
router or firewall (often called a "Bonjour gateway" or "mDNS repeater"), or an avahi
instance with an interface in each subnet. That is network infrastructure work, outside
this project. For a multi-subnet site, pushing the queue by policy is usually less fragile
than reflecting multicast everywhere.

## 3. Ports

| Port | Protocol | Used for |
|---|---|---|
| 631 | TCP | IPP — workstation to janus-print, and janus-print to printer |
| 631 | TCP | CUPS web admin on the print server |
| 8088 | TCP | janus-print console (host port; container listens on 8080) |
| 9100 | TCP | AppSocket/JetDirect **direct to printer — the bypass path** |
| 515 | TCP | LPD **direct to printer — also a bypass path** |
| 5353 | UDP | mDNS/Bonjour discovery (link-local only) |
| 514 | UDP | CEF/syslog to Janus SIEM |

Note 631 appears twice. With `docker-compose.discovery.yml` the cups container uses host
networking and listens on **631**; with the default bridge networking it is published on
**6631**. Client URIs must match whichever is in use.

## 4. Adding the queue on a workstation

The queue name is `office-printer` and the server is `172.18.100.3`.

### macOS — GUI

**System Settings → Printers & Scanners → Add Printer, Scanner or Fax → IP tab**

| Field | Value |
|---|---|
| Address | `172.18.100.3` |
| Protocol | Internet Printing Protocol — IPP |
| Queue | `printers/office-printer` |
| Name | Office Printer (Inspected) |
| Use | Generic PostScript Printer |

### macOS / Linux — command line

```bash
lpadmin -p janus-office -E \
  -v ipp://172.18.100.3:631/printers/office-printer \
  -m everywhere
cupsenable janus-office && cupsaccept janus-office
```

Prefer `-m everywhere`. Driverless clients send PDF, which keeps the text layer intact for
inspection. A client configured with a PostScript PPD converts first, forcing a ghostscript
pass on the server for every job.

### Linux — GUI

* GNOME: Settings → Printers → Add Printer → "Enter address" →
  `ipp://172.18.100.3:631/printers/office-printer`
* KDE: System Settings → Printers → Add → Network Printer → IPP
* Any distro: `http://localhost:631/admin` → Add Printer → Internet Printing Protocol (ipp)

### Linux — whole fleet, without adding a printer at all

Ship this as `/etc/cups/client.conf` via config management:

```
ServerName 172.18.100.3
```

Every queue on the server then appears on the client automatically. One line, no per-machine
printer setup.

### Windows — GUI

**Settings → Bluetooth & devices → Printers & scanners → Add device → Add a printer
manually → Select a shared printer by name:**

```
http://172.18.100.3:631/printers/office-printer
```

Driver: **Microsoft IPP Class Driver**. If it is not in the list, the Internet Printing
Client feature is not installed — see below. Fallback is **Generic → MS Publisher
Imagesetter**, which emits PostScript and works well with CUPS. Avoid *Generic / Text
Only*: it sends raw text the printer will not render and the inspector cannot use.

### Windows — script or policy

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\deploy-windows-printer.ps1 -Server 172.18.100.3 -Port 631 -Queue office-printer
```

See [deploy-windows-printer.ps1](deploy-windows-printer.ps1). Deployable through Group
Policy (Computer Configuration → Preferences → Control Panel Settings → Printers) or Intune.
Add `-RemoveDirectPorts -PrinterAddress 172.18.104.250` to strip existing direct ports.

Windows does **not** discover CUPS queues over Bonjour — it uses WS-Discovery, which CUPS
does not speak. Windows clients always need a manual add or a push.

## 5. The bypass problem

This is the part that decides whether janus-print is a control or merely a record.

Workstations sit on the same subnet as the printer, and the printer advertises itself. So
the default experience for every user is:

* the raw printer appears in their print dialog automatically, with zero effort;
* the inspected queue requires deliberate setup pointing at another subnet.

The path of least resistance bypasses inspection. Users are not evading anything — they
click the printer that showed up. Nothing reaches the spooler: no inspection, no archive
entry, no alert. The console stays quiet and looks healthy while seeing a fraction of what
is printed.

**Two changes close it, and both are needed.**

1. **Firewall — this is the one that enforces.** Block TCP `9100`, `631` and `515` to
   `172.18.104.250` from every source except `172.18.100.3`.

   ```
   permit tcp host 172.18.100.3  host 172.18.104.250 eq 9100 631 515
   deny   tcp any                host 172.18.104.250 eq 9100 631 515
   ```

2. **Turn off the printer's own advertising.** In its web admin at
   `http://172.18.104.250`, disable Bonjour/AirPrint, WS-Discovery and mDNS. It then stops
   appearing in print dialogs, so people stop adding it. This is convenience, not
   enforcement — a user who knows the IP can still add it manually, which is why rule 1 is
   not optional.

Until the firewall rule exists, treat the system as an audit tool for traffic that happens
to pass through it, not as a control.

## 6. Verifying a client is really going through inspection

After adding the queue, print something and check on the server:

```bash
# verdicts, one line per job
sudo docker compose logs api --tail 5 | grep "job "

# what the SIEM received, including which workstation sent it
sudo docker compose logs siem --tail 5 | grep -o "suser=[^ ]* shost=[^ ]*"

# jobs released WITHOUT inspection — must be 0
sudo docker compose logs siem | grep -c FAILED_OPEN
```

Three things to confirm:

* `shost=` shows the workstation, not blank and not the print server. Attribution comes
  from `job-originating-host-name`, which CUPS passes in the backend's options argument —
  it does **not** set `REMOTE_HOST`.
* `tier=text` rather than `unreadable`, which would mean the client's driver is emitting
  something the extractor cannot parse. Switch that client to a PostScript or driverless
  driver.
* `FAILED_OPEN` count is `0`. Anything else means jobs printed uninspected because the
  inspector was unreachable.

## 7. Troubleshooting

Every entry here is a failure actually hit during deployment.

| Symptom | Cause | Fix |
|---|---|---|
| `lpstat: Error - add '/version=1.1' to server name` | Misleading client message for an HTTP 400. CUPS rejected the `Host:` header because the name is not its own | `ServerAlias *` in `cupsd.conf` (or list the real hostnames) |
| `lp: Error - The printer or class does not exist` from the test client | With host networking, `cups` no longer resolves inside the bridge network | Point the client at the host gateway: `CUPS_SERVER=172.17.0.1:631` |
| Job hangs in the queue forever, backend still running | Queue created with `-m raw` — the printer received PDF it cannot render and stalled | Recreate with `ipp://` + `-m everywhere`, then switch the URI to `janus://` |
| `Create-Job: server-error-busy` on unrelated jobs | The printer's buffer is jammed from earlier raw/unrenderable data | Power-cycle the printer |
| `-m everywhere` fails when creating a janus queue | CUPS cannot query capabilities through the `janus://` scheme | Create as `ipp://<printer>/ipp/print -m everywhere` first, then `lpadmin -p <queue> -v janus://ipp/<printer>/ipp/print` |
| `docker compose exec` hangs | TTY allocation with redirected output | Use `-T`, or `docker compose cp` / `docker exec` |
| Operator-created queue disappears after a rebuild | `/etc/cups` had no volume; also CUPS defers writing `printers.conf` by up to 30s and cupsd was killed before flushing | Named volume on `/etc/cups`, `DirtyCleanInterval 0`, and SIGTERM forwarding in the entrypoint |
| cups container exits immediately when there are no queues | `lpstat -v` returns non-zero with zero destinations, and `set -e` killed the entrypoint before `wait` | Fixed — `lpstat -v || echo ...` |
| Lab queues reappear after you delete them | The entrypoint recreates them on every start | `CREATE_LAB_QUEUES=false` |
| "Microsoft IPP Class Driver" missing on Windows | Internet Printing Client feature not installed | `Enable-WindowsOptionalFeature -Online -FeatureName Printing-Foundation-InternetPrinting-Client -All`, then reboot |
| Printer not auto-discovered on a workstation | mDNS does not cross subnets | Add manually, push by policy, or run an mDNS reflector on the router |
| Every job shows `FAILED_OPEN` | The backend cannot reach the API | `docker exec janus-print-cups-1 curl -s http://127.0.0.1:8088/api/v1/health` — with host networking the URL is `127.0.0.1:8088`, not `api:8080` |
