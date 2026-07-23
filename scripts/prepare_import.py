#!/usr/bin/env python3
"""为「导入外部训练好的 3DGS」生成 registration.json + splat-input.json。

本仓库的导入路径 (`reconstruct --engine import`) 需要两个 JSON 契约：
一个声明坐标系的 registration，一个把 .ply 绑到该坐标系的 SplatInput。手写它们
容易出错（source_frame 必须与 registration 的 frame 逐字段一致，session_id 要对上）。
本脚本据一个训练好的 .ply 自动生成最简单的 **sfm-local**（arbitrary/unaligned）契约，
让导入变成一条命令。

结果是 `preview-only`——诚实：没有控制点就不冒充米制。要 metric-aligned，先用
`pipeline.alignment` 加控制点/GPS，见 docs/real-data-workflow.md。

来源是合成影像（如 canary Blender 渲染 + GT 相机训练的 3DGS）时加 `--synthetic`：
frame provenance 申报为 SYNTHETIC，导入分类自动降为 synthetic=true + preview-proxy
（只降不升——申报合成永远不会提升信任等级，不申报则错标为非合成）。

可选 `--colmap-sparse <sparse/0/points3D.txt>`：当你**声称**这个 ply 是用某个本机 COLMAP
workspace 训练出来的（Brush / INRIA 原版这类直接吃 workspace 且保留其坐标系的训练器），
用它做几何一致性检查——对不上就 fail-closed 拒绝生成契约。**只能证伪不能证实**：
检查不通过是强证据说明拿错了文件；检查"没发现矛盾"**不代表**这个 ply 真来自该 workspace。
云端 nerfstudio 路线会重跑 COLMAP 并 re-center/rescale，产物不在本机 sparse 坐标系里，
**不要**对它用这个参数（见 pipeline/splat_provenance.py 的限制一节）。

可选 training provenance handshake（P0.3 hardened）：

- `--training-result` + `--training-request`（成对必填）：验证内容闭包
  （输入/config/log/PLY 字节全部重算匹配）。验证通过但未提供 registration
  quality 时追加弱 receipt ``training_content_closed.v1=<result_sha>``——
  **不是** trusted prefix，明说只证内容闭包。

- 再加 `--registration-quality-report` + `--registration-json` +
  `--registration-quality-policy`（四参数成对必填）：验证 registration quality
  report，derive trust with ``registration_quality_passed=report.quality_accepted``。
  只有 ``is_trustworthy=True`` 时才追加 trusted prefix
  ``training_provenance.v1=<result_sha>``。

  可选 `--capture-manifest` 和 `--sparse-model-dir` 传给 registration quality
  validator 用于 colmap engine 的 capture / sparse 验证。

验证不通过则 fail-closed 拒绝生成契约，除非显式 `--allow-unverified-training`
（开发用，不产生任何 evidence）。

诚实边界：即使 trusted prefix 追加，prepare_import 仍不改 metric_status 或
geo_aligned（PLY 仍是 sfm-local / preview-only）。trusted prefix 只证明
request/result/registration-quality 三者内容闭包且训练已完成，**不**隐含
模型可信、米制或真实照片。

用法:
    python scripts/prepare_import.py trained/point_cloud.ply [--synthetic]
    # 生成 recon/registration.json + recon/splat-input.json, 并打印导入命令
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.recon_schema import (  # noqa: E402
    AlignmentStatus,
    AxisConvention,
    CaptureSession,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    GeoAlignment,
    Handedness,
    MetricStatus,
    RegistrationResult,
    SplatInput,
)

if TYPE_CHECKING:
    from pipeline.registration_quality import SparseModelEnumeration
    from pipeline.training_provenance import TrainingTrust


# ============================================================
# Local frame
# ============================================================

def _local_frame(
    synthetic: bool,
    extra_evidence: tuple[str, ...] = (),
) -> CoordinateFrame:
    """外部训练产物的默认坐标契约：任意尺度、无地理对齐（诚实，不假装米制）。

    synthetic=True 只改申报的来源 provenance（SYNTHETIC + 证据标签），刻意不改
    units/metric_status —— 申报合成是降级声明，不得顺带夹带任何米制提升。

    extra_evidence 追加到 evidence 元组——用于 training_provenance.v1=<sha>
    等前缀证据字符串。**只追加证据，不改 metric_status / geo_aligned**。
    """
    base_evidence = (["external-3dgs-import", "synthetic-source-declared"]
                     if synthetic else ["external-3dgs-import"])
    return CoordinateFrame(
        frame_id="synthetic-local" if synthetic else "sfm-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SYNTHETIC if synthetic else FrameProvenance.SFM,
        evidence=tuple(base_evidence) + extra_evidence,
    )


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def prepare(
    ply: Path,
    out_dir: Path,
    session_id: str,
    synthetic: bool = False,
    extra_evidence: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    frame = _local_frame(synthetic, extra_evidence=extra_evidence)
    reg = RegistrationResult(
        schema_version=2,
        engine="external",  # honest: not colmap/mock — an external-declared import
        pose_frame=frame,
        world_frame=None,
        alignment_status=AlignmentStatus.UNALIGNED,
        sessions=[CaptureSession(session_id=session_id, kind="photo_batch",
                                 source="external-3dgs", images=[])],
        poses=[],  # import path ignores poses; the .ply already carries geometry
    )
    splat = SplatInput(
        session_id=session_id,
        path=str(ply).replace("\\", "/"),
        source_frame=frame,  # byte-identical to registration frame -> no transform
        transform=None,
    )
    reg_path = out_dir / "registration.json"
    splat_path = out_dir / "splat-input.json"
    _write_lf(reg_path, reg.model_dump_json(indent=2))
    _write_lf(splat_path, splat.model_dump_json(indent=2))
    return reg_path, splat_path


# ============================================================
# Content-addressing helpers (shared with emit_training_provenance)
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
    """Deterministic manifest bytes of a directory (relpath\\0size\\0sha\\n)."""
    files = sorted(p for p in path.rglob("*") if p.is_file())
    parts: list[bytes] = []
    for f in files:
        rel = str(f.relative_to(path)).replace("\\", "/")
        sha, size = _file_sha256_and_size(f)
        parts.append(f"{rel}\0{size}\0{sha}\n".encode())
    return b"".join(parts)


def _input_bytes_for_validation(path: Path) -> bytes:
    """Authoritative bytes for closure verification (file bytes or dir manifest)."""
    if path.is_dir():
        return _dir_content_bytes(path)
    return path.read_bytes()


def _read_binding_path_or_fail(path_str: str, label: str) -> bytes:
    """Read bytes at a binding's declared path; fail-closed if missing."""
    p = Path(path_str)
    if not p.exists():
        raise FileNotFoundError(
            f"{label} binding path does not exist locally: {p} — cannot verify "
            f"closure; re-run on the cloud instance or download the workspace")
    return _input_bytes_for_validation(p)


