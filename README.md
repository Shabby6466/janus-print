# janus-print

Print DLP. Inspects every document at the CUPS spooler, holds anything matching policy,
and ships the event to Janus SIEM as CEF over syslog.

Python 3.12 end to end. Janus next door is PHP/MySQL; the only contract between them is one
syslog line — no shared runtime, no shared database.

See [PLAN.md](PLAN.md) for the design and the reasoning behind each decision.

---

## Run the lab

Everything needed to print a document end to end and watch it be inspected, held,
released, and alerted on. No printer and no Janus install required.

```bash
docker compose up -d --build
docker compose exec client print-samples office-laser

open http://localhost:8088        # console — admin / janus-print
open http://localhost:6631        # CUPS admin
docker compose logs -f siem       # the CEF stream Janus would receive
```

`print-samples` prints three documents: a clean one, one with card data, and one marked
confidential. The clean one prints; the other two are held and appear in the console queue.

Tests run in the API image (the host does not need Python 3.12):

```bash
docker compose run --rm api pytest -q          # 109 tests
docker compose run --rm api janus-print-rules test   # rule fixture gate
```

## How interception works

A CUPS backend shim owns the job immediately before it reaches the device, and its exit
code is the verdict:

| Exit | CUPS meaning | Effect |
|---|---|---|
| 0 | `OK` | exec the real backend — the job prints |
| 3 | `HOLD` | job stays in the queue, releasable from the console |
| 5 | `CANCEL` | job destroyed |

Queues are pointed at `janus://<scheme>/<host>/<path>`; the shim strips its own prefix and
`exec`s the real backend with the same argv, so duplex, tray, and stapling options survive.

```bash
lpadmin -p acct-laser -E -v janus://ipp/10.0.4.21/ipp/print -m everywhere
```

Use driverless (`-m everywhere`) queues. They deliver PDF with an intact text layer;
legacy PCL queues rasterize first, forcing OCR on every job.

## Print from a workstation

The lab's `client` container is a stand-in. Real workstations point at the same CUPS queue
and take the identical path — backend, inspector, verdict, hold or release.

On the laptop (macOS or Linux), pointing at the print server on `:6631`:

```bash
lpadmin -p janus-office -E \
    -v ipp://172.18.100.3:6631/printers/office-printer \
    -m everywhere
cupsenable janus-office && cupsaccept janus-office
```

```bash
lpadmin -p janus-office -E \
  -v ipp://172.18.100.3:6631/printers/office-printer \
  -m everywhere
cupsenable janus-office && cupsaccept janus-office
lp -d janus-office some.pdf
```

macOS GUI equivalent: **Settings → Printers & Scanners → Add → IP**, address
`172.18.100.3:6631`, queue `printers/office-printer`, protocol **IPP**.

Prefer `-m everywhere` on the client too. Driverless clients send PDF, which keeps the text
layer intact; a client with a PostScript PPD converts first and forces a ghostscript pass on
the server for every job.

**Workstation attribution** comes from `job-originating-host-name`, which CUPS passes in the
backend's options argument — it does *not* set `REMOTE_HOST`. Reading the wrong one
attributes every laptop's job to the print server itself and makes the audit trail useless
on a shared queue. It lands in the CEF event as `shost=`.

Note that `docker/cups/cupsd.conf` currently allows submission and administration from
anywhere. That is fine on an isolated compose network and wrong on a real LAN — restrict
the `<Location />` and `<Location /admin>` blocks to your client subnet before rollout.

## What is built

**Interception** — `backend/janus`, stdlib-only, ~330 lines. Never raises, fails open by
default, logs coverage gaps to syslog independently of the API so a gap is still reported
when the API is the thing that is down.

**Detection** — three tiers:

- Text layer: regex + checksum validators + proximity scoring. 9–12 ms on a typical page.
- OCR (`tesseract`): image-only pages, always asynchronous.
- Fingerprinting: winnowed 5-gram shingles, so excerpts and reworded copies match, not just
  identical files.

15 rules ship across `rules/*.yaml` (payment cards, IBAN, SSN, NHS number, passports, bulk
email, classification banners, M&A material, salary schedules, private keys, AWS keys).
Every rule carries positive **and** negative fixtures; `janus-print-rules test` runs them
and `/api/v1/rules/reload` refuses a reload that regresses them.

**Verdict pipeline** — inline work runs under a 3 s deadline; past it the queue's fail mode
decides. Fail-open releases and records a distinct `failed_open` state, never a clean pass.

**Archive** — every job body, envelope-encrypted with a per-object key wrapped under a
master key. Reading content needs a stated reason plus a second approver, is single-use and
time-boxed, and every read is logged. Retention purge destroys the wrapped key, which makes
the content unrecoverable even from a bucket backup.

**Console** — server-rendered Jinja2, no build step, no CDN (print servers often have no
egress). Dashboard, queue, job detail with masked samples, rules, fingerprint corpus, queue
policies, audit log.

**SIEM bridge** — CEF over syslog to Janus on 514. Rule ids, counts, and a masked sample
travel; raw content never does.

## The two decisions that matter

Both live in `config/printers.yaml`, per queue, because they are operator decisions:

**`deep_scan_required`** — what happens to a page with no text layer. `true` holds the job
until OCR clears it (the user waits). `false` prints now and raises a retrospective
incident. You cannot unprint, but you get the audit trail.

**`fail_mode`** — what happens when the inspector is down. `open` keeps the office printing
and records a gap; `closed` stops the job. Open is the default on purpose: an inspector
outage that halts every printer in the building is worse than the leak it prevents.

## Verified end to end

Run against the live lab stack, not only in tests:

- Clean document printed; card-data and confidential documents held.
- Held job released from the console → CUPS re-ran the backend → **preflight passed it
  through** → it printed. Without preflight a released job is re-inspected and re-held
  forever; this is the subtle failure the design calls out.
- CEF reached the SIEM with `4111***********1111` and no raw PAN anywhere in the line.
- Image-only document on `finance-laser` held pending deep scan; worker ran OCR and
  updated the verdict.
- Inline inspection 9–12 ms warm, 240 ms cold — well inside the 3 s deadline.

## Before production

- `JANUS_PRINT_ARCHIVE_MASTER_KEY` and `JANUS_PRINT_SESSION_SECRET` must be real secrets.
  The service refuses to start on the default key unless `JANUS_PRINT_DEV_MODE=true`.
- Turn off `dev_mode` — it seeds a default admin account.
- `ServerAlias *` in `docker/cups/cupsd.conf` is lab-only; list real hostnames instead.
- Confirm Janus's `syslog_receiver.py` parses CEF key-value extensions; if it only handles
  RFC 3164/5424 headers, add a CEF branch there rather than flattening this payload.
- Block port 9100 to printers at the switch. Direct IP printing bypasses the spooler
  entirely, and interception is only as good as the controls that force traffic through it.
- Legal/works-council review is a hard gate in the EU. You are recording employee document
  content.

## Layout

```
backend/janus              CUPS backend shim (stdlib only)
src/janusprint/
  config.py                settings + per-queue policy
  models.py                jobs, matches, events, corpus, audit
  inspector/               extract, ocr, rules, validators, fingerprint, engine
  archive/                 encrypted store + retention
  bridge/cef.py            CEF emitter for Janus
  api/                     inspect path, jobs, admin, console
  templates/ static/       server-rendered console
rules/*.yaml               detection rules with fixtures
config/printers.yaml       per-queue policy
docker/                    CUPS lab, API image, client
tests/                     109 tests
```
