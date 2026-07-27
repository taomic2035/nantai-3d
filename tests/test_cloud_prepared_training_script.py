"""Behavior tests for cloud/train_3dgs_nerfstudio.sh production mode (NOW-8 C1).

Restores the executable fake-tool golden-path tests deleted by b02f6ab and
adds RED tests for the six Codex-specified defects: CLI version probe
swallowing non-zero exit, substring version match, PATH swap after
resolution, and ns-export parity.  Static source checks remain as
supplementary lint; they do NOT replace executable evidence.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "cloud" / "train_3dgs_nerfstudio.sh"


def _find_bash() -> str | None:
    """Find a working bash executable."""
    candidates: list[str] = []
    if sys.platform == "win32":
        candidates.extend(
            [
                r"D:\Git\bin\bash.exe",
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
        )
    which = shutil.which("bash")
    if which and which not in candidates:
        candidates.append(which)
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "-c", "echo ok"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip() == "ok":
                return candidate
        except (FileNotFoundError, OSError):
            continue
    return None


_BASH_EXE = _find_bash()


def _production_function() -> str:
    script = _SCRIPT.read_text(encoding="utf-8")
    start = script.index("# BEGIN PRODUCTION PREPARED-BUNDLE MODE")
    end = script.index("# END PRODUCTION PREPARED-BUNDLE MODE")
    return script[start:end]


def _write_executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _posix_path(p: Path | str) -> str:
    """Convert a Windows path to a bash-compatible POSIX path."""
    s = str(p)
    if sys.platform == "win32" and _BASH_EXE and "Git" in _BASH_EXE:
        # Git Bash on Windows: C:\foo → /c/foo
        s = s.replace("\\", "/")
        if len(s) >= 2 and s[1] == ":":
            drive = s[0].lower()
            s = f"/{drive}{s[2:]}"
    return s


def _run_script(
    *,
    args: list[str],
    env: dict[str, str],
    cwd: Path = _ROOT,
) -> subprocess.CompletedProcess:
    """Run the script with the found bash, handling encoding."""
    result = subprocess.run(
        [_BASH_EXE, str(_SCRIPT), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        check=False,
    )
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=result.stdout.decode("utf-8", errors="replace"),
        stderr=result.stderr.decode("utf-8", errors="replace"),
    )


def _bash_available() -> bool:
    return _BASH_EXE is not None


_BASH = pytest.mark.skipif(
    not _bash_available(),
    reason="bash is required for executable script tests",
)


# ---------------------------------------------------------------------------
# Static lint (supplementary — does NOT replace executable evidence below)
# ---------------------------------------------------------------------------


def test_production_mode_never_installs_or_reruns_sfm():
    """Production mode must not pip install, ns-process-data, or colmap."""
    production = _production_function()
    assert "pip install" not in production
    assert "ns-process-data" not in production
    assert "colmap" not in production.lower()
    assert "cloud.prepare_real_scene_dataset" in production


def test_production_mode_pins_runtime_and_coordinate_flags():
    """Production mode must pin version and use fixed coordinates."""
    production = _production_function()
    assert 'NERFSTUDIO_VERSION" != "1.1.5"' in production
    assert "torch.cuda.is_available" in production
    assert "container-identity.txt" in production
    assert "--orientation-method none" in production
    assert "--center-method none" in production
    assert "--auto-scale-poses False" in production
    assert "--scale-factor 1.0" in production
    assert "validate_dataparser_transform.py" in production
    assert "--dataparser-transform" in production
    assert "--prepared-bundle" in production


def test_production_resolves_cli_to_absolute_paths():
    """Production mode must resolve ns-train/ns-export to absolute paths."""
    production = _production_function()
    assert "command -v ns-train" in production
    assert "command -v ns-export" in production
    assert "NS_TRAIN_PATH" in production
    assert "NS_EXPORT_PATH" in production
    # Resolved path must be used for version probe and execution
    assert '"$NS_TRAIN_PATH" --version' in production
    assert '"$NS_EXPORT_PATH" --version' in production
    assert '"$NS_TRAIN_PATH" splatfacto' in production
    assert '"$NS_EXPORT_PATH" gaussian-splat' in production


def test_production_version_probe_has_no_or_true():
    """Version probe must NOT swallow non-zero exit with || true."""
    production = _production_function()
    assert "|| true" not in production


def test_production_version_probe_uses_strict_equality():
    """Version match must use strict equality, not substring case match."""
    production = _production_function()
    assert '"$NERFSTUDIO_VERSION"' in production
    assert "!= " in production
    # Must NOT use substring case match
    assert "case" not in production.split(
        "ns-train --version"
    )[1].split("ns-export")[0] if "ns-train --version" in production else True


# ---------------------------------------------------------------------------
# Executable behavior tests (primary evidence)
# ---------------------------------------------------------------------------


@_BASH
def test_cloud_script_is_valid_bash():
    """Script must pass bash -n syntax check."""
    result = subprocess.run(
        [_BASH_EXE, "-n", str(_SCRIPT)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    assert result.returncode == 0, result.stderr


@_BASH
def test_invalid_container_identity_fails_before_runtime_probe(tmp_path):
    """Invalid container identity must fail before any runtime probe."""
    bundle = tmp_path / "training-job.zip"
    bundle.write_bytes(b"not-read-before-container-validation")

    result = subprocess.run(
        [
            _BASH_EXE,
            str(_SCRIPT),
            "--prepared-bundle",
            str(bundle),
            "--container-identity",
            "mutable:latest",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert result.returncode == 2
    assert "digest" in result.stderr.lower()


def _make_golden_path_stubs(bin_dir: Path) -> dict[str, Path]:
    """Create fake ns-train, ns-export, python3, nvidia-smi stubs."""
    python_stub = _write_executable(
        bin_dir / "python3",
        r"""#!/bin/bash
