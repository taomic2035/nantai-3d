"""SfM-arbitrary -> ENU-metric Sim3 alignment (the measured 3DGS path).

An SfM reconstruction (e.g. COLMAP) lives in an arbitrary, non-metric frame.
This module fits a similarity transform (Umeyama, 1991) from >=3 control points
-- surveyed ENU points or GPS anchors -- to promote a registration into the
metric ENU world the schema already defines, **without ever silently promoting
arbitrary geometry to metres**.

Every gate is fail-closed.  If there are fewer than three finite control points,
if the source configuration is degenerate (collinear/coplanar), if the fitted
scale is non-positive, or if the RMS residual exceeds ``max_rms_m``, no world
frame is produced: the registration stays sfm-local / UNALIGNED.  A proper
rotation (det=+1) is forced, so a reflection is never emitted as a rotation.

The fit is recorded as ``Sim3AlignmentEvidence`` and serialised onto both the
``FrameTransform.evidence`` and the measured world frame's ``evidence`` via the
``sim3.alignment.v1=<json>`` convention, so downstream audit code can re-derive
the residuals and see the gate outcome.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from pipeline.recon_schema import (
    AlignmentStatus,
    AxisConvention,
    CameraPose,
    ControlPoint,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    FrameTransform,
    GeoAlignment,
    GeoAnchor,
    Handedness,
    MetricStatus,
    RegistrationResult,
    Sim3,
    Sim3AlignmentEvidence,
    TransformMethod,
    gps_to_enu,
)

# Absolute floor (metres) on the smallest source singular value, so a config
# that is coplanar/collinear to numerical precision is rejected even when the
# relative ``min_span_ratio`` floor would round to zero.
_ABS_SPAN_FLOOR_M = 1e-6


class AlignmentError(ValueError):
    """Raised when a Sim3 alignment gate fails; no world frame is emitted."""


def umeyama_sim3(
    src: np.ndarray, dst: np.ndarray, with_scale: bool = True
) -> tuple[float, np.ndarray, np.ndarray]:
    """Closed-form least-squares similarity fit ``dst ~= scale * R @ src + t``.

    Returns ``(scale, R, t)`` where ``R`` is a proper rotation (det=+1).  A
    reflection is prevented by flipping the sign of the last singular direction
    when ``det(U) * det(Vt) < 0`` -- the standard Umeyama reflection guard.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise AlignmentError("umeyama_sim3 requires matching (N, 3) point arrays")
    n = src.shape[0]
    mu_s = src.mean(axis=0)
    mu_d = dst.mean(axis=0)
    src_c = src - mu_s
    dst_c = dst - mu_d
    cov = (dst_c.T @ src_c) / n
    u, d, vt = np.linalg.svd(cov)
    s = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        s[2, 2] = -1.0  # reflection guard: force a proper rotation
    rotation = u @ s @ vt
    if with_scale:
        var_src = (src_c ** 2).sum() / n
        if var_src <= 0:
            raise AlignmentError("source points are coincident; scale is undefined")
        scale = float(np.trace(np.diag(d) @ s) / var_src)
    else:
        scale = 1.0
    translation = mu_d - scale * (rotation @ mu_s)
    return scale, rotation, translation


def _residuals(
    src: np.ndarray, dst: np.ndarray, scale: float, rotation: np.ndarray, t: np.ndarray
) -> tuple[np.ndarray, float, float]:
    """Per-point Euclidean residuals plus their RMS and max, in metres."""
    predicted = scale * (src @ rotation.T) + t
    per_point = np.linalg.norm(dst - predicted, axis=1)
    rms = float(np.sqrt((per_point ** 2).mean()))
    max_residual = float(per_point.max())
    return per_point, rms, max_residual


def _source_span(src: np.ndarray) -> np.ndarray:
    """Singular values (descending) of the centred source points."""
    centred = src - src.mean(axis=0)
    return np.linalg.svd(centred, compute_uv=False)


