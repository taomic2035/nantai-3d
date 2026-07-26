from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _ROOT / "cloud" / "train_3dgs_nerfstudio.sh"


def _production_function() -> str:
    script = _SCRIPT.read_text(encoding="utf-8")
    start = script.index("# BEGIN PRODUCTION PREPARED-BUNDLE MODE")
    end = script.index("# END PRODUCTION PREPARED-BUNDLE MODE")
    return script[start:end]


def test_production_mode_never_installs_or_reruns_sfm():
    production = _production_function()

    assert "pip install" not in production
    assert "ns-process-data" not in production
    assert "colmap" not in production.lower()
    assert "cloud.prepare_real_scene_dataset" in production


def test_production_mode_pins_runtime_and_coordinate_flags():
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


def test_invalid_container_identity_fails_before_runtime_probe(tmp_path):
    bundle = tmp_path / "training-job.zip"
    bundle.write_bytes(b"not-read-before-container-validation")

    result = subprocess.run(
        [
            "bash",
            str(_SCRIPT),
            "--prepared-bundle",
            str(bundle),
            "--container-identity",
            "mutable:latest",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "digest" in result.stderr.lower()


def _write_executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_prepared_mode_runs_pinned_stubbed_golden_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
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
    bundle = tmp_path / "training-job.zip"
    bundle.write_bytes(b"stubbed-verified-bundle")
    argv_file = tmp_path / "ns-train.argv"
    export_argv_file = tmp_path / "ns-export.argv"
    work = tmp_path / "work"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "PYTHON_BIN": str(python_stub),
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
            "bash",
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
        token.decode("utf-8")
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
        token.decode("utf-8")
        for token in export_argv_file.read_bytes().split(b"\0")
        if token
    ]
    assert (
        export_tokens[export_tokens.index("--output-filename") + 1]
        == "point_cloud.ply"
    )


def test_cloud_script_is_valid_bash():
    result = subprocess.run(
        ["bash", "-n", str(_SCRIPT)],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
