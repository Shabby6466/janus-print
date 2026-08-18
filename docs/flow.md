# How a document flows through janus-print

A trace of one print job from the workstation to the paper, with the file and line where
each step lives. Line numbers drift; the function names are the stable reference.

```
workstation
   │  IPP
   ▼
CUPS queue  local-office          (raw — deliberately no filtering)
   │
   ▼
backend/janus                      the interception point, runs as `lp`
   │  HTTP multipart
   ▼
POST /api/v1/inspect               routes_inspect.py
   │
   ▼
inspect_job()                      engine.py — archive, extract, rules, verdict
   │
   ├─ allow/log ──▶ exit 0 ──▶ exec real backend ──▶ local-office-device ──▶ printer
   ├─ hold     ──▶ exit 3 ──▶ job parked in CUPS, awaits console decision
   └─ block    ──▶ exit 5 ──▶ job destroyed
```

---

## 1. Interception — `backend/janus`

CUPS runs this as the queue's backend, so it owns the job immediately before the device.
Stdlib only: it executes on every print in the building and must never need a `pip install`.

| Step | Function | Line |
|---|---|---|
| entry, argv parsing | `main` | 251 |
| already-released check | `main` → `/api/v1/preflight` | ~276 |
| spool the job body | `spool_job` | 205 |
| workstation name | `originating_host` | 118 |
| send for inspection | `post_for_inspection` | 187 |
| release to the device | `exec_real_backend` | 221 |

Its **exit code is the verdict** — 0 release, 3 hold, 5 cancel. Everything else in the
system exists to decide that number.

Two behaviours worth knowing:

- **Preflight.** Resuming a held job re-runs the backend from the top. Without a check for
  an existing analyst release, a released job would be inspected again, match again, and be
  held forever.
- **Fail-open.** If the API is unreachable, the job is released and the gap is logged to
  syslog independently of the API — the events that matter most are the ones raised when
  the API is the thing that is down.

## 2. The HTTP edge — `src/janusprint/api/routes_inspect.py`

| Endpoint | Function | Line |
|---|---|---|
| `GET /api/v1/preflight` | `preflight` | 26 |
| `POST /api/v1/inspect` | `inspect` | 54 |

Deliberately unauthenticated: the backend runs as `lp` on the print server and holds no
credentials. It is protected by network placement, not a session.

## 3. Orchestration — `src/janusprint/inspector/engine.py`

`inspect_job` (line 93) is the spine. In order:

1. **Resolve policy** — `policy_for` in `printers.py:103`. Database first, then
   `config/printers.yaml`, then the built-in default, so an unconfigured queue is still
   inspected rather than erroring on the print path.
2. **Deduplicate** — CUPS re-runs the backend on retry and resume, so the same physical job
   arrives several times. Matched on `(queue, cups_job_id, sha256)`.
3. **Archive first** (`archive/store.py`) — whatever else fails, the audit copy survives.
4. **Extract** — `_run_inspection` at line 171.
5. **Evaluate rules**, then fingerprints (`_match_fingerprints`, line 281).
6. **Decide**, write `Match` rows, emit CEF.

Failure paths are first-class, not exception handlers:

| Situation | Function | Line |
|---|---|---|
| cannot read the document | `_unreadable` | 333 |
| crash or deadline exceeded | `_resolve_failure` | 370 |
| action → job state | `_state_for` | 409 |

`_resolve_failure` implements fail-open/fail-closed. Fail-open releases the job but records
state `failed_open`, never `released` — a coverage gap must never be mistaken for a clean
pass.

## 4. Reading the document — `src/janusprint/inspector/extract.py`

| Step | Function | Line |
|---|---|---|
| identify the format by magic bytes | `sniff_format` | 106 |
| dispatch | `extract` | 128 |
| PDF text layer (pypdfium2) | `_extract_pdf` | 150 |
| PostScript/PCL → PDF (ghostscript) | `_to_pdf` | 180 |
| **is this text actually language?** | `looks_like_language` | 40 |
| rasterise for OCR/preview | `render_pages_to_png` | 215 |

`looks_like_language` is the least obvious and most important. A PDF built from macOS
PostScript often carries a subset font with no Unicode map, so extraction yields one symbol
per glyph:

```
!"#$%&'(#)*$+'+,+-.*!+--/#"$0*1%)'."23&#"".&'31#"-.
```

That is worse than no text: the page counts as having a text layer, the OCR fallback never
runs, every rule fails to match, and the job is reported as cleanly inspected. Pages failing
this check are routed to OCR alongside genuinely blank ones.

## 5. Matching — `src/janusprint/inspector/rules.py`