def build_control_points(
    reg: RegistrationResult,
    control_points: list[ControlPoint],
    origin: GeoAnchor | None,
) -> list[tuple[np.ndarray, np.ndarray, str]]:
    """Resolve ``ControlPoint`` specs into ``(sfm_xyz, enu_xyz, label)`` triples.

    ``image`` sources resolve to that pose's camera centre (``CameraPose.t_xyz``)
    in ``reg.pose_frame``; ``geo`` targets reduce through ``gps_to_enu`` against
    ``origin``.  Anything unresolved fails closed.
    """
    poses_by_image: dict[str, CameraPose] = {p.image: p for p in reg.poses}
    resolved: list[tuple[np.ndarray, np.ndarray, str]] = []
    for cp in control_points:
        if cp.source_xyz is not None:
            sfm = np.asarray(cp.source_xyz, dtype=np.float64)
        else:
            pose = poses_by_image.get(cp.image)
            if pose is None:
                raise AlignmentError(
                    f"control point {cp.label!r} references unknown image {cp.image!r}"
                )
            sfm = np.asarray(pose.t_xyz, dtype=np.float64)
        if cp.enu_xyz is not None:
            enu = np.asarray(cp.enu_xyz, dtype=np.float64)
        else:
            if origin is None:
                raise AlignmentError(
                    f"control point {cp.label!r} uses a GPS anchor but no geo origin"
                    " is available"
                )
            enu = np.asarray(gps_to_enu(cp.geo, origin), dtype=np.float64)
        if not (np.all(np.isfinite(sfm)) and np.all(np.isfinite(enu))):
            raise AlignmentError(
                f"control point {cp.label!r} resolved to non-finite coordinates"
            )
        resolved.append((sfm, enu, cp.label))
    return resolved


def control_points_from_geo_anchors(
    reg: RegistrationResult,
    image_anchors: dict[str, GeoAnchor],
) -> list[ControlPoint]:
    """把逐图 geo 锚点 (通常自 EXIF GPS 派生) 配对成对齐控制点, 直接喂 align_registration。

    让 GPS 标记的采集免手工逐图写 ControlPoint 即可 turnkey 米制对齐: 每张【既注册
    (在 ``reg.poses`` 有位姿) 又有锚点】的图 → 一个 ``ControlPoint(image=..., geo=...)``,
    source 侧解析为该位姿的相机中心, target 侧经 ``gps_to_enu`` 归约。未注册或无锚点的
    图静默排除 (无对应即无证据)。按 image 排序, 输出确定。

    本函数只【组装证据】, 绝不提升信任: 拟合门 (>=3 点、退化守卫、RMS 阈值) 仍由
    ``fit_sfm_to_enu`` / ``align_registration`` 权威裁决, 证据不足/不一致照样 fail-closed。
    ``GpsObservation`` (ingest EXIF) 可平凡转 ``GeoAnchor(lat, lon, alt=altitude_m or 0.0)``。
    """
    registered = {pose.image for pose in reg.poses}
    return [
        ControlPoint(label=image, image=image, geo=image_anchors[image])
        for image in sorted(image_anchors)
        if image in registered
    ]