# ============================================================
# Training provenance + registration quality handshake (P0.3)
# ============================================================

def _validate_training_provenance(
    ply: Path,
    training_result_path: Path,
    training_request_path: Path,
) -> tuple[bool, str | None, TrainingTrust | None]:
    """验证云 GPU 训练 provenance handshake 的内容闭包。

    返回 (content_closed, result_sha, trust)。
    - content_closed=True: 输入/配置/输出/环境内容闭包通过且训练已完成。
      caller 可追加弱 receipt ``training_content_closed.v1=<result_sha>``。
    - content_closed=False: 验证失败，调用者应 fail-closed。

    诚实边界：prepare_import **不**执行 SfM registration 质量门（除非 caller
    通过 ``--registration-quality-report`` 单独传入），故
    ``registration_quality_passed`` 恒为 False，``TrainingTrust.is_trustworthy``
    恒为 False。trusted prefix 需 caller 额外传入 registration quality report。
    """
    from pipeline.training_provenance import (
        TrainingRequest,
        TrainingResult,
        derive_training_trust,
        result_canonical_sha256,
        validate_training_provenance,
    )

    request = TrainingRequest.model_validate_json(
        training_request_path.read_text(encoding="utf-8"))
    result = TrainingResult.model_validate_json(
        training_result_path.read_text(encoding="utf-8"))

    # PLY bytes from the local --ply argument (authoritative for the trained PLY).
    ply_bytes = ply.read_bytes()

    # Config bytes from the result's training_config_yml binding path.
    config_bindings = [
        b for b in result.output_bindings
        if b.artifact_kind == "training_config_yml"
    ]
    if len(config_bindings) != 1:
        print(f"[TRAINING-PROVENANCE-FAIL] expected exactly 1 "
              f"training_config_yml binding; got {len(config_bindings)}",
              file=sys.stderr)
        return False, None, None
    config_bytes = _read_binding_path_or_fail(
        config_bindings[0].artifact_path, "training_config_yml")

    # Log bytes from the result's training_log binding path.
    log_bindings = [
        b for b in result.output_bindings
        if b.artifact_kind == "training_log"
    ]
    if len(log_bindings) != 1:
        print(f"[TRAINING-PROVENANCE-FAIL] expected exactly 1 "
              f"training_log binding; got {len(log_bindings)}",
              file=sys.stderr)
        return False, None, None
    log_bytes = _read_binding_path_or_fail(
        log_bindings[0].artifact_path, "training_log")

    # Input bytes from the request's input_bindings paths.
    input_bytes_by_path: dict[str, bytes] = {}
    for binding in request.input_bindings:
        input_bytes_by_path[binding.artifact_path] = _read_binding_path_or_fail(
            binding.artifact_path, f"input[{binding.artifact_kind}]")

    try:
        validate_training_provenance(
            result, request,
            actual_ply_bytes=ply_bytes,
            actual_config_bytes=config_bytes,
            actual_log_bytes=log_bytes,
            input_bytes_by_path=input_bytes_by_path,
        )
    except ValueError as exc:
        print(f"[TRAINING-PROVENANCE-FAIL] {exc}", file=sys.stderr)
        return False, None, None

    # Derive trust honestly: without registration quality, is_trustworthy=False.
    trust = derive_training_trust(
        result, request,
        actual_ply_bytes=ply_bytes,
        actual_config_bytes=config_bytes,
        actual_log_bytes=log_bytes,
        input_bytes_by_path=input_bytes_by_path,
        registration_quality_passed=False,
    )
    if not trust.content_closed:
        print(f"[TRAINING-PROVENANCE-FAIL] content closure not verified "
              f"(training_status.state={result.training_status.state})",
              file=sys.stderr)
        return False, None, None

    result_sha = result_canonical_sha256(result)
    print(f"[TRAINING-PROVENANCE-OK] content closure verified, "
          f"result_sha={result_sha[:12]}... "
          f"(content-only; registration quality NOT checked — "
          f"is_trustworthy={trust.is_trustworthy})")
    return True, result_sha, trust


