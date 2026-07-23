#!/usr/bin/env python3
"""Emit cloud-GPU training provenance manifests (training-request / -result).

Runs on the cloud GPU instance alongside ``cloud/train_3dgs_nerfstudio.sh`` to
produce content-addressed manifests that ``scripts/prepare_import.py`` later
verifies locally.  Both manifests are built via ``pipeline.training_provenance``
so their canonical SHAs are byte-exact with what the local validator re-derives.

The result subcommand uses ``build_training_result`` so every SHA/size is
derived from authoritative bytes (PLY, config.yml, training log, and every
declared input artefact).  Self-reported fields are never trusted — the
emitter is just the byte-gathering side of the fail-closed contract.

Honest boundary: the manifests only bind what the operator actually feeds in.
The helper never invents SHAs, trainer versions, or GPU info.  When GPU fields
are omitted it attempts ``nvidia-smi``; if that fails it errors out (no silent
placeholder).  A verified handshake proves content closure only — never metric
/ aligned / real-photos.

Usage (request, before training)::

    python scripts/emit_training_provenance.py request \\
        --input capture_manifest:photos/capture_manifest.json \\
        --config-yml configs/splatfacto.yml \\
        --trainer nerfstudio-splatfacto --trainer-version 0.1.0 \\
        --max-resolution 800 --total-steps 10000 --seed 42 \\
        --output training-request.json

Usage (result, after export)::

    python scripts/emit_training_provenance.py result \\
        --request training-request.json \\
        --ply export/point_cloud.ply \\
        --config-yml outputs/.../config.yml \\
        --log training.log \\
        --trainer nerfstudio-splatfacto --trainer-version 0.1.0 \\
        --output training-result.json
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.training_provenance import (  # noqa: E402
    GpuEnvironment,
    TrainerDriftRecord,
    TrainingConfig,
    TrainingInputBinding,
    TrainingRequest,
    build_training_result,
    request_canonical_sha256,
    result_canonical_sha256,
)

# ============================================================
# Content-addressing helpers
# ============================================================

def _file_sha256_and_size(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _dir_content_bytes(path: Path) -> bytes:
    """Deterministic content bytes of a directory: concatenated
    ``relpath\\0size\\0sha\\n`` records over the sorted file list.

    Reproducible across machines (no tar metadata / mtimes).  The bytes are
    what ``input_bytes_by_path`` feeds to the validator so that
    ``hashlib.sha256(bytes).hexdigest()`` and ``len(bytes)`` match the
    directory binding's declared sha/size.
    """
    files = sorted(p for p in path.rglob("*") if p.is_file())
    parts: list[bytes] = []
    for f in files:
        rel = str(f.relative_to(path)).replace("\\", "/")
        sha, size = _file_sha256_and_size(f)
        parts.append(f"{rel}\0{size}\0{sha}\n".encode())
    return b"".join(parts)


def _dir_content_sha256_and_size(path: Path) -> tuple[str, int]:
    """Deterministic content address of a directory.

    Returns ``(sha256(content_bytes), len(content_bytes))`` so the size is
    the length of the reproducible manifest bytes (not the sum of file
    sizes).  This keeps sha/size mutually consistent for closure checks.
    """
    content = _dir_content_bytes(path)
    return hashlib.sha256(content).hexdigest(), len(content)


def _input_sha256_and_size(path: Path) -> tuple[str, int]:
    if path.is_dir():
        return _dir_content_sha256_and_size(path)
    return _file_sha256_and_size(path)


def _input_bytes_for_validation(path: Path) -> bytes:
    """Return the authoritative bytes used to verify a binding's sha/size.

    For a file this is the file's bytes.  For a directory this is the
    deterministic manifest bytes (so ``sha256(bytes) == dir_content_hash``
    and ``len(bytes) == manifest_len``).
    """
    if path.is_dir():
        return _dir_content_bytes(path)
    return path.read_bytes()


def _parse_ply_header(path: Path) -> tuple[int, int]:
    """Return (vertex_count, sh_degree) by parsing the PLY text header only.

    sh_degree is derived from the f_rest property count (INRIA 3DGS convention):
    per-color coeffs = total_f_rest // 3; n_coeffs = per_color + 1 (DC);
    degree = round(sqrt(n_coeffs)) - 1, validated by (degree+1)**2 == n_coeffs.
    """
    f_rest_count = 0
    vertex_count = 0
    with path.open("rb") as f:
        for raw in f:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line == "end_header":
                break
            m = re.match(r"element\s+vertex\s+(\d+)", line)
            if m:
                vertex_count = int(m.group(1))
            if re.match(r"property\s+\S+\s+f_rest_\d+", line):
                f_rest_count += 1
    if vertex_count == 0:
        raise ValueError(f"PLY header has no 'element vertex' line: {path}")
    n_coeffs = f_rest_count // 3 + 1
    degree = int(round(n_coeffs ** 0.5)) - 1
    if (degree + 1) ** 2 != n_coeffs:
        raise ValueError(
            f"f_rest count {f_rest_count} does not form a complete SH degree")
    return vertex_count, max(degree, 0)


def _detect_gpu() -> tuple[str, int, str, str]:
    """Auto-detect GPU via nvidia-smi.  Raises if unavailable (no placeholder)."""
    def _query(query: str) -> str:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=" + query, "--format=csv,noheader,nounits",
             "-i", "0"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip().splitlines()[0]

    name = _query("name")
    mem_mib = int(float(_query("memory.total")))
    # driver + cuda versions
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader",
         "-i", "0"], capture_output=True, text=True, check=True)
    driver = out.stdout.strip().splitlines()[0]
    cuda = _query("cuda_version") if _nvidia_smi_has_cuda() else _detect_cuda_from_nvcc()
    return name, mem_mib, cuda, driver


def _nvidia_smi_has_cuda() -> bool:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=cuda_version", "--format=csv,noheader",
             "-i", "0"], capture_output=True, text=True, check=True)
        return bool(out.stdout.strip())
    except Exception:
        return False


def _detect_cuda_from_nvcc() -> str:
    try:
        out = subprocess.run(["nvcc", "--version"], capture_output=True,
                             text=True, check=True)
        m = re.search(r"release\s+([\d.]+)", out.stdout + out.stderr)
        if m:
            return m.group(1)
    except Exception:
        pass
    raise RuntimeError(
        "cannot determine CUDA version (nvidia-smi and nvcc both unavailable); "
        "pass --cuda-version explicitly")


# ============================================================
# canonical JSON write (LF, pretty, sorted fields via model_dump_json)
# ============================================================

def _write_json(path: Path, model) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_iso_utc(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp into a timezone-aware datetime.

    Accepts trailing ``Z`` or explicit ``+00:00`` offset; the result is
    always normalized to ``datetime(UTC)``.
    """
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.utcoffset() is None:
        raise ValueError(f"timestamp must be UTC-aware: {value!r}")
    return dt.astimezone(UTC)