def fit_sfm_to_enu(
    control_points: list[tuple[np.ndarray, np.ndarray, str]],
    geo_origin: GeoAnchor,
    *,
    max_rms_m: float = 2.0,
    min_span_ratio: float = 1e-3,
) -> tuple[Sim3, Sim3AlignmentEvidence]:
    """Fit a gated Sim3 from resolved control points; fail closed on any gate.

    Gates (all fail-closed): >=3 finite control points, non-degenerate source
    span, fitted ``scale > 0``, and ``rms_residual <= max_rms_m``.  Returns the
    ``(Sim3, Sim3AlignmentEvidence)`` only when every gate passes; otherwise
    raises ``AlignmentError`` and emits nothing.
    """
    if len(control_points) < 3:
        raise AlignmentError(
            f"need >=3 control points to fit a Sim3, got {len(control_points)}"
        )
    src = np.array([cp[0] for cp in control_points], dtype=np.float64)
    dst = np.array([cp[1] for cp in control_points], dtype=np.float64)
    labels = tuple(cp[2] for cp in control_points)
    if not (np.all(np.isfinite(src)) and np.all(np.isfinite(dst))):
        raise AlignmentError("non-finite control points")

    singular_values = _source_span(src)
    span_floor = max(min_span_ratio * float(singular_values[0]), _ABS_SPAN_FLOOR_M)
    if singular_values[0] <= 0 or float(singular_values[2]) < span_floor:
        raise AlignmentError(
            "degenerate control-point span (collinear/coplanar): "
            f"singular values {singular_values.tolist()} below floor {span_floor:g}"
        )

    scale, rotation, translation = umeyama_sim3(src, dst)
    if not np.isfinite(scale) or scale <= 0:
        raise AlignmentError(f"non-positive or non-finite scale: {scale}")

    per_point, rms, max_residual = _residuals(src, dst, scale, rotation, translation)
    passed = rms <= max_rms_m

    # Sim3 itself re-validates orthogonality and rejects reflections; building it
    # here means a bad rotation fails closed before any evidence is trusted.
    sim3 = Sim3(
        scale=scale,
        rotation_matrix_xyz=tuple(tuple(row) for row in rotation.tolist()),
        t_xyz=tuple(translation.tolist()),
    )
    evidence = Sim3AlignmentEvidence(
        method="umeyama-sim3",
        n_control_points=len(control_points),
        scale=scale,
        rms_residual_m=rms,
        max_residual_m=max_residual,
        per_point_residual_m=tuple(per_point.tolist()),
        source_singular_values=tuple(float(v) for v in singular_values.tolist()),
        min_span_ratio=min_span_ratio,
        max_rms_threshold_m=max_rms_m,
        geo_origin={
            "lat": geo_origin.lat,
            "lon": geo_origin.lon,
            "alt": geo_origin.alt,
        },
        control_point_labels=labels,
        passed=passed,
    )
    if not passed:
        raise AlignmentError(
            f"rms_residual {rms:.3f}m exceeds max_rms {max_rms_m}m; "
            "refusing to emit an aligned world frame"
        )
    return sim3, evidence


def align_registration(
    reg: RegistrationResult,
    control_points: list[ControlPoint],
    *,
    geo_origin: GeoAnchor | None = None,
    world_frame_id: str = "world-enu",
    max_rms_m: float = 2.0,
    min_span_ratio: float = 1e-3,
    method: TransformMethod | None = None,
    allow_unaligned_fallback: bool = False,
) -> RegistrationResult:
    """Return an ALIGNED copy of ``reg`` in ``world-enu``, or fail closed.

    On success the returned registration carries a measured ``world-enu``
    ``world_frame``, a ``pose_to_world`` ``FrameTransform`` (both bearing the
    ``sim3.alignment.v1`` evidence), and ``alignment_status=ALIGNED``.  On any
    gate failure it raises ``AlignmentError`` -- or, when
    ``allow_unaligned_fallback`` is True, returns ``reg`` **unchanged** (still
    sfm-local / UNALIGNED, never partially mutated).  Arbitrary geometry is never
    silently promoted to metres.
    """
    origin = geo_origin if geo_origin is not None else reg.geo_origin
    if origin is None:
        raise AlignmentError(
            "a geo origin is required to define the world-enu frame "
            "(pass geo_origin or set reg.geo_origin)"
        )
    try:
        resolved = build_control_points(reg, control_points, origin)
        sim3, evidence = fit_sfm_to_enu(
            resolved,
            origin,
            max_rms_m=max_rms_m,
            min_span_ratio=min_span_ratio,
        )
    except AlignmentError:
        if allow_unaligned_fallback:
            return reg  # unchanged: still sfm-local / UNALIGNED (atomic)
        raise

    evidence_str = evidence.to_evidence()
    world_frame = CoordinateFrame(
        frame_id=world_frame_id,
        handedness=Handedness.RIGHT,
        axes=AxisConvention.ENU_Z_UP,
        units=CoordinateUnits.METERS,
        metric_status=MetricStatus.METRIC,
        geo_aligned=GeoAlignment.ALIGNED,
        provenance=FrameProvenance.MEASURED,
        evidence=(
            "sfm-to-enu-sim3-alignment",
            f"geo-origin:{origin.lat},{origin.lon},{origin.alt}",
            evidence_str,
        ),
    )
    if method is None:
        # GPS-derived targets => GPS_ANCHOR; explicit surveyed ENU => CONTROL_POINTS.
        used_gps = any(cp.geo is not None for cp in control_points)
        method = (
            TransformMethod.GPS_ANCHOR if used_gps else TransformMethod.CONTROL_POINTS
        )
    transform = FrameTransform(
        source_frame=reg.pose_frame.frame_id,
        target_frame=world_frame_id,
        sim3=sim3,
        method=method,
        evidence=(evidence_str,),
    )
    return reg.model_copy(
        update={
            "world_frame": world_frame,
            "pose_to_world": transform,
            "alignment_status": AlignmentStatus.ALIGNED,
        }
    )