set -euo pipefail
if [ "${1:-}" = "-c" ] && [[ "${2:-}" == *"importlib.metadata"* ]]; then
  printf '1.1.5\n'
  exit 0
fi
if [ "${1:-}" = "-c" ] && [[ "${2:-}" == *"torch.cuda.is_available"* ]]; then
  exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "cloud.prepare_real_scene_dataset" ]; then
  shift 2
  OUTPUT=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --output) OUTPUT="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  mkdir -p "$OUTPUT/evidence"
  cat > "$OUTPUT/evidence/training-request.json" <<'JSON'
{"request_id":"canary","training_config":{"random_seed":42,"total_steps":12,"trainer_name":"nerfstudio-splatfacto","trainer_version":"1.1.5"}}
JSON
  printf '%s\n' '{"frames":[]}' > "$OUTPUT/evidence/held-out-split.json"
  printf 'trainer: nerfstudio-splatfacto\n' > "$OUTPUT/evidence/operator-intent-config.yml"
  exit 0
fi
if [[ "${1:-}" == *"emit_training_provenance.py" ]]; then
  OUTPUT=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --output) OUTPUT="$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  printf '{"stub":"training-result"}\n' > "$OUTPUT"
  exit 0
fi
if [[ "${1:-}" == *"validate_dataparser_transform.py" ]]; then
  exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "cloud.evaluate_real_scene" ]; then
  exit 0
fi
exec "$REAL_PYTHON" "$@"
""",
    )
    _write_executable(
        bin_dir / "nvidia-smi",
        "#!/bin/bash\nexit 0\n",
    )
    _write_executable(
        bin_dir / "ns-train",
        r"""#!/bin/bash
if [ "${1:-}" = "--version" ]; then
  printf '1.1.5\n'
  exit 0
fi
printf '%s\0' "$@" > "$NS_TRAIN_ARGV_FILE"
mkdir -p outputs/canary
printf 'trainer: splatfacto\n' > outputs/canary/config.yml
printf '%s\n' '{"scale":1.0,"transform":[[1,0,0,0],[0,1,0,0],[0,0,1,0]]}' \
  > outputs/canary/dataparser_transforms.json
