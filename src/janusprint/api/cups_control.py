"""Acting on a held CUPS job from the console.

The backend puts a job in `held` state by exiting 3. Releasing it means telling CUPS to
resume it, which re-runs the backend from the top — hence the preflight check in
routes_inspect.py, without which a released job would simply be held again.

Modes:
    local  shell out to lp/cancel (API runs on the print server)
    ssh    same commands over ssh (API runs elsewhere)
    none   record the decision only — for the lab and for tests
"""

from __future__ import annotations

import logging
import shutil
import subprocess

from ..config import get_settings

log = logging.getLogger(__name__)


class CupsControlError(RuntimeError):
    pass


def _mode() -> str:
    import os

    return os.environ.get("JANUS_PRINT_CUPS_CONTROL", "none").lower()


def _ssh_target() -> str:
    import os

    return os.environ.get("JANUS_PRINT_CUPS_SSH", "")


def _run(args: list[str]) -> None:
    mode = _mode()
    if mode == "none":
        log.info("cups control disabled; would run: %s", " ".join(args))
        return

    if mode == "ssh":
        target = _ssh_target()
        if not target:
            raise CupsControlError("JANUS_PRINT_CUPS_SSH not set for ssh mode")
        args = ["ssh", "-o", "BatchMode=yes", target, *args]
    elif shutil.which(args[0]) is None:
        raise CupsControlError(f"{args[0]} not found on this host")

    try:
        subprocess.run(args, check=True, capture_output=True, timeout=15)  # noqa: S603
    except subprocess.CalledProcessError as exc:
        raise CupsControlError(
            f"{' '.join(args)} failed: {exc.stderr.decode(errors='replace').strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CupsControlError(f"{' '.join(args)} timed out") from exc


def job_uri(queue: str, cups_job_id: str) -> str:
    return f"{queue}-{cups_job_id}"


def release(queue: str, cups_job_id: str) -> None:
    """Resume a held job so it prints."""
    _run(["lp", "-i", job_uri(queue, cups_job_id), "-H", "resume"])


def cancel(queue: str, cups_job_id: str) -> None:
    """Destroy a held job."""
    _run(["cancel", job_uri(queue, cups_job_id)])


def describe() -> dict[str, str]:
    return {"mode": _mode(), "ssh_target": _ssh_target() or "-"}


def settings_summary() -> str:
    settings = get_settings()
    return f"deadline={settings.inspect_deadline_seconds}s cups_control={_mode()}"
