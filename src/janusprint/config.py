"""Runtime configuration.

Everything is env-overridable so the same image runs in the lab and in production.
Printer policy is deliberately *not* here — it lives in config/printers.yaml because
it is edited by operators, not deployers.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

FailMode = Literal["open", "closed"]
Action = Literal["allow", "log", "hold", "block"]

# Action precedence — when several rules fire, the most restrictive wins.
ACTION_RANK: dict[str, int] = {"allow": 0, "log": 1, "hold": 2, "block": 3}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JANUS_PRINT_", env_file=".env", extra="ignore")

    # --- storage -------------------------------------------------------------
    database_url: str = "postgresql+psycopg://janus:janus@postgres:5432/janusprint"
    redis_url: str = "redis://redis:6379/0"

    archive_backend: Literal["fs", "s3"] = "fs"
    archive_path: Path = Path("/var/lib/janus-print/archive")
    archive_bucket: str = "janus-print"
    archive_retention_days: int = 90
    s3_endpoint: str = "http://minio:9000"
    s3_access_key: str = "janus"
    s3_secret_key: str = "janusjanus"
    s3_region: str = "us-east-1"

    # Master key wrapping the per-object archive keys. MUST be overridden in production;
    # the service refuses to start with the default value unless dev_mode is on.
    archive_master_key: str = "INSECURE-DEV-KEY-CHANGE-ME"
    dev_mode: bool = False

    # --- inspection ----------------------------------------------------------
    rules_dir: Path = REPO_ROOT / "rules"
    printers_config: Path = REPO_ROOT / "config" / "printers.yaml"

    # Hard ceiling on inline (blocking) inspection. Past this the job is resolved by the
    # queue's fail mode.
    inspect_deadline_seconds: float = 15.0
    ocr_page_timeout_seconds: float = 20.0
    ocr_max_pages: int = 40
    # Page render scale for OCR, in multiples of 72dpi. The default of 2.0 meant 144dpi —
    # below a typical scan, so a 300dpi page was downsampled before being read and small
    # type lost its inter-word spacing. 3.0 (216dpi) measurably recovers it.
    ocr_render_scale: float = 3.0
    # A page yielding fewer than this many characters is treated as image-only.
    text_layer_min_chars: int = 20

    fingerprint_k: int = 5  # shingle size, in words
    fingerprint_window: int = 4  # winnowing window
    fingerprint_threshold: float = 0.35

    # --- SIEM ----------------------------------------------------------------
    siem_host: str = "janus-siem"
    siem_port: int = 514
    siem_protocol: Literal["udp", "tcp"] = "udp"
    siem_enabled: bool = True
    siem_vendor: str = "Janus"
    siem_product: str = "PrintDLP"
    siem_version: str = "1.0"

    # --- console -------------------------------------------------------------
    session_secret: str = "INSECURE-DEV-SESSION-SECRET"
    session_ttl_seconds: int = 8 * 3600
    # Viewing archived document *content* needs a second person to approve.
    require_dual_approval_for_content: bool = True
    # Whether an analyst may page through a HELD job without a grant, to decide on it.
    # Rendered images only, watermarked and logged; downloading the original always needs
    # the grant. Turning this off is more private but tends to produce rubber-stamped
    # releases, because reviewers decide without seeing the page.
    allow_preview_for_held_jobs: bool = True


class PrinterPolicy(BaseModel):
    """Per-queue policy. See PLAN.md §3 and §4 — these two knobs are the whole product."""

    queue: str
    # Image-only pages can't be read inline. True = hold until OCR clears it (accept the
    # wait). False = release now, OCR asynchronously, alert retroactively.
    deep_scan_required: bool = False
    # What happens when the inspector is down or over deadline. "open" keeps the office
    # printing and raises an alarm; "closed" stops the job.
    fail_mode: FailMode = "open"
    # Encrypted PDFs we cannot read at all.
    on_unreadable: Action = "log"
    rule_tags: list[str] = Field(default_factory=lambda: ["*"])
    description: str = ""


class PrinterPolicies(BaseModel):
    default: PrinterPolicy = PrinterPolicy(queue="__default__")
    queues: dict[str, PrinterPolicy] = Field(default_factory=dict)

    def for_queue(self, queue: str) -> PrinterPolicy:
        return self.queues.get(queue, self.default)


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()


@functools.lru_cache
def get_printer_policies() -> PrinterPolicies:
    path = get_settings().printers_config
    if not path.exists():
        return PrinterPolicies()
    raw = yaml.safe_load(path.read_text()) or {}

    default = PrinterPolicy(queue="__default__", **(raw.get("default") or {}))
    queues: dict[str, PrinterPolicy] = {}
    for name, overrides in (raw.get("queues") or {}).items():
        merged = default.model_dump() | (overrides or {})
        merged["queue"] = name
        queues[name] = PrinterPolicy(**merged)
    return PrinterPolicies(default=default, queues=queues)


def reset_caches() -> None:
    """Test hook — settings and policies are cached for the process lifetime."""
    get_settings.cache_clear()
    get_printer_policies.cache_clear()
