"""Driving the CUPS spooler from the API.

Two jobs: acting on individual held jobs (release/cancel), and managing the queues
themselves (create/delete/share) so printers can be administered from the console.

The backend puts a job in `held` state by exiting 3. Releasing it means telling CUPS to
resume it, which re-runs the backend from the top — hence the preflight check in
routes_inspect.py, without which a released job would simply be held again.

Modes:
    local  shell out to lp/lpadmin/cancel. Honours CUPS_SERVER, so this also drives a
           remote spooler over IPP — which is how the API container manages a
           host-networked CUPS container.
    ssh    same commands over ssh, for an API that cannot reach CUPS' IPP port
    none   record the decision only — for the lab and for tests

Queue management is a privileged capability: it lets the console reconfigure the print
server. It is restricted to the admin role and every change is written to
PrinterRevision, but that is a real escalation over read-only DLP and worth knowing.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess

from ..config import get_settings

log = logging.getLogger(__name__)

# Queue names reach a shell-free argv, but CUPS itself rejects some characters and a
# name with a slash or space produces confusing failures much later.
QUEUE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,127}$")
ALLOWED_SCHEMES = {"ipp", "ipps", "socket", "lpd", "http", "https", "usb", "cups-pdf"}


class CupsControlError(RuntimeError):
    pass


def _mode() -> str:
    return os.environ.get("JANUS_PRINT_CUPS_CONTROL", "none").lower()


def _ssh_target() -> str:
    return os.environ.get("JANUS_PRINT_CUPS_SSH", "")


# Status reads happen while a page is rendering, so they must fail fast. Queue creation
# makes CUPS negotiate capabilities with the printer over IPP, which on a big MFP takes
# tens of seconds. One shared timeout cannot serve both: long enough to create a queue
# means a hung lpstat blocks the console for just as long.
QUICK_TIMEOUT = 6.0


def _timeout() -> float:
    """Seconds to allow a slow, operator-initiated CUPS command (queue creation)."""
    try:
        return float(os.environ.get("JANUS_PRINT_CUPS_TIMEOUT", "90"))
    except ValueError:
        return 90.0


def _run(args: list[str], *, check_output: bool = False, timeout: float | None = None) -> str:
    mode = _mode()
    if mode == "none":
        log.info("cups control disabled; would run: %s", " ".join(args))
        return ""

    if mode == "ssh":
        target = _ssh_target()
        if not target:
            raise CupsControlError("JANUS_PRINT_CUPS_SSH not set for ssh mode")
        args = ["ssh", "-o", "BatchMode=yes", target, *args]
    elif shutil.which(args[0]) is None:
        raise CupsControlError(f"{args[0]} not found on this host")

    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            args, check=True, capture_output=True, timeout=timeout or _timeout()
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip()
        if "Unable to connect to server" in stderr:
            # This is the API failing to reach cupsd, not the printer failing to answer.
            # The distinction matters: the usual cause is CUPS_SERVER pointing at a name
            # that no longer resolves, e.g. after moving cups to host networking.
            raise CupsControlError(
                f"cannot reach cupsd at CUPS_SERVER={os.environ.get('CUPS_SERVER', '(unset)')} "
                f"— this is the print server, not the printer. Check that the address "
                f"resolves from the API container and that cupsd allows it. ({stderr})"
            ) from exc
        raise CupsControlError(f"{' '.join(args)} failed: {stderr}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CupsControlError(
            f"{' '.join(args)} timed out after {timeout or _timeout():.0f}s. If the printer is slow "
            f"to answer, raise JANUS_PRINT_CUPS_TIMEOUT; if it is unreachable or busy, "
            f"CUPS will never get its capabilities."
        ) from exc

    return completed.stdout.decode(errors="replace") if check_output else ""


# --- job control -------------------------------------------------------------


def job_uri(queue: str, cups_job_id: str) -> str:
    return f"{queue}-{cups_job_id}"


def release(queue: str, cups_job_id: str) -> None:
    """Resume a held job so it prints."""
    _run(["lp", "-i", job_uri(queue, cups_job_id), "-H", "resume"])


def cancel(queue: str, cups_job_id: str) -> None:
    """Destroy a held job."""
    _run(["cancel", job_uri(queue, cups_job_id)])


# --- queue management --------------------------------------------------------


def validate_name(name: str) -> str:
    if not QUEUE_NAME.match(name):
        raise CupsControlError(
            "queue name may contain only letters, digits, dot, underscore and hyphen"
        )
    return name


def validate_device_uri(uri: str) -> str:
    scheme, separator, rest = uri.partition("://")
    if not separator or not rest:
        raise CupsControlError("device URI must look like ipp://host/path or socket://host:9100")
    if scheme.lower() not in ALLOWED_SCHEMES:
        raise CupsControlError(
            f"unsupported scheme {scheme!r}; use one of: {', '.join(sorted(ALLOWED_SCHEMES))}"
        )
    if scheme.lower() == "janus":
        raise CupsControlError("give the real device URI; the janus:// wrapper is added for you")
    return uri


def janus_uri(device_uri: str) -> str:
    scheme, _, rest = device_uri.partition("://")
    return f"janus://{scheme}/{rest}"


def device_queue_name(name: str) -> str:
    return f"{name}-device"


def create_queue(
    name: str,
    device_uri: str,
    *,
    model: str = "everywhere",
    description: str = "",
    location: str = "",
    shared: bool = True,
) -> list[str]:
    """Create an inspected queue as a *pair*, and return any non-fatal warnings.

    CUPS runs its filters before the backend. A single queue built with `-m everywhere`
    against a modern printer therefore converts the job into that printer's raster format
    *before* janus ever sees it — and raster has no text, so every job arrives unreadable
    and the inspector is blind while appearing healthy. This is the single most important
    detail in the whole integration.

    So two queues:

        <name>          client-facing, raw. No filtering, so the client's own PostScript
                        or PDF reaches the backend intact and can be read.
        <name>-device   internal, driverless. Does the conversion and talks to the
                        hardware. Never shared, so nobody can print to it directly and
                        skip inspection.

    The client-facing queue points at the internal one through janus://, so the release
    path is: inspected job -> internal queue -> filters -> printer.
    """
    validate_name(name)
    validate_device_uri(device_uri)
    device_queue = device_queue_name(name)
    warnings: list[str] = []

    # 1. Internal queue that owns the hardware and does the format conversion.
    _run(["lpadmin", "-p", device_queue, "-E", "-v", device_uri, "-m", model])
    _run(["lpadmin", "-p", device_queue, "-D", f"internal device queue for {name}"])
    try:
        set_shared(device_queue, False)
    except CupsControlError as exc:
        # Worth surfacing: a shared device queue is a documented bypass route.
        warnings.append(f"could not un-share the internal device queue: {exc}")
    _run(["cupsenable", device_queue])
    _run(["cupsaccept", device_queue])

    # 2. Client-facing queue. Raw on purpose — the whole point is that CUPS does NOT
    #    transform the job before inspection.
    internal_uri = f"janus://ipp/localhost/printers/{device_queue}"
    _run(["lpadmin", "-p", name, "-E", "-v", internal_uri, "-m", "raw"])

    if description:
        _run(["lpadmin", "-p", name, "-D", description])
    if location:
        _run(["lpadmin", "-p", name, "-L", location])

    # Only the inspected queue is ever advertised.
    try:
        set_shared(name, shared)
    except CupsControlError as exc:
        if "remote queues" in str(exc):
            warnings.append(
                "queue created, but CUPS will not set sharing over the network. It will "
                "not be advertised for auto-discovery until sharing is applied on the "
                "print server itself (restart the cups service, or use ssh control mode)."
            )
        else:
            warnings.append(f"could not set sharing: {exc}")

    _run(["cupsenable", name])
    _run(["cupsaccept", name])
    return warnings


def delete_queue(name: str) -> None:
    """Remove the inspected queue and its internal device queue together.

    Leaving the device queue behind would strand a working, un-inspected route to the
    printer — exactly the thing this system exists to prevent.
    """
    validate_name(name)
    _run(["lpadmin", "-x", name])
    try:
        _run(["lpadmin", "-x", device_queue_name(name)])
    except CupsControlError as exc:
        log.warning("no internal device queue to remove for %s: %s", name, exc)


def set_shared(name: str, shared: bool) -> None:
    validate_name(name)
    _run(["lpadmin", "-p", name, "-o", f"printer-is-shared={'true' if shared else 'false'}"])


def set_enabled(name: str, enabled: bool) -> None:
    validate_name(name)
    _run(["cupsenable" if enabled else "cupsdisable", name])
    _run(["cupsaccept" if enabled else "cupsreject", name])


def list_queues() -> dict[str, str]:
    """Queue name -> device URI, as CUPS currently has it.

    Used to reconcile: a queue configured here but missing in CUPS looks configured while
    inspecting nothing.
    """
    output = _run(["lpstat", "-v"], check_output=True, timeout=QUICK_TIMEOUT)
    queues: dict[str, str] = {}
    for line in output.splitlines():
        # "device for office-printer: janus://ipp/10.0.0.5/ipp/print"
        match = re.match(r"device for ([^:]+): (.+)", line.strip())
        if match:
            queues[match.group(1)] = match.group(2)
    return queues


def printer_state(name: str) -> dict[str, str]:
    """What CUPS thinks of this queue: idle, processing, disabled, or not visible.

    `lpstat -p` returns nothing for an unshared queue when asked from another host, because
    CUPS only exposes shared printers remotely. Reporting that as "no response from cupsd"
    sends an operator hunting a connectivity fault that does not exist, so it is
    distinguished from a genuinely missing queue by cross-checking the device list.
    """
    validate_name(name)
    output = _run(["lpstat", "-p", name], check_output=True, timeout=QUICK_TIMEOUT).strip()

    if output:
        first = output.splitlines()[0]
        for state in ("is idle", "now printing", "is processing", "disabled"):
            if state in first:
                return {"state": state.replace("is ", "").replace("now ", ""), "detail": first}
        return {"state": "unknown", "detail": first}

    try:
        known = list_queues()
    except CupsControlError as exc:
        return {"state": "unknown", "detail": f"cupsd did not answer: {exc}"}

    if name in known:
        return {
            "state": "not-visible",
            "detail": "the queue exists but is not shared, so its status is not "
            "published to other hosts. This does not affect printing.",
        }
    return {"state": "missing", "detail": "cupsd does not have a queue with this name"}


def submit_file(queue: str, path: str, title: str) -> str:
    """Print a file to a queue. Returns the CUPS request id, if it reported one."""
    validate_name(queue)
    output = _run(["lp", "-d", queue, "-t", title, path], check_output=True)
    # "request id is office-printer-42 (1 file(s))"
    match = re.search(r"request id is (\S+)", output)
    return match.group(1) if match else ""


LOOPBACK = {"localhost", "127.0.0.1", "::1", "ip6-localhost"}


def device_endpoint(device_uri: str) -> tuple[str, int]:
    """Host and port to probe for reachability, derived from the device URI.

    Note the probe runs from wherever the API lives, while the device URI is written from
    the spooler's point of view. For a routable address the two agree; for a loopback
    address they do not, which is why callers reject those rather than probing themselves
    and reporting a meaningless refusal.
    """
    scheme, _, rest = device_uri.partition("://")
    scheme = scheme.lower()
    hostport = rest.split("/", 1)[0]

    if "@" in hostport:  # strip any credentials
        hostport = hostport.rsplit("@", 1)[1]

    if ":" in hostport:
        host, _, port = hostport.rpartition(":")
        try:
            return host, int(port)
        except ValueError:
            pass
    else:
        host = hostport

    default = {"ipp": 631, "ipps": 631, "http": 80, "https": 443, "socket": 9100, "lpd": 515}
    if scheme not in default:
        raise CupsControlError(f"cannot probe a {scheme}:// device")
    return host, default[scheme]


def available() -> bool:
    return _mode() != "none"


def describe() -> dict[str, str]:
    return {"mode": _mode(), "ssh_target": _ssh_target() or "-"}


def settings_summary() -> str:
    settings = get_settings()
    return f"deadline={settings.inspect_deadline_seconds}s cups_control={_mode()}"