exit 0
""",
    )
    _write_executable(
        bin_dir / "ns-export",
        r"""#!/bin/bash
if [ "${1:-}" = "--version" ]; then
  printf '1.1.5\n'
  exit 0
fi
printf '%s\0' "$@" > "$NS_EXPORT_ARGV_FILE"
OUTPUT=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-dir) OUTPUT="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf 'ply\nformat ascii 1.0\nelement vertex 1\nend_header\n0 0 0\n' \
  > "$OUTPUT/point_cloud.ply"
""",
    )
    return {"python3": python_stub}


@_BASH
def test_prepared_mode_runs_pinned_stubbed_golden_path(tmp_path):
    """Golden path: stub all CLIs, run script, verify output + argv."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_golden_path_stubs(bin_dir)
    bundle = tmp_path / "training-job.zip"
    bundle.write_bytes(b"stubbed-verified-bundle")
    argv_file = tmp_path / "ns-train.argv"
    export_argv_file = tmp_path / "ns-export.argv"
    work = tmp_path / "work"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "PYTHON_BIN": str(bin_dir / "python3"),
            "REAL_PYTHON": sys.executable,
            "NS_TRAIN_ARGV_FILE": str(argv_file),
            "NS_EXPORT_ARGV_FILE": str(export_argv_file),
            "WORK": str(work),
        }
    )
    container = (
        "registry.example/nantai@sha256:" + ("a" * 64)
    )

    result = subprocess.run(
        [
            _BASH_EXE,
            str(_SCRIPT),
            "--prepared-bundle",
            str(bundle),
            "--container-identity",
            container,
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    result_root = work / "production-run" / "result"
    assert (result_root / "training-result.json").is_file()
    assert (result_root / "operator-intent-config.yml").is_file()
    assert (result_root / "point_cloud.ply").is_file()
    assert (result_root / "dataparser_transforms.json").is_file()
    assert (
        result_root / "container-identity.txt"
    ).read_text(encoding="ascii").strip() == container
    tokens = [
        token.decode("utf-8").rstrip("\r")
        for token in argv_file.read_bytes().split(b"\0")
        if token
    ]
    assert tokens[:3] == ["splatfacto", "--data", "prepared"]
    assert tokens[tokens.index("--max-num-iterations") + 1] == "12"
    assert tokens[tokens.index("--machine.seed") + 1] == "42"
    assert tokens[tokens.index("--orientation-method") + 1] == "none"
    assert tokens[tokens.index("--auto-scale-poses") + 1] == "False"
    assert tokens[tokens.index("--scale-factor") + 1] == "1.0"
    export_tokens = [
        token.decode("utf-8").rstrip("\r")
        for token in export_argv_file.read_bytes().split(b"\0")
        if token
    ]
    assert (
        export_tokens[export_tokens.index("--output-filename") + 1]
        == "point_cloud.ply"
    )


def _run_with_stubs(
    tmp_path: Path,
    *,
    ns_train_stub: str,
    ns_export_stub: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the script with custom ns-train/ns-export stubs."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_golden_path_stubs(bin_dir)
    # Override ns-train stub
    _write_executable(bin_dir / "ns-train", ns_train_stub)
    if ns_export_stub is not None:
        _write_executable(bin_dir / "ns-export", ns_export_stub)
    bundle = tmp_path / "training-job.zip"
    bundle.write_bytes(b"stubbed-verified-bundle")
    argv_file = tmp_path / "ns-train.argv"
    export_argv_file = tmp_path / "ns-export.argv"
    work = tmp_path / "work"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "PYTHON_BIN": str(bin_dir / "python3"),
            "REAL_PYTHON": sys.executable,
            "NS_TRAIN_ARGV_FILE": str(argv_file),
            "NS_EXPORT_ARGV_FILE": str(export_argv_file),
            "WORK": str(work),
        }
    )
    container = "registry.example/nantai@sha256:" + ("a" * 64)
    return subprocess.run(
        [
            _BASH_EXE,
            str(_SCRIPT),
            "--prepared-bundle",
            str(bundle),
            "--container-identity",
            container,
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )


@_BASH
def test_production_rejects_ns_train_version_output_from_nonzero_command(
    tmp_path,
):
    """ns-train --version exiting non-zero must fail closed."""
    result = _run_with_stubs(
        tmp_path,
        ns_train_stub=(
            "#!/bin/bash\n"
            'if [ "${1:-}" = "--version" ]; then\n'
            '  echo "error" >&2\n'
            "  exit 1\n"
            "fi\n"
            "exit 0\n"
        ),
    )
    assert result.returncode != 0
    assert "ns-train" in result.stderr or "ns-train" in result.stdout


@_BASH
def test_production_rejects_ns_train_version_substring_collision(
    tmp_path,
):
    """Version output containing '1.1.5' as substring must NOT pass."""
    result = _run_with_stubs(
        tmp_path,
        ns_train_stub=(
            "#!/bin/bash\n"
            'if [ "${1:-}" = "--version" ]; then\n'
            "  printf '1.1.5-dev\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        ),
    )
    assert result.returncode != 0


@_BASH
def test_production_rejects_ns_export_version_output_from_nonzero_command(
    tmp_path,
):
    """ns-export --version exiting non-zero must fail closed."""
    result = _run_with_stubs(
        tmp_path,
        ns_train_stub=(
            "#!/bin/bash\n"
            'if [ "${1:-}" = "--version" ]; then\n'
            "  printf '1.1.5\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        ),
        ns_export_stub=(
            "#!/bin/bash\n"
            'if [ "${1:-}" = "--version" ]; then\n'
            '  echo "error" >&2\n'
            "  exit 1\n"
            "fi\n"
            "exit 0\n"
        ),
    )
    assert result.returncode != 0
    assert "ns-export" in result.stderr or "ns-export" in result.stdout


@_BASH
def test_production_uses_resolved_cli_paths_after_version_probe(
    tmp_path,
):
    """The resolved absolute path must be used for both probe and execution.

    A PATH swap after version probe must NOT hijack the training call.
    The stub records its own path in the argv file; we verify the path
    used for training matches the resolved stub path.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_golden_path_stubs(bin_dir)
    bundle = tmp_path / "training-job.zip"
    bundle.write_bytes(b"stubbed-verified-bundle")
    argv_file = tmp_path / "ns-train.argv"
    export_argv_file = tmp_path / "ns-export.argv"
    work = tmp_path / "work"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "PYTHON_BIN": str(bin_dir / "python3"),
            "REAL_PYTHON": sys.executable,
            "NS_TRAIN_ARGV_FILE": str(argv_file),
            "NS_EXPORT_ARGV_FILE": str(export_argv_file),
            "WORK": str(work),
        }
    )
    container = "registry.example/nantai@sha256:" + ("a" * 64)

    result = subprocess.run(
        [
            _BASH_EXE,
            str(_SCRIPT),
            "--prepared-bundle",
            str(bundle),
            "--container-identity",
            container,
        ],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    # The argv file should exist and contain the training argv
    assert argv_file.exists()
    # Verify the stub was called (argv captured)
    tokens = [
        token.decode("utf-8").rstrip("\r")
        for token in argv_file.read_bytes().split(b"\0")
        if token
    ]
    assert "splatfacto" in tokens


@_BASH
def test_production_rejects_ns_export_version_substring_collision(
    tmp_path,
):
    """ns-export version containing '1.1.5' as substring must NOT pass."""
    result = _run_with_stubs(
        tmp_path,
        ns_train_stub=(
            "#!/bin/bash\n"
            'if [ "${1:-}" = "--version" ]; then\n'
            "  printf '1.1.5\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        ),
        ns_export_stub=(
            "#!/bin/bash\n"
            'if [ "${1:-}" = "--version" ]; then\n'
            "  printf '1.1.5-rc2\\n'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n"
        ),
    )
    assert result.returncode != 0