def _validate_registration_quality(
    registration_quality_report_path: Path,
    registration_json_path: Path,
    registration_quality_policy_path: Path,
    capture_manifest_path: Path | None,
    sparse_model_dir_path: Path | None,
) -> tuple[bool, bool, str | None]:
    """验证 registration quality report。

    返回 (validated, quality_accepted, report_sha)。
    - validated=True: report 通过 validate_registration_quality。
      quality_accepted 是 report.quality_accepted（caller 用它作为
      registration_quality_passed）。
    - validated=False: 验证失败，调用者应 fail-closed。
    """
    from pipeline.registration_quality import (
        RegistrationQualityPolicy,
        RegistrationQualityReport,
        validate_registration_quality,
    )

    report = RegistrationQualityReport.model_validate_json(
        registration_quality_report_path.read_text(encoding="utf-8"))
    registration_json_bytes = registration_json_path.read_bytes()
    policy = RegistrationQualityPolicy.model_validate_json(
        registration_quality_policy_path.read_text(encoding="utf-8"))

    capture_manifest_bytes: bytes | None = None
    if capture_manifest_path is not None:
        capture_manifest_bytes = capture_manifest_path.read_bytes()

    sparse_enumeration: SparseModelEnumeration | None = None
    if sparse_model_dir_path is not None:
        sparse_enumeration = _load_sparse_enumeration(sparse_model_dir_path)

    try:
        validate_registration_quality(
            report, policy, registration_json_bytes,
            capture_manifest_bytes=capture_manifest_bytes,
            sparse_enumeration=sparse_enumeration,
        )
    except ValueError as exc:
        print(f"[REGISTRATION-QUALITY-FAIL] {exc}", file=sys.stderr)
        return False, False, None

    report_sha = hashlib.sha256(
        registration_quality_report_path.read_bytes()).hexdigest()
    print(f"[REGISTRATION-QUALITY-OK] report verified, "
          f"quality_accepted={report.quality_accepted}, "
          f"training_allowed={report.training_allowed}")
    return True, report.quality_accepted, report_sha


