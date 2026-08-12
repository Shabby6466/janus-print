"""The CUPS backend shim.

It is stdlib-only and lives outside the package (it is installed to
/usr/lib/cups/backend/janus), so it is loaded here by path.

What matters most here is not the happy path — it is that the shim never raises and never
returns an exit code CUPS will misread. A traceback in this file stops printing for the
building.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_PATH = Path(__file__).resolve().parents[1] / "backend" / "janus"


@pytest.fixture(scope="module")
def shim():
    spec = importlib.util.spec_from_loader(
        "janus_backend", importlib.machinery.SourceFileLoader("janus_backend", str(BACKEND_PATH))
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_is_valid_python():
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(BACKEND_PATH)], capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode()


class TestDeviceUri:
    def test_ipp_uri_is_unwrapped(self, shim):
        scheme, uri = shim.real_device_uri("janus://ipp/10.0.4.21/ipp/print")
        assert scheme == "ipp"
        assert uri == "ipp://10.0.4.21/ipp/print"

    def test_socket_uri_is_unwrapped(self, shim):
        scheme, uri = shim.real_device_uri("janus://socket/10.0.4.9:9100")
        assert (scheme, uri) == ("socket", "socket://10.0.4.9:9100")

    def test_hyphenated_backend_names_are_accepted(self, shim):
        # cups-pdf, cups-brf and driverless-fax are all real CUPS backends.
        assert shim.real_device_uri("janus://cups-pdf/") == ("cups-pdf", "cups-pdf://")

    @pytest.mark.parametrize(
        "uri",
        [
            "",
            "ipp://10.0.4.21/print",
            "janus://",
            "janus://noslash",
            "not a uri",
            "janus://../../bin/sh/host",  # would exec the wrong binary as root
            "janus://.hidden/host",
        ],
    )
    def test_bad_uris_are_rejected_rather_than_guessed(self, shim, uri):
        assert shim.real_device_uri(uri) == (None, None)


class TestExitCodes:
    def test_codes_match_the_cups_contract(self, shim):
        # Getting these wrong silently changes what happens to every job.
        assert shim.CUPS_BACKEND_OK == 0
        assert shim.CUPS_BACKEND_FAILED == 1
        assert shim.CUPS_BACKEND_HOLD == 3
        assert shim.CUPS_BACKEND_STOP == 4
        assert shim.CUPS_BACKEND_CANCEL == 5


class TestDiscovery:
    def test_no_arguments_advertises_the_device(self, shim, capsys):
        assert shim.main(["janus"]) == shim.CUPS_BACKEND_OK
        assert "janus" in capsys.readouterr().out

    def test_too_few_arguments_fails_cleanly(self, shim):
        assert shim.main(["janus", "1", "user"]) == shim.CUPS_BACKEND_FAILED

    def test_bad_device_uri_stops_the_queue(self, shim, monkeypatch):
        monkeypatch.setenv("DEVICE_URI", "nonsense://host")
        code = shim.main(["janus", "1", "user", "title", "1", ""])
        # STOP, not FAILED: a misconfigured queue should stop, not retry forever.
        assert code == shim.CUPS_BACKEND_STOP


class TestConfig:
    def test_defaults_are_fail_open(self, shim):
        # The default that keeps the product deployed (PLAN.md §4).
        assert shim.DEFAULTS["fail_mode"] == "open"

    def test_environment_overrides_config(self, shim, monkeypatch):
        monkeypatch.setenv("JANUS_PRINT_BACKEND_FAIL_MODE", "closed")
        monkeypatch.setenv("JANUS_PRINT_BACKEND_API_URL", "http://example:9999")
        config = shim.load_config()
        assert config["fail_mode"] == "closed"
        assert config["api_url"] == "http://example:9999"


class TestMultipart:
    def test_encoding_is_well_formed(self, shim):
        body, content_type = shim.encode_multipart(
            {"queue": "office-laser", "username": "jdoe"}, "job.pdf", b"%PDF-1.4 body"
        )
        boundary = content_type.split("boundary=")[1]
        assert body.startswith(f"--{boundary}".encode())
        assert body.rstrip().endswith(f"--{boundary}--".encode())
        assert b'name="queue"' in body
        assert b'name="document"; filename="job.pdf"' in body
        assert b"%PDF-1.4 body" in body

    def test_binary_payloads_survive_intact(self, shim):
        payload = bytes(range(256))
        body, _ = shim.encode_multipart({}, "job.prn", payload)
        assert payload in body


class TestFailureBehaviour:
    def _argv(self):
        return ["janus", "77", "jdoe", "report.pdf", "1", ""]

    def test_unreachable_api_fails_open_by_default(self, shim, monkeypatch, tmp_path):
        """The decision that keeps printers working during an inspector outage."""
        monkeypatch.setenv("DEVICE_URI", "janus://ipp/printer.local/ipp/print")
        monkeypatch.setenv("JANUS_PRINT_BACKEND_API_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("JANUS_PRINT_BACKEND_FAIL_MODE", "open")

        spool = tmp_path / "job.pdf"
        spool.write_bytes(b"%PDF-1.4 test")
        monkeypatch.setattr(shim, "spool_job", lambda argv: (str(spool), False))

        released = {}

        def fake_exec(scheme, uri, argv, path):
            released.update(scheme=scheme, uri=uri, path=path)
            return shim.CUPS_BACKEND_OK

        monkeypatch.setattr(shim, "exec_real_backend", fake_exec)

        assert shim.main([*self._argv(), str(spool)]) == shim.CUPS_BACKEND_OK
        assert released["uri"] == "ipp://printer.local/ipp/print"

    def test_unreachable_api_holds_when_fail_closed(self, shim, monkeypatch, tmp_path):
        monkeypatch.setenv("DEVICE_URI", "janus://ipp/printer.local/ipp/print")
        monkeypatch.setenv("JANUS_PRINT_BACKEND_API_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("JANUS_PRINT_BACKEND_FAIL_MODE", "closed")

        spool = tmp_path / "job.pdf"
        spool.write_bytes(b"%PDF-1.4 test")
        monkeypatch.setattr(shim, "spool_job", lambda argv: (str(spool), False))

        assert shim.main([*self._argv(), str(spool)]) == shim.CUPS_BACKEND_HOLD

    def test_oversized_job_skips_inline_inspection(self, shim, monkeypatch, tmp_path):
        monkeypatch.setenv("DEVICE_URI", "janus://ipp/printer.local/ipp/print")
        monkeypatch.setenv("JANUS_PRINT_BACKEND_MAX_BYTES", "10")
        monkeypatch.setenv("JANUS_PRINT_BACKEND_FAIL_MODE", "open")

        spool = tmp_path / "big.pdf"
        spool.write_bytes(b"x" * 5000)
        monkeypatch.setattr(shim, "spool_job", lambda argv: (str(spool), False))
        monkeypatch.setattr(
            shim, "exec_real_backend", lambda *_a: shim.CUPS_BACKEND_OK
        )
        # Must not attempt the POST at all.
        monkeypatch.setattr(
            shim,
            "post_for_inspection",
            lambda *_a: pytest.fail("oversized job was sent for inline inspection"),
        )

        assert shim.main([*self._argv(), str(spool)]) == shim.CUPS_BACKEND_OK

    def test_verdicts_map_to_the_right_exit_codes(self, shim, monkeypatch, tmp_path):
        monkeypatch.setenv("DEVICE_URI", "janus://ipp/printer.local/ipp/print")
        spool = tmp_path / "job.pdf"
        spool.write_bytes(b"%PDF-1.4 test")
        monkeypatch.setattr(shim, "spool_job", lambda argv: (str(spool), False))
        monkeypatch.setattr(shim, "exec_real_backend", lambda *_a: shim.CUPS_BACKEND_OK)

        for action, expected in [
            ("allow", shim.CUPS_BACKEND_OK),
            ("log", shim.CUPS_BACKEND_OK),
            ("hold", shim.CUPS_BACKEND_HOLD),
            ("block", shim.CUPS_BACKEND_CANCEL),
        ]:
            monkeypatch.setattr(
                shim,
                "post_for_inspection",
                lambda *_a, action=action: {"action": action, "reason": "test", "score": 0.9},
            )
            assert shim.main([*self._argv(), str(spool)]) == expected, action
