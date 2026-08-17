# Deploying janus-print on a new server

Assumes a Linux host with Docker and the repo cloned. Network discovery (Bonjour) needs
host networking, which is Linux-only — Docker Desktop on macOS/Windows will run the stack
but will not advertise printers to the LAN.

---

## 1. Prerequisites

```bash
docker --version          # 24+ with the compose plugin
docker compose version
```

**Port 631 must be free.** With the discovery override, the cups container binds it on the
host. If the host runs its own CUPS:

```bash
sudo systemctl disable --now cups cups-browsed
```

**Check for an address-pool collision.** Docker's default pool is `172.16.0.0/12`. If your
printers or clients live in that range, bridge-networked containers will route those
addresses to their own bridges instead of the LAN. Printing still works (cups is
host-networked) but the API's connectivity probe will report healthy printers as
unreachable. To avoid it, in `/etc/docker/daemon.json`:

```json
{ "default-address-pools": [ { "base": "10.201.0.0/16", "size": 24 } ] }
```

then `sudo systemctl restart docker`.

## 2. Secrets — do this before the first start

The archive master key **cannot be rotated later without losing every archived document**:
per-object keys are wrapped under it, so changing it makes existing archives permanently
unreadable. Set it now, while the archive is empty.

```bash
openssl rand -base64 32   # archive master key
openssl rand -base64 32   # session secret
```

Create `.env` beside `docker-compose.yml`:

```bash
cat > .env <<'EOF'
JANUS_PRINT_ARCHIVE_MASTER_KEY=<first value>
JANUS_PRINT_SESSION_SECRET=<second value>
JANUS_PRINT_DEV_MODE=false
EOF
chmod 600 .env
```

With `dev_mode=false` nothing seeds a default admin — see step 4. Leaving it `true` creates
`admin` / `janus-print`, which is fine for a lab and unacceptable on a network.

## 3. Start

```bash
# with network discovery (recommended on a real print server)
sudo docker compose -f docker-compose.yml -f docker-compose.discovery.yml up -d --build

# or plain, no discovery, CUPS on host port 6631
sudo docker compose up -d --build
```

Check it came up:

```bash
sudo docker compose ps
curl -s http://localhost:8088/api/v1/health
```

`rules_loaded` should be 15, with `ocr_available` and `ghostscript_available` true.

## 4. Create the first admin

```bash
sudo docker compose exec -e JANUS_PRINT_NEW_PASSWORD='<a real password>' \
  api janus-print-admin create <username> --role admin
```

Then sign in at `http://<server>:8088`.

Roles: `viewer` (read only), `analyst` (release/deny), `approver` (+approve archive
access), `admin` (+rules, users, printers).

## 5. Add a printer

Console → **Printers** → Add, or:

```bash
curl -s -c /tmp/c -o /dev/null -X POST http://localhost:8088/login \
  -d "username=<you>&password=<pw>&next=/"

curl -s -b /tmp/c -X POST http://localhost:8088/api/v1/printers \
  -H "Content-Type: application/json" -d '{
    "name":"office-mfp",
    "device_uri":"ipp://<printer-ip>/ipp/print",
    "fail_mode":"open",
    "on_unreadable":"hold",
    "deep_scan_required":true,
    "rule_tags":["*"],
    "note":"initial install"}'
```

The printer must be **powered on and idle** — CUPS queries its capabilities during
creation and will hang if it is busy.

Two queues appear per printer. That is deliberate:

| Queue | Role |
|---|---|
| `office-mfp` | client-facing, raw, shared — no filtering, so the inspector sees the document intact |
| `office-mfp-device` | internal, driverless, hidden — converts and talks to the hardware |

CUPS runs its filters *before* the backend, so a single queue would rasterise the job
before inspection and the inspector would receive a bitmap with no text.

### Policy worth setting deliberately

- **`on_unreadable: hold`** — the `log` default lets a document the inspector cannot read
  print anyway. That is how an unreadable-format problem hides for weeks.
- **`deep_scan_required: true`** — holds pages that cannot be read as text until OCR clears
  them. Costs the user a few seconds per page; without it you get a retrospective incident
  after the paper is already out.
- **`fail_mode`** — `open` keeps printing when the inspector is down (and logs the gap);
  `closed` stops the job. Reserve `closed` for genuinely sensitive queues: with many
  fail-closed printers, one inspector outage stops the whole floor.

## 6. Connect workstations

Clients on the **server's own subnet** discover the queues automatically over Bonjour.
Anywhere else, add manually — mDNS does not cross a router.

```bash
# macOS / Linux
lpadmin -p office-mfp -E -v ipp://<server>:631/printers/office-mfp -m everywhere
cupsenable office-mfp && cupsaccept office-mfp
```

macOS GUI: Settings → Printers & Scanners → Add → **IP**, address `<server>`, queue
`printers/office-mfp`, protocol IPP.

Windows: `docs/deploy-windows-printer.ps1 -Server <server> -Port 631 -Queue office-mfp`
(Windows does not discover CUPS queues over Bonjour — it uses WSD, which CUPS does not
speak.)

## 7. Verify the chain

```bash
# printer answers, nothing printed
curl -s -b /tmp/c -X POST http://localhost:8088/api/v1/printers/office-mfp/test-connection

# a real page through interception -> verdict -> device
curl -s -b /tmp/c -X POST http://localhost:8088/api/v1/printers/office-mfp/test-page \
  -H "Content-Type: application/json" -d '{"note":"commissioning"}'
```

Then print from a workstation:

1. A clean document — should print.
2. A document containing `STRICTLY CONFIDENTIAL` — should be **held**.

```bash
sudo docker compose logs api --tail 20 | grep "job "
sudo docker compose logs siem --tail 10 | grep CEF        # what Janus receives
sudo docker compose logs siem | grep -c FAILED_OPEN       # must be 0
```

`FAILED_OPEN` means jobs printed without inspection because the backend could not reach the
API. It is the one number to alert on.

## 8. Point it at the real SIEM

The compose `siem` service is a stand-in that prints what it receives. For production, set
`JANUS_PRINT_SIEM_HOST` to your Janus collector and drop that service. Confirm Janus's
`syslog_receiver.py` parses CEF key-value extensions — if it only handles RFC 3164/5424
headers, add a CEF branch there rather than flattening the payload.

## 9. The control this does not provide

Everything above inspects what reaches the spooler. A workstation with a direct TCP/IP port
to the printer bypasses all of it — no inspection, no archive, no alert, and the console
stays quiet.

Block TCP **9100**, **631** and **515** to every printer from all sources except the print
server. Until that exists, this is an audit tool for traffic that happens to pass through
it, not an enforcement control.

Also: recording what employees print is regulated in many jurisdictions. In the EU,
works-council/legal sign-off is a hard gate, not a formality.

---

## Operations

```bash
sudo docker compose logs -f api                     # verdicts as they happen
sudo docker compose exec api janus-print-rules test # rule fixtures still pass?
sudo docker compose exec -T postgres pg_dump -U janus janusprint > backup.sql
```

Back up the Postgres volume **and** the archive bucket together — the wrapped per-object
keys live in the database while the ciphertext lives in the bucket, and neither is useful
without the other.