def _load_sparse_enumeration(sparse_dir: Path) -> SparseModelEnumeration | None:
    """Load a SparseModelEnumeration from a COLMAP sparse directory.

    Reads images.txt / cameras.txt / points3D.txt (text format) and builds
    the enumeration.  Returns None if the directory is empty.
    """
    from pipeline.registration_quality import SparseModelEnumeration
    # The sparse enumeration is typically built by the registration quality
    # builder; here we only need to re-load it for validation.  In practice
    # the caller should pass a pre-built enumeration JSON.  For now we read
    # the COLMAP text files and build a minimal enumeration.
    #
    # NOTE: This is a simplified loader.  The full builder lives in
    # pipeline/registration_quality.py build_registration_quality_report.
    # prepare_import only needs to pass the enumeration to the validator,
    # so we expect the caller to provide a serialized enumeration JSON next
    # to the sparse dir, or we skip sparse verification.
    enum_json = sparse_dir / "sparse_enumeration.json"
    if enum_json.is_file():
        return SparseModelEnumeration.model_validate_json(
            enum_json.read_text(encoding="utf-8"))
    # If no pre-built enumeration JSON exists, we cannot verify sparse model
    # consistency for colmap engine — fail-closed.
    raise FileNotFoundError(
        f"sparse_enumeration.json not found in {sparse_dir} — cannot verify "
        f"COLMAP sparse model enumeration; build it via "
        f"pipeline.registration_quality.build_registration_quality_report")


def _check_consistency(ply: Path, sparse: Path) -> bool:
    """几何一致性检查。返回 False 表示应 fail-closed 中止。

    三态如实转述，**绝不**把"没发现矛盾"包装成"已验证"：
    contradicted -> 拒绝；unknown -> 放行但明说什么都没检出；
    not-contradicted -> 放行并明说这不是证明。
    """
    from pipeline.splat_provenance import Verdict, check_splat_against_sparse

    result = check_splat_against_sparse(ply, sparse)
    if result.verdict is Verdict.CONTRADICTED:
        print(f"[FAIL-CLOSED] {result.summary()}", file=sys.stderr)
        print("拒绝生成契约: 这个 ply 与你声称的 COLMAP workspace 几何对不上。",
              file=sys.stderr)
        return False
    if result.verdict is Verdict.UNKNOWN:
        print(f"[UNKNOWN] {result.summary()}")
        print("  -> 没做成检查, 因此对这个 ply 的来源**没有任何结论** (不是通过)。")
        return True
    print(f"[未发现矛盾] {result.summary()}")
    print("  -> 注意: 这**不是**通过。几何一致只能证伪、不能证实来源; "
          "它不代表这个 ply 真是用该 workspace 训练的。")
    return True