# ============================================================
# request subcommand
# ============================================================

def _cmd_request(args: argparse.Namespace) -> int:
    config_path = Path(args.config_yml)
    if not config_path.is_file():
        raise SystemExit(f"config.yml not found: {config_path}")
    config_bytes = config_path.read_bytes()
    requested_config_sha = hashlib.sha256(config_bytes).hexdigest()
    print(f"[CONFIG] {config_path}  sha256={requested_config_sha[:12]}...  "
          f"size={len(config_bytes)}")

    bindings: list[TrainingInputBinding] = []
    for spec in args.input:
        kind, _, raw_path = spec.partition(":")
        if not kind or not raw_path:
            raise SystemExit(
                f"--input must be 'kind:path'; got {spec!r}")
        p = Path(raw_path)
        if not p.exists():
            raise SystemExit(f"input path does not exist: {p}")
        sha, size = _input_sha256_and_size(p)
        bindings.append(TrainingInputBinding(
            artifact_kind=kind,
            artifact_sha256=sha,
            artifact_path=str(p).replace("\\", "/"),
            artifact_size_bytes=size,
        ))
        print(f"[INPUT] {kind}: {p}  sha256={sha[:12]}...  size={size}")

    config = TrainingConfig(
        trainer_name=args.trainer,
        trainer_version=args.trainer_version,
        max_resolution=args.max_resolution,
        total_steps=args.total_steps,
        random_seed=args.seed,
    )
    request = TrainingRequest(
        request_id=args.request_id or f"req-{_utc_now().strftime('%Y%m%dT%H%M%SZ')}",
        created_at_utc=_utc_now(),
        input_bindings=tuple(bindings),
        training_config=config,
        expected_output_format="inria-3dgs-ply",
        requested_config_sha256=requested_config_sha,
    )
    _write_json(Path(args.output), request)
    sha = request_canonical_sha256(request)
    print(f"[REQUEST] wrote {args.output}  canonical_sha256={sha}")
    return 0