| Step | Function | Line |
|---|---|---|
| all rules against one page | `RuleSet.evaluate_page` | 137 |
| one rule, with scoring | `RuleSet._evaluate_rule` | 145 |
| combine hits into one action | `merge_action` | 198 |
| load YAML packs (seeding only) | `load_rules` | 217 |
| run a rule's own fixtures | `test_fixtures` | 250 |

Scoring, in `_evaluate_rule`:

```
score = base_confidence            (default 0.6)
      + validator_weight           (0.3) if a validator is configured and passes
      + context.boost              (0.3) if a context term is within context.window chars

a configured validator that FAILS discards the match entirely
match counts if score >= threshold; rule fires if count >= min_count
```

`merge_action` takes the most restrictive action across all hits: `block` > `hold` > `log`.

**Validators** live in `validators.py` — `luhn`, `iban`, `us_ssn`, `nhs_number`, `mod11`,
`entropy`. They are the difference between a card rule and a rule that fires on every
invoice number. Unknown names are rejected when a rule is saved, not when a job prints.

## 6. Where rules actually live

**In the database**, table `rules` (`RuleRow` in `models.py`), managed by
`src/janusprint/inspector/store.py`:

| Action | Function | Line |
|---|---|---|
| load active ruleset (cached) | `load_ruleset` | 135 |
| import the YAML packs, once | `seed_from_yaml` | 157 |
| validate before persisting | `validate` | 89 |
| create / update / enable / delete | 195 / 219 / 246 / 266 | |
| preview without saving | `try_rule` | 289 |
| change history | `revisions` | 282 |

`rules/*.yaml` seeds the table on first start and never overwrites an existing row — a
restart cannot silently revert an operator's edit.

Editing happens in the console (`/rules`, templates `rules.html` and `rule_edit.html`,
routes in `api/routes_admin.py`). A rule is compiled **and** run against its own fixtures
before it is stored, so the inspection path cannot be broken from the UI.

The cache is keyed on `(count, max(updated_at))`, so a change made in the API process takes
effect in the worker process on the next job — no restart, no stale rules.

## 7. Fingerprints — `src/janusprint/inspector/fingerprint.py`

Winnowed k-gram shingles: normalise → 5-word shingles → blake2b → keep the minimum hash per
window. Matching uses containment in both directions, so an excerpt of a registered document
matches and so does a document containing one. Registered via `register_document`
(`engine.py:558`); queried by `_match_fingerprints` (`engine.py:281`) with a single indexed
lookup.

## 8. Alerting — `src/janusprint/bridge/cef.py`

`event_for_job` (line 68) builds the CEF line sent to Janus over syslog. Rule ids, counts
and a **masked** sample travel; raw content never does. A SIEM outage is logged and
swallowed — it must never affect printing.

## 9. The deferred tier — `src/janusprint/worker.py` → `engine.deep_scan` (line 425)

Queued when any page could not be read as text. Converts to PDF if needed, rasterises,
runs `ocr.py`, re-evaluates the rules, then either confirms the hold (`deep_scan_hit`) or
releases it (`deep_scan_clear`).

Whether the user waits for this is the queue's `deep_scan_required` policy. `true` holds
until OCR clears; `false` prints now and raises a retrospective incident.

## 10. Deciding — the console

| Page | Route | Template |
|---|---|---|
| queue, job detail | `api/routes_console.py` | `queue.html`, `job.html` |
| page viewer | `job_viewer` | `job_view.html` |
| rules | `rules_view`, `rule_edit` | `rules.html`, `rule_edit.html` |
| printers | `printers_view` | `printers.html` |

Release/deny live in `api/routes_jobs.py`, which records the decision **before** calling
CUPS (`api/cups_control.py`) so the preflight grant exists even if `lp` is slow.

---

## Following one job yourself

```bash
# 1. the verdict
docker compose logs api --tail 20 | grep "job "

# 2. what the rules actually saw
docker compose exec -T postgres psql -U janus -d janusprint -c \
  "SELECT j.title, j.state, j.scan_tier, left(e.pages::text, 300)
   FROM jobs j LEFT JOIN extracted_text e ON e.job_id = j.id
   ORDER BY j.created_at DESC LIMIT 1;"

# 3. which rules fired
docker compose exec -T postgres psql -U janus -d janusprint -c \
  "SELECT rule_id, count, score, tier, sample FROM matches
   ORDER BY id DESC LIMIT 10;"

# 4. what Janus received
docker compose logs siem --tail 5 | grep CEF
```

Step 2 is the one that answers most "why didn't my rule fire" questions: if `scan_tier` is
`unreadable`, or the extracted text is symbol soup, the rules never got a chance.