# ============================================================
# CLI
# ============================================================

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="生成 sfm-local 导入契约 (registration.json + splat-input.json)")
    ap.add_argument("ply", type=Path, help="训练好的 3DGS .ply")
    ap.add_argument("--out-dir", type=Path, default=Path("recon"),
                    help="契约输出目录 (默认 recon/)")
    ap.add_argument("--session-id", default="external_3dgs",
                    help="会话 id (registration 与 splat 必须一致; 默认 external_3dgs)")
    ap.add_argument("--synthetic", action="store_true",
                    help="来源为合成影像时如实申报 (provenance=SYNTHETIC -> "
                         "synthetic=true + preview-proxy, 只降不升)")
    ap.add_argument("--colmap-sparse", type=Path, default=None,
                    help="声称本 ply 训练自某 COLMAP workspace 时, 传其 "
                         "sparse/0/points3D.txt 做几何一致性检查 (对不上则 fail-closed; "
                         "只能证伪不能证实; 不适用于云端 nerfstudio 重跑 COLMAP 的路线)")

    # Training provenance handshake (P0.3 hardened).
    ap.add_argument("--training-result", type=Path, default=None,
                    help="云 GPU 训练 provenance result manifest (training-result.json); "
                         "需与 --training-request 配对, 验证内容闭包")
    ap.add_argument("--training-request", type=Path, default=None,
                    help="云 GPU 训练 provenance request manifest (training-request.json); "
                         "需与 --training-result 配对")
    ap.add_argument("--registration-quality-report", type=Path, default=None,
                    help="RegistrationQualityReport JSON; 需与 --registration-json + "
                         "--registration-quality-policy + training pair 配对, "
                         "验证后 is_trustworthy=True 才追加 trusted prefix")
    ap.add_argument("--registration-json", type=Path, default=None,
                    help="registration.json (RegistrationResult) 用于验证 quality report")
    ap.add_argument("--registration-quality-policy", type=Path, default=None,
                    help="RegistrationQualityPolicy JSON 用于验证 quality report")
    ap.add_argument("--capture-manifest", type=Path, default=None,
                    help="可选 CaptureRevisionManifest JSON 用于 colmap engine 验证")
    ap.add_argument("--sparse-model-dir", type=Path, default=None,
                    help="可选 COLMAP sparse 目录 (含 sparse_enumeration.json) "
                         "用于 colmap engine 验证")
    ap.add_argument("--allow-unverified-training", action="store_true",
                    help="跳过 training provenance 验证失败时的 fail-closed (仅开发用; "
                         "不产生 training_provenance evidence)")

    args = ap.parse_args(argv)
    if not args.ply.is_file():
        raise SystemExit(f"文件不存在: {args.ply}")

    # ---- Argument symmetry (P0.3: paired args must appear together) ----
    training_pair_given = (
        args.training_result is not None or args.training_request is not None)
    if args.training_result is not None and args.training_request is None:
        raise SystemExit("--training-result 需要与 --training-request 配对使用")
    if args.training_request is not None and args.training_result is None:
        raise SystemExit("--training-request 需要与 --training-result 配对使用")

    reg_quality_given = any(v is not None for v in (
        args.registration_quality_report, args.registration_json,
        args.registration_quality_policy))
    if reg_quality_given:
        # All three registration-quality args must appear together.
        if not all([args.registration_quality_report, args.registration_json,
                    args.registration_quality_policy]):
            raise SystemExit(
                "--registration-quality-report / --registration-json / "
                "--registration-quality-policy 必须同时出现")
        # Registration quality requires the training pair.
        if not training_pair_given:
            raise SystemExit(
                "--registration-quality-report 需要 --training-result + "
                "--training-request 配对 (trusted prefix 需要三者)")

    # ---- Training provenance + registration quality handshake ----
    extra_evidence: tuple[str, ...] = ()
    if training_pair_given:
        if not args.training_result.is_file():
            raise SystemExit(f"文件不存在: {args.training_result}")
        if not args.training_request.is_file():
            raise SystemExit(f"文件不存在: {args.training_request}")
        content_closed, result_sha, trust = _validate_training_provenance(
            args.ply, args.training_result, args.training_request)
        if not content_closed:
            if not args.allow_unverified_training:
                print("[FAIL-CLOSED] training provenance 验证失败; "
                      "加 --allow-unverified-training 可跳过 (仅开发用, 不产生 evidence)",
                      file=sys.stderr)
                return 1
            print("[WARN] --allow-unverified-training: 跳过 training provenance, "
                  "不追加 evidence", file=sys.stderr)
        elif result_sha:
            if reg_quality_given:
                # Verify registration quality and derive trust honestly.
                rq_ok, quality_accepted, _rq_sha = _validate_registration_quality(
                    args.registration_quality_report,
                    args.registration_json,
                    args.registration_quality_policy,
                    args.capture_manifest,
                    args.sparse_model_dir)
                if not rq_ok:
                    if not args.allow_unverified_training:
                        print("[FAIL-CLOSED] registration quality 验证失败; "
                              "加 --allow-unverified-training 可跳过 (仅开发用, "
                              "不产生 evidence)", file=sys.stderr)
                        return 1
                    print("[WARN] --allow-unverified-training: 跳过 registration "
                          "quality, 追加弱 receipt", file=sys.stderr)
                    extra_evidence = (
                        f"training_content_closed.v1={result_sha}",)
                elif quality_accepted and trust is not None and \
                        trust.content_closed and trust.trainer_identified:
                    # Trusted prefix: content closed + registration quality
                    # accepted + trainer identified (no drift).  All other
                    # trust booleans derive from content_closed so they're
                    # already True here.
                    extra_evidence = (
                        f"training_provenance.v1={result_sha}",)
                    print(f"[TRUSTED] training_provenance.v1={result_sha[:12]}... "
                          f"(content closed + registration quality accepted + "
                          f"trainer identified — still NOT metric/aligned/real-photos)")
                else:
                    # Registration quality verified but not accepted, or trainer
                    # drifted -> content-only receipt, not trusted.
                    extra_evidence = (
                        f"training_content_closed.v1={result_sha}",)
                    print(f"[CONTENT-ONLY] training_content_closed.v1="
                          f"{result_sha[:12]}... (registration quality "
                          f"accepted={quality_accepted}, trainer_identified="
                          f"{trust.trainer_identified if trust else 'unknown'} — "
                          f"NOT trusted)")
            else:
                # No registration quality -> content-only receipt.
                extra_evidence = (
                    f"training_content_closed.v1={result_sha}",)
                print(f"[CONTENT-ONLY] training_content_closed.v1="
                      f"{result_sha[:12]}... (registration quality NOT checked — "
                      f"NOT trusted)")

    if args.colmap_sparse is not None and not _check_consistency(
        args.ply, args.colmap_sparse
    ):
        return 1
    reg_path, splat_path = prepare(args.ply, args.out_dir, args.session_id,
                                   synthetic=args.synthetic,
                                   extra_evidence=extra_evidence)
    print(f"[OK] 已生成:\n  {reg_path}\n  {splat_path}\n")
    print("下一步导入 (非米制 frame 必须 --dedup-voxel 0):")
    print(f"  python -m pipeline.reconstruct --engine import "
          f"--registration {reg_path} --splat {splat_path} "
          f"--dedup-voxel 0 --replace-margin 0 --photos photos")
    print("然后:  python make.py serve   # http://127.0.0.1:8000/web/studio/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
