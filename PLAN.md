# janus-print — Print DLP

Inspect every document at the print spooler, hold anything matching a sensitive-data
policy, ship the event to Janus SIEM.

Target: **CUPS on Linux** as the choke point. Detection: **text extraction → OCR fallback
→ document fingerprinting**.

---

## 1. Interception mechanism

CUPS gives us exactly the primitive this design needs: a **backend** owns the job at the
last moment before it reaches the device, and its *exit code* decides the job's fate.

```
CUPS_BACKEND_OK      0   release
CUPS_BACKEND_HOLD    3   hold in queue, reviewable, releasable later
CUPS_BACKEND_CANCEL  5   kill the job
```

So: `/usr/lib/cups/backend/janus` replaces the real backend in the queue's device URI.

```
lpadmin -p acct-laser -E \
  -v janus://ipp/10.0.4.21/ipp/print \
  -m everywhere
```

The backend receives `argv[1..6]` = job-id, user, title, copies, options, file — plus the
job body on stdin when no file argument is given. It:

1. spools the body to a temp file
2. POSTs metadata + file to the inspector
3. on `allow` → `exec` the real backend (`ipp://10.0.4.21/ipp/print`) with the same argv,
   transparently passing through its exit code
4. on `hold` → exit 3 and emit the alert
5. on `block` → exit 5

**Make every queue driverless (`-m everywhere`).** Then the backend receives PDF with an
intact text layer. Legacy PCL/PostScript driver queues rasterize first, which forces OCR
on 100% of jobs and destroys the latency budget.

The backend must be small, dependency-free, and boring — it runs as `lp` on every print in
the building. All real logic lives in the API service.

## 2. Components

| Component | What it is | Stack |
|---|---|---|
| `backend/` | CUPS backend shim | Python 3.12, stdlib only |
| `api/` | `/inspect`, `/jobs`, `/rules`, `/release` | FastAPI + Uvicorn |
| `inspector/` | extraction + rule engine + fingerprint matcher | Python, RQ workers |
| `archive/` | encrypted job store + extracted-text index | MinIO (S3 API) + Postgres |
| `console/` | SOC queue, release workflow, rule editor | FastAPI + Jinja2 + Bootstrap 5 |
| `bridge/` | CEF over syslog → Janus :514 | Python |

Postgres for job/verdict/rule state. Redis for the work queue.

**Python 3.12 end-to-end.** Janus next door is PHP 8.2/MySQL, but janus-print stays a
separate service and the only contract between them is CEF over syslog (§7) — no shared
runtime, no shared database, no PHP anywhere in this repo. The console borrows Janus's
Bootstrap 5 *look* so the two feel like one product to a SOC analyst, but it is served by
FastAPI + Jinja2 and is never ported into Janus's PHP app.

## 3. The latency problem — decide this first

Nobody tolerates a print that takes 30 seconds. The three detection tiers have wildly
different costs:

| Tier | Cost | Inline? |
|---|---|---|
| Text layer + regex/keyword | 50–300 ms | yes, blocking |
| Fingerprint match against corpus | 200 ms–2 s | yes, blocking |
| OCR on image-only pages | 2–15 s **per page** | no |

Design: **tiers 1–2 inline with a hard 3 s deadline. OCR is asynchronous.**

When a job contains pages with no text layer, per-printer policy decides:

- `deep_scan_required: true` (finance, HR, legal queues) → hold immediately, release only
  after OCR clears it. Users on those queues accept the wait.
- `deep_scan_required: false` (everything else) → release now, OCR in background, alert
  retroactively if it hits. You can't unprint, but you get the audit trail and the
  incident.

This is the single most consequential decision in the system. Put it in config per queue,
not in code.

## 4. Fail-open vs fail-closed

If the inspector is down or times out, does the office stop printing? Per-printer setting,
**default fail-open with a loud alarm** — an outage that halts all printing company-wide is
a Sev1 that gets the product ripped out. High-sensitivity queues can be fail-closed
explicitly. Every fail-open pass-through is logged as its own event type so the gap is
auditable.

## 5. Detection engine

**Rules as versioned YAML**, never code:

```yaml
- id: pan-primary
  name: Payment card number
  pattern: '\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})\b'
  validator: luhn              # kills ~90% of false positives
  context:                     # proximity boost, ±50 chars
    terms: [visa, mastercard, card number, cvv, exp]
    boost: 0.3
  threshold: 0.75
  min_count: 1
  action: hold
```

Non-negotiable pieces:

- **Validators** (Luhn, IBAN mod-97, national-ID checksums). A bare 16-digit regex will
  flag invoice numbers and drown the SOC in noise on day one.
- **Proximity/context scoring** — a match near "account number" scores higher than one
  floating in a table of part numbers.
- **Confidence → action ladder**: `log` / `hold` / `block`.
- **A rule test corpus + pytest suite.** Every rule ships with positive and negative
  fixtures. A rule change that regresses the corpus fails CI. This is what keeps the false
  positive rate survivable as rules accumulate.

**Fingerprinting** (phase 6): normalize extracted text → 5-gram shingles → winnowing to
select a stable subset → store per registered document. Match = fraction of query
fingerprints present in the index. Catches reworded and partial excerpts, which plain
hashing misses entirely. Keep an exact SHA-256 path too for unmodified files — it's free.

Extraction chain: `pypdfium2`/`pdfplumber` for PDF → `ghostscript` for PS/PCL → PDF →
`tesseract` for pages returning under ~20 chars of text.

## 6. Archive store

Every job body, encrypted at rest, retention-capped (default 90 days, configurable — set
it deliberately, not by accident).

The archive is the highest-value target in the whole system: it is a searchable copy of
everything the company prints, including payroll, board decks, and medical records.
Treat it accordingly — per-object encryption keys, access to document *content* gated
behind a second approver, and every read of the archive logged as its own auditable event.
An unlogged DLP archive is a bigger liability than the leak it was built to prevent.

## 7. Janus integration

Emit CEF to Janus's syslog receiver on 514 — no direct DB writes, no coupling to its schema.

```
CEF:0|Janus|PrintDLP|1.0|PAN_DETECTED|Payment card in print job|8|
 suser=jdoe shost=WS-4471 dproc=acct-laser fname=Q3_recon.pdf
 cn1=3 cn1Label=matchCount cs1=hold cs1Label=action
 cs2=job-88213 cs2Label=jobId
```

Confirm `syslog_receiver.py`'s parser accepts CEF key-value extensions before committing to
the format — it may only handle RFC 3164/5424 headers today, in which case add a CEF branch
there rather than dumbing down this payload.

Never put matched content in the syslog line. Send the rule ID, count, and a job reference;
the content stays in the archive behind the approval gate.

## 8. Build order

**Phase 0 — Lab.** docker-compose: CUPS container, a virtual PDF sink queue, a client
container. `lp file.pdf` lands a PDF in a folder. Golden-file test harness with a fixture
corpus (clean docs, PAN docs, scanned docs, PCL docs) before any detection code exists.

**Phase 1 — Transparent interception.** Backend that captures, archives, and releases
100% of jobs. Ship nothing else until printing is provably unbroken: run a week of real
jobs, measure added latency at p50/p99, confirm duplex/stapling/tray options survive the
`exec` pass-through.

**Phase 2 — Text extraction + rule engine.** Regex/keyword/validator/context scoring,
verdicts, hold via exit 3. Tune against the fixture corpus.

**Phase 3 — Console.** Held-job queue, release/deny with reason, rule CRUD, job history.
Server-rendered Jinja2 off the same FastAPI app — no separate frontend build, no SPA. Styled
to match Janus so the two read as one product; deep-linked from Janus alerts, not embedded
in it.

**Phase 4 — Janus bridge.** CEF emitter, event taxonomy, alert dedup.

**Phase 5 — OCR fallback** + the async/deep-scan policy from §3.

**Phase 6 — Fingerprinting.** Corpus registration UI, shingle index, match scoring.

**Phase 7 — Hardening.** Key management, retention enforcement, inspector HA, backend
circuit breaker, load test at realistic job rates.

Phases 1–4 are a working product. 5–7 are what make it defensible.

## 9. Known limits — state these upfront, don't discover them in a POC

- **Evasion is trivial for a motivated insider.** Print to image, shrink the font,
  rotate 3°, use a lookalike Unicode digit. Print DLP stops accidents, careless habits,
  and unsophisticated exfiltration — which is most real incidents. It does not stop a
  determined adversary, and claiming otherwise will lose you the room.
- **Legal/works-council review is a hard gate in the EU**, not a formality. You are
  recording employee document content. Sequence this before deployment, not after.
- **Non-CUPS clients bypass everything** — direct IP printing from a workstation to the
  device never touches the spooler. Interception is only as good as the network controls
  that force traffic through it. Block port 9100 to printers at the switch.
- **Encrypted or password-protected PDFs** can't be inspected. Policy decision, same shape
  as the OCR one: hold or pass with a flag.