# ============================================================
# result subcommand
# ============================================================

def _cmd_result(args: argparse.Namespace) -> int:
    request = TrainingRequest.model_validate_json(
        Path(args.request).read_text(encoding="utf-8"))

    ply = Path(args.ply)
    if not ply.is_file():
        raise SystemExit(f"PLY not found: {ply}")
    ply_bytes = ply.read_bytes()

    # Parse PLY header for gaussian_count + sh_degree.
    try:
        gaussian_count, sh_degree = _parse_ply_header(ply)
    except ValueError as exc:
        print(f"[WARN] could not parse PLY header: {exc}", file=sys.stderr)
        gaussian_count, sh_degree = None, None

    # Config bytes.
    config_path = Path(args.config_yml)
    if not config_path.is_file():
        raise SystemExit(f"config.yml not found: {config_path}")
    config_bytes = config_path.read_bytes()

    # Log bytes.
    log_path = Path(args.log)
    if not log_path.is_file():
        raise SystemExit(f"log not found: {log_path}")
    log_bytes = log_path.read_bytes()

    # GPU environment.  If every field is supplied explicitly, use them;
    # otherwise auto-detect via nvidia-smi and apply any explicit overrides.
    gpu_all_explicit = all(v is not None for v in (
        args.gpu_name, args.gpu_memory_mb, args.cuda_version, args.driver_version))
    if gpu_all_explicit:
        gpu_name = args.gpu_name
        cuda = args.cuda_version
        driver = args.driver_version
        mem = args.gpu_memory_mb
    else:
        gpu_name, mem, cuda, driver = _detect_gpu()
        if args.gpu_name:
            gpu_name = args.gpu_name
        if args.gpu_memory_mb is not None:
            mem = args.gpu_memory_mb
        if args.cuda_version:
            cuda = args.cuda_version
        if args.driver_version:
            driver = args.driver_version

    env = GpuEnvironment(
        gpu_name=gpu_name,
        gpu_memory_mb=mem,
        cuda_version=cuda,
        driver_version=driver,
    )

    # Actual trainer (may differ from request when a drift record is supplied).
    actual_trainer_name = args.trainer
    actual_trainer_version = args.trainer_version

    trainer_drift: TrainerDriftRecord | None = None
    if args.trainer_drift_reason:
        if actual_trainer_name == request.training_config.trainer_name and \
                actual_trainer_version == request.training_config.trainer_version:
            raise SystemExit(
                "--trainer-drift-reason supplied but actual trainer matches "
                "the request — no drift occurred")
        trainer_drift = TrainerDriftRecord(
            requested_trainer_name=request.training_config.trainer_name,
            requested_trainer_version=request.training_config.trainer_version,
            actual_trainer_name=actual_trainer_name,
            actual_trainer_version=actual_trainer_version,
            reason=args.trainer_drift_reason,
        )

    # Re-read input artefact bytes for closure verification.
    input_bytes_by_path: dict[str, bytes] = {}
    for binding in request.input_bindings:
        p = Path(binding.artifact_path)
        if not p.exists():
            raise SystemExit(
                f"input artefact no longer exists at declared path: {p}")
        input_bytes_by_path[binding.artifact_path] = _input_bytes_for_validation(p)

    # Timestamps.
    started = _parse_iso_utc(args.started_at) if args.started_at else _utc_now()
    finished = _parse_iso_utc(args.finished_at) if args.finished_at else _utc_now()

    # Exit code + error message.
    exit_code = args.exit_code
    error_message: str | None = args.error_message
    if exit_code != 0 and not error_message:
        error_message = f"trainer exited with code {exit_code}"

    # Fail fast: a non-zero exit code with a non-empty PLY is inconsistent —
    # the hardened validator would reject it anyway (failed/interrupted runs
    # cannot declare a trained_ply output).  Refuse to emit so the operator
    # sees the contradiction immediately rather than after a round-trip.
    if exit_code != 0 and ply_bytes:
        raise SystemExit(
            f"exit_code={exit_code} but PLY is non-empty "
            f"({len(ply_bytes)} bytes); failed/interrupted runs cannot "
            "produce a trained PLY — remove the --ply argument or use a "
            "zero-byte placeholder")

    result = build_training_result(
        request=request,
        result_id=args.result_id or f"res-{_utc_now().strftime('%Y%m%dT%H%M%SZ')}",
        started_at_utc=started,
        finished_at_utc=finished,
        actual_trainer_name=actual_trainer_name,
        actual_trainer_version=actual_trainer_version,
        actual_config_bytes=config_bytes,
        actual_ply_bytes=ply_bytes,
        actual_log_bytes=log_bytes,
        input_bytes_by_path=input_bytes_by_path,
        gpu_environment=env,
        exit_code=exit_code,
        error_message=error_message,
        gaussian_count=gaussian_count,
        sh_degree=sh_degree,
        trainer_drift=trainer_drift,
    )
    _write_json(Path(args.output), result)
    sha = result_canonical_sha256(result)
    print(f"[RESULT] wrote {args.output}  canonical_sha256={sha}")
    print(f"         ply_sha256={hashlib.sha256(ply_bytes).hexdigest()}  "
          f"gaussians={gaussian_count}  sh_degree={sh_degree}  "
          f"state={result.training_status.state}")
    return 0


