#!/usr/bin/env python3
"""Score a benchmark run against what the inspector actually decided.

Reads the jobs back from the API and compares each against its expectation. Two failure
directions, deliberately reported separately because they cost different things:

    MISS            a document that should have been held printed. This is the failure
                    the system exists to prevent.
    FALSE POSITIVE  a harmless document was held. Cheap once, expensive daily: it is how
                    a queue becomes noise nobody reads.

Usage:
    python score.py --api http://10.0.1.5:8088 --manifest ./bench/manifest.json \\
        --user admin --password '...'
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

HELD_STATES = {"held", "blocked", "denied_by_analyst"}
# released_by_analyst counts as held: a human intervened, so the system did stop it.
INTERVENED = {"released_by_analyst"}


def login(client: httpx.Client, api: str, user: str, password: str) -> None:
    response = client.post(
        f"{api}/login", data={"username": user, "password": password, "next": "/"},
        follow_redirects=False,
    )
    if response.status_code != 303:
        sys.exit(f"login failed ({response.status_code}) — check credentials")


def fetch_jobs(client: httpx.Client, api: str, limit: int = 200) -> list[dict]:
    response = client.get(f"{api}/api/v1/jobs", params={"limit": limit})
    response.raise_for_status()
    return response.json()


def match_job(jobs: list[dict], name: str) -> dict | None:
    """Find the newest job whose title mentions this document.

    Clients set the title from the filename, though they decorate it — Word sends
    "Microsoft Word - foo.docx" — so this matches on the stem rather than equality.
    """
    stem = name.replace(".pdf", "")
    candidates = [j for j in jobs if stem in (j.get("title") or "")]
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8088")
    parser.add_argument("--manifest", default="./bench/manifest.json")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="janus-print")
    parser.add_argument(
        "--wait", type=int, default=0,
        help="seconds to wait first, so deferred OCR has finished",
    )
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text())

    if args.wait:
        print(f"waiting {args.wait}s for deferred OCR...")
        time.sleep(args.wait)

    with httpx.Client(timeout=30.0) as client:
        login(client, args.api, args.user, args.password)
        jobs = fetch_jobs(client, args.api)

    rows, misses, false_positives, missing, latencies = [], [], [], [], []

    for entry in manifest:
        job = match_job(jobs, entry["file"])
        if job is None:
            missing.append(entry["name"])
            rows.append((entry["name"], entry["expect"], "NOT PRINTED", "-", "-", "-"))
            continue

        state = job["state"]
        held = state in HELD_STATES or state in INTERVENED
        fired = sorted({m["rule_id"] for m in job.get("matches", [])})
        latencies.append(job.get("inline_ms", 0))

        if entry["expect"] == "hold":
            verdict = "PASS" if held else "MISS"
            if not held:
                misses.append(entry["name"])
        else:
            verdict = "PASS" if not held else "FALSE POSITIVE"
            if held:
                false_positives.append(entry["name"])

        rows.append((
            entry["name"], entry["expect"], verdict,
            job.get("scan_tier", "-"), ",".join(fired) or "-",
            f"{job.get('inline_ms', 0)}ms",
        ))

    width = max(len(r[0]) for r in rows) + 2
    print(f"\n{'DOCUMENT':<{width}} {'EXPECT':<7} {'RESULT':<15} {'TIER':<13} {'RULES':<34} INLINE")
    print("-" * (width + 80))
    for row in rows:
        print(f"{row[0]:<{width}} {row[1]:<7} {row[2]:<15} {row[3]:<13} {row[4]:<34} {row[5]}")

    scanned = [e for e in manifest if e["scanned"] and e["expect"] == "hold"]
    scanned_names = {e["name"] for e in scanned}
    scanned_caught = sum(
        1 for r in rows if r[0] in scanned_names and r[2] == "PASS"
    )

    text_cases = [e for e in manifest if not e["scanned"] and e["expect"] == "hold"]
    text_names = {e["name"] for e in text_cases}
    text_caught = sum(1 for r in rows if r[0] in text_names and r[2] == "PASS")

    print("\n--- summary ---")
    if text_cases:
        print(f"  text-layer detection : {text_caught}/{len(text_cases)}")
    if scanned:
        print(f"  scanned (OCR)        : {scanned_caught}/{len(scanned)}"
              f"   <-- the number this benchmark exists to produce")
    print(f"  false positives      : {len(false_positives)}"
          + (f"  {false_positives}" if false_positives else ""))
    print(f"  misses               : {len(misses)}" + (f"  {misses}" if misses else ""))
    if missing:
        print(f"  not printed          : {len(missing)}  {missing}")
    if latencies:
        ordered = sorted(latencies)
        print(f"  inline latency       : median {ordered[len(ordered) // 2]}ms, "
              f"max {ordered[-1]}ms")

    if misses:
        print("\n  A MISS means a document that should have been stopped was printed.")
        print("  For scanned cases, check the job's scan tier: 'ocr_pending' means the")
        print("  deep scan had not finished — rerun with --wait 60.")

    return 1 if (misses or false_positives) else 0


if __name__ == "__main__":
    raise SystemExit(main())
