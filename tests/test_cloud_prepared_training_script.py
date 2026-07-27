"""Static security audit tests for cloud/train_3dgs_nerfstudio.sh (NOW-8).

Checks for mutable image/tag, unpinned CLI, shell injection, result
self-report, cleanup-before-publication, and log-leakage patterns.
Each issue is a reproducible RED test; minimal fixes follow in the script.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = _ROOT / "cloud" / "train_3dgs_nerfstudio.sh"
WORKER = _ROOT / "cloud" / "remote_training_worker.py"


def _read_script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _production_section(script: str) -> str:
    """Extract the production prepared-bundle mode function body."""
    start = script.find("run_production_prepared_bundle_mode()")
    if start < 0:
        start = script.find("run_production_prepared_bundle_mode")
    end_marker = "# END PRODUCTION PREPARED-BUNDLE MODE"
    end = script.find(end_marker)
    if end < 0:
        end = len(script)
    return script[start:end]


# ---------------------------------------------------------------------------
# NOW-8: mutable image/tag — pip install nerfstudio must pin version
# ---------------------------------------------------------------------------


def test_pip_install_nerfstudio_pins_version():
    """pip install nerfstudio must include ==1.1.5 version pin."""
    script = _read_script()
    lines = [
        line.strip()
        for line in script.splitlines()
        if "pip install" in line and "nerfstudio" in line
    ]
    assert lines, "expected at least one pip install nerfstudio line"
    for line in lines:
        assert "==1.1.5" in line, (
            f"pip install nerfstudio must pin ==1.1.5: {line}"
        )


# ---------------------------------------------------------------------------
# NOW-8: unpinned CLI — ns-train --version must be verified in production
# ---------------------------------------------------------------------------


def test_production_verifies_ns_train_cli_version():
    """RED→GREEN: production mode must verify ns-train --version output."""
    script = _read_script()
    prod = _production_section(script)
    assert re.search(r"ns-train.*--version", prod), (
        "production mode must verify ns-train --version output"
    )


# ---------------------------------------------------------------------------
# NOW-8: unpinned CLI — ns-export --version must be verified in production
# ---------------------------------------------------------------------------


def test_production_verifies_ns_export_cli_version():
    """RED→GREEN: production mode must verify ns-export --version output."""
    script = _read_script()
    prod = _production_section(script)
    assert re.search(r"ns-export.*--version", prod), (
        "production mode must verify ns-export --version output"
    )


# ---------------------------------------------------------------------------
# NOW-8: shell injection — no eval, no unquoted variables
# ---------------------------------------------------------------------------


def test_script_uses_set_euo_pipefail():
    """Script must use set -euo pipefail for safety."""
    script = _read_script()
    assert "set -euo pipefail" in script


def test_script_has_no_eval():
    """Script must not use eval (shell injection risk)."""
    script = _read_script()
    # Remove comments first
    lines = [
        line
        for line in script.splitlines()
        if not line.strip().startswith("#")
    ]
    clean = "\n".join(lines)
    assert "eval " not in clean and "eval$" not in clean, (
        "script must not use eval"
    )


# ---------------------------------------------------------------------------
# NOW-8: result self-report — exit code must come from PIPESTATUS, not $?
# ---------------------------------------------------------------------------


def test_production_captures_exit_via_pipestatus():
    """Production mode must capture exit code via PIPESTATUS[0], not $?."""
    script = _read_script()
    prod = _production_section(script)
    assert "PIPESTATUS[0]" in prod, (
        "production mode must use PIPESTATUS[0] to capture real exit code"
    )


# ---------------------------------------------------------------------------
# NOW-8: cleanup before publication — RUN_ROOT must not pre-exist
# ---------------------------------------------------------------------------


def test_production_run_root_must_not_preexist():
    """Production mode must fail if RUN_ROOT already exists (no stale data)."""
    script = _read_script()
    prod = _production_section(script)
    assert "必须不存在" in prod or "must not exist" in prod.lower(), (
        "production mode must reject pre-existing RUN_ROOT"
    )


# ---------------------------------------------------------------------------
# NOW-8: log leakage — script must not log secrets
# ---------------------------------------------------------------------------


def test_script_does_not_log_passwords_or_tokens():
    """Script must not echo passwords, tokens, or private keys."""
    script = _read_script()
    # Remove comments
    lines = [
        line
        for line in script.splitlines()
        if not line.strip().startswith("#")
    ]
    clean = "\n".join(lines)
    forbidden = [
        r"(?i)password\s*[=:]",
        r"(?i)token\s*[=:]",
        r"(?i)private[_-]?key\s*[=:]",
        r"(?i)secret\s*[=:]",
        r"(?i)credential\s*[=:]",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, clean), (
            f"script must not log secrets: {pattern}"
        )


# ---------------------------------------------------------------------------
# NOW-8: container identity must be verified as immutable digest
# ---------------------------------------------------------------------------


def test_production_verifies_container_identity_digest():
    """Production mode must verify container identity is sha256 digest."""
    script = _read_script()
    prod = _production_section(script)
    assert "sha256:" in prod and "64" in prod, (
        "production mode must verify container identity is sha256:64-hex"
    )


# ---------------------------------------------------------------------------
# NOW-8: worker security — structured argv, no shell=True
# ---------------------------------------------------------------------------


def test_worker_uses_structured_argv():
    """Worker must use structured argv, not shell=True."""
    worker_code = WORKER.read_text(encoding="utf-8")
    assert "shell=False" in worker_code, (
        "worker must use shell=False for all subprocess calls"
    )
    assert "shell=True" not in worker_code, (
        "worker must never use shell=True"
    )


def test_worker_has_no_eval():
    """Worker must not use eval."""
    worker_code = WORKER.read_text(encoding="utf-8")
    assert "eval(" not in worker_code, (
        "worker must not use eval()"
    )


# ---------------------------------------------------------------------------
# NOW-8: worker must not log secrets in exception handler
# ---------------------------------------------------------------------------


def test_worker_exception_handler_does_not_log_secrets():
    """Worker exception handler must only log exception type, not data."""
    worker_code = WORKER.read_text(encoding="utf-8")
    # The exception handler writes type(exc).__name__ only
    assert "type(exc).__name__" in worker_code, (
        "worker must log only exception type, not sensitive data"
    )
    # Must not log container_identity, bundle_sha, or paths in exception
    except_section = worker_code[
        worker_code.find("except (OSError") : worker_code.find(
            "return 75"
        )
    ]
    assert "container_identity" not in except_section, (
        "worker must not log container_identity in exception handler"
    )
    assert "training_bundle" not in except_section, (
        "worker must not log training_bundle_sha256 in exception handler"
    )