# ============================================================
# CLI
# ============================================================

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Emit cloud-GPU training provenance manifests.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rq = sub.add_parser("request", help="emit training-request.json (pre-training)")
    rq.add_argument("--input", action="append", required=True, metavar="KIND:PATH",
                    help="content-addressed input binding (repeatable); "
                         "KIND in capture_manifest/registration_json/"
                         "registration_quality_report/sparse_model_dir; "
                         "PATH may be a file or directory (dir → deterministic "
                         "content hash)")
    rq.add_argument("--config-yml", required=True,
                    help="config.yml whose SHA-256 becomes "
                         "requested_config_sha256; the result subcommand must "
                         "bind a config with the same SHA (unless drift allowed)")
    rq.add_argument("--trainer", required=True,
                    choices=["nerfstudio-splatfacto", "brush", "gsplat", "inria"])
    rq.add_argument("--trainer-version", required=True)
    rq.add_argument("--max-resolution", type=int, default=800)
    rq.add_argument("--total-steps", type=int, default=10000)
    rq.add_argument("--seed", type=int, required=True,
                    help="random seed (required — no seed = not reproducible)")
    rq.add_argument("--request-id", default=None)
    rq.add_argument("--output", default="training-request.json")

    rs = sub.add_parser("result", help="emit training-result.json (post-training)")
    rs.add_argument("--request", required=True, help="path to training-request.json")
    rs.add_argument("--ply", required=True, help="trained point_cloud.ply")
    rs.add_argument("--config-yml", required=True, help="nerfstudio config.yml")
    rs.add_argument("--log", required=True, help="training log file")
    rs.add_argument("--trainer", required=True,
                    choices=["nerfstudio-splatfacto", "brush", "gsplat", "inria"])
    rs.add_argument("--trainer-version", required=True)
    rs.add_argument("--gpu-name", default=None)
    rs.add_argument("--gpu-memory-mb", type=int, default=None)
    rs.add_argument("--cuda-version", default=None)
    rs.add_argument("--driver-version", default=None)
    rs.add_argument("--exit-code", type=int, default=0,
                    help="trainer process exit code (default 0 = completed); "
                         "non-zero + non-empty PLY is rejected; non-zero with "
                         "no PLY yields failed/interrupted state")
    rs.add_argument("--error-message", default=None,
                    help="error message; required when exit-code != 0")
    rs.add_argument("--started-at", default=None,
                    help="ISO-8601 UTC start time (default: now)")
    rs.add_argument("--finished-at", default=None,
                    help="ISO-8601 UTC finish time (default: now)")
    rs.add_argument("--trainer-drift-reason", default=None,
                    help="if supplied, record a TrainerDriftRecord with this "
                         "reason; requires actual trainer != requested trainer")
    rs.add_argument("--result-id", default=None)
    rs.add_argument("--output", default="training-result.json")

    args = ap.parse_args(argv)
    if args.cmd == "request":
        return _cmd_request(args)
    if args.cmd == "result":
        return _cmd_result(args)
    ap.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