def load_control_points_json(path: str | Path) -> list[ControlPoint]:
    """Load a JSON array of control-point specs into validated ``ControlPoint``s."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise AlignmentError("control-points JSON must be a list of objects")
    return [ControlPoint.model_validate(item) for item in raw]


def load_control_points_from_ingest_gps(
    manifest_path: str | Path,
    reg: RegistrationResult,
) -> list[ControlPoint]:
    """Turnkey GPS 对齐: 从 ingest manifest 的逐图 EXIF GPS 构造对齐控制点。

    只有【既注册 (在 ``reg.poses``) 又有 GPS】的图成为控制点 (照片带 GPS; 视频帧无
    EXIF GPS 故天然排除)。图名以 manifest 的 ``output_path`` 匹配 registration 的
    ``pose.image``; 不匹配者静默排除 (无对应即无证据)。控制点 <3 时 align_registration
    的门会 fail-closed 并给出清晰错误。``GpsObservation`` 无高度时 alt 记 0。
    """
    from pipeline.ingest_manifest import IngestManifest

    manifest = IngestManifest.model_validate_json(
        Path(manifest_path).read_text(encoding="utf-8"))
    anchors: dict[str, GeoAnchor] = {}
    for src in manifest.sources:
        if src.gps is None:
            continue
        anchor = GeoAnchor(lat=src.gps.lat, lon=src.gps.lon,
                           alt=src.gps.altitude_m if src.gps.altitude_m is not None else 0.0)
        for out in src.outputs:
            anchors[str(out.output_path)] = anchor
    control_points = control_points_from_geo_anchors(reg, anchors)
    if not control_points:
        raise AlignmentError(
            "--from-gps 未找到【既注册又带 EXIF GPS】的图: 确认照片含 GPS 且已被配准, "
            "或改用 --control-points 手工提供")
    return control_points


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fit an SfM->ENU Sim3 and write an aligned registration.json"
    )
    parser.add_argument("--registration", required=True,
                        help="path to a RegistrationResult JSON (sfm-local)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--control-points",
                        help="path to a control-point spec JSON list")
    source.add_argument("--from-gps", metavar="INGEST_MANIFEST",
                        help="derive control points from per-image EXIF GPS in an "
                             "ingest manifest (turnkey for GPS-tagged captures); "
                             "pairs each registered image with its GPS anchor")
    parser.add_argument("--max-rms", type=float, default=2.0,
                        help="RMS residual gate in metres (default 2.0)")
    parser.add_argument("--min-span-ratio", type=float, default=1e-3,
                        help="relative degeneracy floor for the source span")
    parser.add_argument("--out", required=True,
                        help="output path for the aligned registration.json")
    parser.add_argument("--geo-origin", default=None, metavar="LAT,LON,ALT",
                        help="ENU tangent origin lat,lon,alt; supplies/overrides "
                             "registration.geo_origin (required if neither has one)")
    args = parser.parse_args(argv)

    reg = RegistrationResult.model_validate_json(
        Path(args.registration).read_text(encoding="utf-8")
    )
    geo_origin = None
    if args.geo_origin:
        try:
            lat, lon, alt = (float(v) for v in args.geo_origin.split(","))
        except ValueError as exc:
            raise AlignmentError("--geo-origin must be LAT,LON,ALT") from exc
        geo_origin = GeoAnchor(lat=lat, lon=lon, alt=alt)
    if args.from_gps:
        control_points = load_control_points_from_ingest_gps(args.from_gps, reg)
    else:
        control_points = load_control_points_json(args.control_points)
    aligned = align_registration(
        reg,
        control_points,
        geo_origin=geo_origin,
        max_rms_m=args.max_rms,
        min_span_ratio=args.min_span_ratio,
    )
    # LF: registration.json is a trust root; keep it byte-reproducible across OSes.
    Path(args.out).write_text(aligned.model_dump_json(indent=2) + "\n",
                              encoding="utf-8", newline="\n")
    print(f"[OK] aligned registration written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
