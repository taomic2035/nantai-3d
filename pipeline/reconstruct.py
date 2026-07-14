"""
端到端重建 CLI: 照片 + 视频 → 统一坐标系 → 高斯泼溅 → 拼接 → LOD 导出

流程:
  1. 配准    registration.register (colmap 联合 SfM 或确定性 mock)
  2. 泼溅    每会话生成 3DGS 场景:
             - engine=mock:   由位姿 + 输入图像调色板合成代理泼溅 (本机无 GPU 可跑通全链路)
             - engine=import: 导入外部训练好的 3DGS ply (云端 gsplat/nerfstudio 训练产物),
                              每个输入必须声明 frame 并携带必要的 FrameTransform
  3. 拼接    GaussianScene.merge + 体素去重 (图/视频/多会话重叠区消融)
  4. 变清晰  --base-scene 提供旧场景时, 新重建 replace_region 覆盖对应区域 (补拍增清)
  5. 导出    LOD 三级 ply (可变清晰) + recon_manifest.json → Web viewer 直接加载

用法:
    # 全 mock 链路 (无 GPU / 无 colmap)
    python -m pipeline.reconstruct --photos photos --engine mock

    # 导入云端训练的 3DGS（JSON 内含完整 source_frame / transform 契约）
    python -m pipeline.reconstruct --engine import \
      --splat trained/dji-splat-input.json

    # 补拍变清晰: 用新重建覆盖旧场景对应区域
    python -m pipeline.reconstruct --engine mock --base-scene recon/scene_full.ply
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
from loguru import logger

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import (
    AlignmentStatus,
    CaptureSession,
    CoordinateFrame,
    CoordinateUnits,
    FrameProvenance,
    FrameTransform,
    GeoAlignment,
    MetricStatus,
    RegistrationResult,
    SplatInput,
)
from pipeline.registration import register

DEFAULT_OUT_DIR = "recon"
DEFAULT_WEB_DIR = "web/data/recon"

FULL_3DGS_BASE_ATTRIBUTES = [
    "x", "y", "z", "nx", "ny", "nz",
    "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
    "scale_0", "scale_1", "scale_2",
    "rot_0", "rot_1", "rot_2", "rot_3",
]
SIMPLE_PREVIEW_ATTRIBUTES = ["x", "y", "z", "r", "g", "b", "scale"]


def _derive_geometry_usability(
    target_frame: CoordinateFrame,
    alignment_status: AlignmentStatus,
    metric_evidence: list[str],
    *,
    synthetic: bool,
    provenance_known: bool = True,
) -> str:
    """Classify geometry only from the coordinate evidence contract.

    Engine labels are deliberately absent from this boundary.  Synthetic
    geometry remains a proxy even if it happens to use metre-shaped numbers;
    incomplete or contradictory alignment facts fail closed to preview-only.
    """
    if synthetic:
        return "preview-proxy"
    if not provenance_known:
        return "preview-only"
    if not (
        target_frame.units is CoordinateUnits.METERS
        and target_frame.metric_status is MetricStatus.METRIC
        and metric_evidence
    ):
        return "preview-only"
    if (
        target_frame.geo_aligned is GeoAlignment.ALIGNED
        and alignment_status is AlignmentStatus.ALIGNED
    ):
        return "metric-aligned"
    if (
        target_frame.geo_aligned is GeoAlignment.UNALIGNED
        and alignment_status is AlignmentStatus.UNALIGNED
    ):
        return "metric-unaligned"
    return "preview-only"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_descriptor(
    path: Path,
    manifest_dir: Path,
    *,
    kind: str,
    fidelity: str,
    attributes: list[str],
    sh_degree: int | None,
) -> dict:
    """Build an integrity-verifiable descriptor relative to its manifest."""
    try:
        relative_path = path.resolve().relative_to(manifest_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"artifact must be inside manifest directory: {path}") from exc
    return {
        "path": relative_path.as_posix(),
        "kind": kind,
        "fidelity": fidelity,
        "sha256": _sha256_file(path),
        "bytes": path.stat().st_size,
        "attributes": attributes,
        "sh_degree": sh_degree,
        # This path is a mutable local build output.  Its digest detects stale
        # or replaced bytes, but only a later freeze/export step may call it
        # immutable.
        "immutable": False,
    }


def _full_3dgs_attributes(scene: GaussianScene) -> list[str]:
    rest = [f"f_rest_{index}" for index in range(scene.sh_rest.shape[1])]
    return [
        *FULL_3DGS_BASE_ATTRIBUTES[:9],
        *rest,
        *FULL_3DGS_BASE_ATTRIBUTES[9:],
        *scene.extra_properties,
    ]


# ============ mock 泼溅合成 ============
def _sample_palette(photos_dir: Path, images: list[str],
                    max_samples: int = 5) -> np.ndarray:
    """从输入图像抽取调色板 (Nx3, [0,1]) — mock 泼溅颜色与真实素材相关"""
    colors = []
    step = max(1, len(images) // max_samples)
    for img in images[::step][:max_samples]:
        p = photos_dir / img
        try:
            from PIL import Image
            with Image.open(p) as im:
                small = np.asarray(im.convert("RGB").resize((8, 8))) / 255.0
            colors.append(small.reshape(-1, 3))
        except Exception:
            continue
    if not colors:
        return np.array([[0.5, 0.55, 0.4]])
    return np.concatenate(colors)


def synth_session_splat(session: CaptureSession, reg: RegistrationResult,
                        photos_dir: Path) -> GaussianScene:
    """由配准位姿 + 图像调色板合成一个会话的代理泼溅场景

    覆盖度与输入量挂钩: 帧越多 (视频多角度) → 高斯越多、越完整,
    模拟"多角度采集提升重建完整度"的真实行为。
    """
    poses = reg.poses_by_session(session.session_id)
    if not poses:
        return GaussianScene(
            np.zeros((0, 3)), np.zeros((0, 3)),
            frame_id=reg.target_frame.frame_id,
            units=reg.target_frame.units.value,
        )

    cam_pos = np.array([p.t_xyz for p in poses])
    center = cam_pos.mean(axis=0)
    center[2] = 0.0  # 场景中心落地
    radius = float(np.median(np.linalg.norm(cam_pos[:, :2] - center[:2], axis=1)))
    radius = max(radius, 5.0)

    import hashlib
    seed = int(hashlib.sha1(session.session_id.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    palette = _sample_palette(photos_dir, session.images)

    n_imgs = len(session.images)
    n_ground = min(1500 + 120 * n_imgs, 12000)
    n_struct = min(800 + 60 * n_imgs, 6000)
    n_scatter = min(400 + 30 * n_imgs, 3000)

    def pick_colors(n, jitter=0.05):
        idx = rng.integers(0, len(palette), n)
        return np.clip(palette[idx] + rng.normal(0, jitter, (n, 3)), 0, 1)

    # 地面盘
    ang = rng.uniform(0, 2 * np.pi, n_ground)
    rad = radius * np.sqrt(rng.uniform(0, 1, n_ground))
    ground = np.stack([center[0] + rad * np.cos(ang),
                       center[1] + rad * np.sin(ang),
                       rng.uniform(0, 0.3, n_ground)], axis=1)

    # 中央结构 (盒状聚簇)
    w, d, h = 8.0, 6.0, 5.0
    struct = np.stack([
        center[0] + rng.uniform(-w / 2, w / 2, n_struct),
        center[1] + rng.uniform(-d / 2, d / 2, n_struct),
        rng.uniform(0, h, n_struct)], axis=1)

    # 周边散布簇
    n_clusters = max(2, n_imgs // 10)
    scatter_list = []
    for _ in range(n_clusters):
        c_ang = rng.uniform(0, 2 * np.pi)
        c_rad = rng.uniform(radius * 0.3, radius * 0.9)
        c = center + np.array([c_rad * np.cos(c_ang), c_rad * np.sin(c_ang), 0])
        k = n_scatter // n_clusters
        scatter_list.append(c + rng.normal(0, 1.5, (k, 3)) * [1, 1, 0.8]
                            + [0, 0, 1.5])
    scatter = np.concatenate(scatter_list) if scatter_list else np.zeros((0, 3))
    if len(scatter):
        scatter[:, 2] = np.abs(scatter[:, 2])

    xyz = np.concatenate([ground, struct, scatter])
    n = len(xyz)
    rgb = np.concatenate([pick_colors(n_ground),
                          pick_colors(n_struct),
                          pick_colors(len(scatter))])
    opacity = rng.uniform(0.55, 1.0, n)
    scale = np.exp(rng.normal(np.log(0.12), 0.4, (n, 3)))
    scene = GaussianScene(
        xyz, rgb, opacity, scale,
        frame_id=reg.pose_frame.frame_id,
        units=reg.pose_frame.units.value,
    )
    if reg.pose_to_world is not None:
        scene.apply_frame_transform(
            reg.pose_to_world,
            target_units=reg.target_frame.units.value,
        )
    return scene


# ============ import splats ============
def _apply_splat_transform(
    scene: GaussianScene,
    item: SplatInput,
    target_frame: CoordinateFrame,
) -> GaussianScene:
    """Validate and apply one explicit import-frame contract.

    A PLY without embedded Nantai metadata may be bound to ``item.source_frame``
    because the caller explicitly supplied that full declaration.  Geometry is
    transformed only through ``GaussianScene.apply_frame_transform``, whose
    content-derived history enforces exactly-once semantics atomically.
    """
    source_frame = item.source_frame
    if (
        item.transform is not None
        and item.transform.transform_id in scene.applied_transform_ids
    ):
        raise ValueError(
            f"transform already applied: {item.transform.transform_id}"
        )
    if scene.frame_id is None:
        scene.frame_id = source_frame.frame_id
    elif scene.frame_id != source_frame.frame_id:
        raise ValueError(
            f"embedded PLY frame {scene.frame_id!r} conflicts with SplatInput "
            f"source frame {source_frame.frame_id!r}"
        )

    if scene.units in (None, "unknown"):
        scene.units = source_frame.units.value
    elif scene.units != source_frame.units.value:
        raise ValueError(
            f"embedded PLY units {scene.units!r} conflict with SplatInput "
            f"source units {source_frame.units.value!r}"
        )

    transform = item.transform
    if transform is None:
        if item.uses_legacy_frame_id:
            raise ValueError(
                "legacy frame_id-only SplatInput cannot prove a no-op coordinate "
                "contract and requires a FrameTransform; pass a complete source_frame "
                "or explicit FrameTransform"
            )
        source_contract = (
            source_frame.frame_id,
            source_frame.handedness,
            source_frame.axes,
            source_frame.units,
            source_frame.metric_status,
            source_frame.geo_aligned,
        )
        target_contract = (
            target_frame.frame_id,
            target_frame.handedness,
            target_frame.axes,
            target_frame.units,
            target_frame.metric_status,
            target_frame.geo_aligned,
        )
        if source_contract != target_contract:
            raise ValueError(
                f"splat source coordinate contract {source_frame.frame_id!r}/"
                f"{source_frame.units.value!r} requires a FrameTransform to target "
                f"{target_frame.frame_id!r}/{target_frame.units.value!r}"
            )
        return scene

    if transform.target_frame != target_frame.frame_id:
        raise ValueError(
            f"transform target_frame {transform.target_frame!r} does not match "
            f"registration target {target_frame.frame_id!r}"
        )
    # Do not duplicate this logic with an untracked ``scene.transform`` call.
    scene.apply_frame_transform(
        transform,
        target_units=target_frame.units.value,
    )
    if scene.frame_id != target_frame.frame_id:
        raise RuntimeError("frame transform completed without reaching target frame")
    return scene


def import_session_splats(
    splat_inputs: list[SplatInput],
    reg: RegistrationResult,
) -> list[GaussianScene]:
    """Import external 3DGS PLY files through explicit frame declarations."""
    if not isinstance(splat_inputs, list) or any(
        not isinstance(item, SplatInput) for item in splat_inputs
    ):
        raise TypeError("splat imports must be a list of SplatInput objects")

    scenes = []
    known_sessions = {session.session_id for session in reg.sessions}
    for item in splat_inputs:
        if item.session_id not in known_sessions:
            raise ValueError(f"SplatInput references unknown session: {item.session_id}")
        scene = GaussianScene.load_ply(item.path, require_3dgs=True)
        _apply_splat_transform(scene, item, reg.target_frame)
        logger.info(
            f"导入泼溅: {item.session_id} ← {item.path} "
            f"({len(scene)} 高斯, frame={scene.frame_id})"
        )
        scenes.append(scene)
    return scenes


def _validate_spatial_parameter(
    name: str,
    value: float,
    target_frame: CoordinateFrame,
    *,
    active: bool = True,
) -> float:
    """Validate a distance in the registration target frame.

    Positive tuning values in this CLI are calibrated in metres.  A zero value
    is dimensionless (disable/no margin) and is the only safe value for an
    arbitrary or unknown scale until a metric FrameTransform is supplied.
    """
    value = float(value)
    if not np.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    if (
        active
        and value > 0
        and (
            target_frame.units is not CoordinateUnits.METERS
            or target_frame.metric_status is not MetricStatus.METRIC
        )
    ):
        raise ValueError(
            f"{name}={value} is calibrated in meters and cannot be applied to "
            f"non-metric target frame {target_frame.frame_id!r}/"
            f"{target_frame.units.value!r}; set {name}=0 or align scale first"
        )
    return value


def _transform_catalog(
    reg: RegistrationResult,
    splat_map: list[SplatInput] | None,
) -> tuple[dict[str, FrameTransform], dict[str, list[str]]]:
    """Index declared transform definitions separately from their evidence."""
    declared = list(reg.transform_chain)
    declared.extend(
        item.transform
        for item in (splat_map or [])
        if item.transform is not None
    )
    definitions: dict[str, FrameTransform] = {}
    evidence: dict[str, list[str]] = {}
    for transform in declared:
        transform_id = transform.transform_id
        definitions.setdefault(transform_id, transform)
        evidence.setdefault(transform_id, [])
        evidence[transform_id].extend(transform.evidence)
        evidence[transform_id] = list(dict.fromkeys(evidence[transform_id]))
    return definitions, evidence


def _validate_scene_history(
    scene: GaussianScene,
    definitions: dict[str, FrameTransform],
    *,
    label: str,
    require_composable: bool = True,
) -> list[str]:
    transform_ids = list(scene.applied_transform_ids)
    if len(transform_ids) != len(set(transform_ids)):
        raise ValueError(f"{label} transform history contains duplicate ids")
    missing = [transform_id for transform_id in transform_ids if transform_id not in definitions]
    if missing:
        raise ValueError(
            f"{label} transform history has no auditable transform definition: {missing}"
        )
    if require_composable and transform_ids:
        current_frame = scene.frame_id
        for transform_id in reversed(transform_ids):
            transform = definitions[transform_id]
            if transform.target_frame != current_frame:
                raise ValueError(
                    f"{label} transform history is not composable: "
                    f"{transform_id} targets {transform.target_frame!r}, "
                    f"expected target frame {current_frame!r}"
                )
            current_frame = transform.source_frame
    return transform_ids


# ============ main pipeline ============
def reconstruct(photos_dir: str | Path = "photos",
                out_dir: str | Path = DEFAULT_OUT_DIR,
                web_dir: str | Path = DEFAULT_WEB_DIR,
                engine: str = "mock",
                reg_engine: str = "auto",
                splat_map: list[SplatInput] | None = None,
                base_scene: str | Path | None = None,
                dedup_voxel: float = 0.10,
                replace_margin: float = 2.0) -> dict:
    """端到端重建, 返回 manifest dict"""
    photos_dir = Path(photos_dir)
    out_dir = Path(out_dir)
    web_dir = Path(web_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if engine not in {"mock", "import"}:
        raise ValueError(f"未知泼溅引擎: {engine}")
    if splat_map is not None and (
        not isinstance(splat_map, list)
        or any(not isinstance(item, SplatInput) for item in splat_map)
    ):
        raise TypeError("splat_map must be a list of SplatInput objects")
    if engine == "mock" and splat_map:
        raise ValueError("engine=mock cannot consume splat_map; use engine=import")

    # 1. Registration -> one explicit target-frame contract
    reg = register(photos_dir, out_dir / "registration.json", engine=reg_engine)
    dedup_voxel = _validate_spatial_parameter(
        "dedup_voxel", dedup_voxel, reg.target_frame
    )
    replace_margin = _validate_spatial_parameter(
        "replace_margin",
        replace_margin,
        reg.target_frame,
        active=base_scene is not None,
    )
    transform_definitions, transform_evidence = _transform_catalog(reg, splat_map)

    # 2. 每会话泼溅
    synthetic_geometry = False
    ancestry: list[dict] = []
    if engine == "import":
        if not splat_map:
            raise ValueError("engine=import 需要 --splat SplatInput JSON 契约")
        scenes = import_session_splats(splat_map, reg)
        for item, scene in zip(splat_map, scenes, strict=True):
            _validate_scene_history(
                scene,
                transform_definitions,
                label=f"imported scene {item.session_id!r}",
            )
        ancestry = [
            {
                "kind": "import-splat",
                "session_id": item.session_id,
                "artifact_sha256": _sha256_file(Path(item.path)),
                "source_frame": item.source_frame.model_dump(mode="json"),
                "result_frame_id": scene.frame_id,
                "units": scene.units,
                "applied_transform_ids": list(scene.applied_transform_ids),
            }
            for item, scene in zip(splat_map, scenes, strict=True)
        ]
    elif engine == "mock":
        # This code path constructs geometry rather than reconstructing measured
        # scene content.  Record the fact at creation time; downstream trust
        # logic must not infer it from the engine's display name.
        synthetic_geometry = True
        scenes = []
        for sess in reg.sessions:
            s = synth_session_splat(sess, reg, photos_dir)
            _validate_scene_history(
                s,
                transform_definitions,
                label=f"synthetic scene {sess.session_id!r}",
            )
            logger.info(f"mock 泼溅: {sess.session_id} ({sess.kind}, "
                        f"{len(sess.images)} 图) → {len(s)} 高斯")
            scenes.append(s)
            ancestry.append({
                "kind": "synthetic-session",
                "session_id": sess.session_id,
                "source_frame": reg.pose_frame.model_dump(mode="json"),
                "result_frame_id": s.frame_id,
                "units": s.units,
                "applied_transform_ids": list(s.applied_transform_ids),
            })
    # 3. Merge only after every scene reports the same target frame/units.
    merged = GaussianScene.merge(scenes, dedup_voxel=dedup_voxel)
    _validate_scene_history(
        merged,
        transform_definitions,
        label="merged scene",
        require_composable=False,
    )
    logger.info(f"拼接完成: {len(scenes)} 个会话场景 → {len(merged)} 高斯 "
                f"(dedup_voxel={dedup_voxel} {reg.target_frame.units.value})")

    # 4. 可变清晰: 基底场景区域替换 (补拍的新重建覆盖旧区域)
    if base_scene:
        base_path = Path(base_scene)
        base = GaussianScene.load_ply(base_path)
        if (
            base.frame_id != reg.target_frame.frame_id
            or base.units != reg.target_frame.units.value
        ):
            raise ValueError(
                "base scene coordinate contract does not match reconstruction "
                f"target: {base.frame_id}/{base.units} vs "
                f"{reg.target_frame.frame_id}/{reg.target_frame.units.value}"
            )
        base_transform_ids = _validate_scene_history(
            base, transform_definitions, label="base scene"
        )
        ancestry.insert(0, {
            "kind": "base-scene",
            "artifact_sha256": _sha256_file(base_path),
            "source_frame": reg.target_frame.model_dump(mode="json"),
            "result_frame_id": base.frame_id,
            "units": base.units,
            "applied_transform_ids": base_transform_ids,
        })
        before = len(base)
        merged = base.replace_region(merged, margin=replace_margin)
        _validate_scene_history(
            merged,
            transform_definitions,
            label="replaced scene",
            require_composable=False,
        )
        logger.info(f"区域替换: 基底 {before} 高斯 + 新重建 → {len(merged)} 高斯")

    # 5. 导出: 全量 3dgs ply + LOD simple ply + manifest
    audit_full_path = out_dir / "scene_full.ply"
    merged.save_ply(audit_full_path, flavor="3dgs")
    web_full_path = web_dir / "recon_full.ply"
    web_full_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(audit_full_path, web_full_path)
    lod_files = merged.export_lod(web_dir, "recon", flavor="simple")

    full_artifact = _artifact_descriptor(
        web_full_path,
        web_dir,
        kind="3dgs-ply",
        fidelity="full-3dgs",
        attributes=_full_3dgs_attributes(merged),
        sh_degree=merged.sh_degree,
    )
    lod_artifacts = {
        str(level): _artifact_descriptor(
            web_dir / filename,
            web_dir,
            kind="simple-ply",
            fidelity="dc-point-preview",
            attributes=list(SIMPLE_PREVIEW_ATTRIBUTES),
            sh_degree=None,
        )
        for level, filename in lod_files.items()
    }

    lo, hi = merged.bounds()
    applied_transform_ids = list(merged.applied_transform_ids)
    applied_transforms = [
        transform_definitions[transform_id] for transform_id in applied_transform_ids
    ]
    applied_set = set(applied_transform_ids)
    for ancestor in ancestry:
        ancestor["applied_transform_ids"] = [
            transform_id
            for transform_id in ancestor["applied_transform_ids"]
            if transform_id in applied_set
        ]
    ancestry_transform_ids = list(dict.fromkeys(
        transform_id
        for ancestor in ancestry
        for transform_id in ancestor["applied_transform_ids"]
    ))
    if ancestry_transform_ids != applied_transform_ids:
        raise RuntimeError(
            "manifest ancestry does not match final applied transform history: "
            f"{ancestry_transform_ids} != {applied_transform_ids}"
        )

    def transform_record(transform: FrameTransform) -> dict:
        return {
            **transform.model_dump(mode="json"),
            "evidence": transform_evidence[transform.transform_id],
        }

    registration_chain = list(reg.transform_chain)
    catalog_ids = list(dict.fromkeys([
        *(transform.transform_id for transform in registration_chain),
        *applied_transform_ids,
    ]))
    transform_catalog = [
        transform_record(transform_definitions[transform_id])
        for transform_id in catalog_ids
    ]
    for ancestor in ancestry:
        ancestor["transform_path"] = [
            transform_record(transform_definitions[transform_id])
            for transform_id in ancestor["applied_transform_ids"]
        ]

    metric_evidence = list(reg.target_frame.evidence)
    for transform in applied_transforms:
        metric_evidence.extend(transform_evidence[transform.transform_id])
    metric_evidence = list(dict.fromkeys(metric_evidence))

    provenance_frames = [reg.pose_frame, reg.world_frame]
    provenance_frames.extend(
        item.source_frame for item in (splat_map or [])
    )
    is_synthetic = synthetic_geometry or any(
        frame is not None and frame.provenance is FrameProvenance.SYNTHETIC
        for frame in provenance_frames
    )
    provenance_known = all(
        frame.provenance is not FrameProvenance.UNKNOWN
        for frame in provenance_frames
        if frame is not None
    )
    geometry_usability = _derive_geometry_usability(
        reg.target_frame,
        reg.alignment_status,
        metric_evidence,
        synthetic=is_synthetic,
        provenance_known=provenance_known,
    )
    manifest = {
        "schema_version": 2,
        "engine": engine,
        "registration_engine": reg.engine,
        "gaussian_count": len(merged),
        "bounds": {"min": lo.tolist(), "max": hi.tolist()},
        "spatial_parameters": {
            "frame_id": reg.target_frame.frame_id,
            "units": reg.target_frame.units.value,
            "dedup_voxel": dedup_voxel,
            "replace_margin": replace_margin if base_scene is not None else None,
        },
        "lod": {level: artifact["path"] for level, artifact in lod_artifacts.items()},
        "full_3dgs": full_artifact["path"],
        "artifacts": {
            "full_3dgs": full_artifact,
            "lod": lod_artifacts,
        },
        "sessions": [
            {"session_id": s.session_id, "kind": s.kind,
             "n_images": len(s.images)} for s in reg.sessions
        ],
        "coordinate_contract": {
            "pose_frame": reg.pose_frame.model_dump(mode="json"),
            "target_frame": reg.target_frame.model_dump(mode="json"),
            "alignment_status": reg.alignment_status.value,
            "metric_evidence": metric_evidence,
            "transform_chain": [
                transform_record(transform) for transform in registration_chain
            ],
            "transform_catalog": transform_catalog,
            "applied_transform_ids": applied_transform_ids,
            "ancestry": ancestry,
        },
        "provenance": {
            "requested_reconstruction_engine": engine,
            "actual_reconstruction_engine": (
                "mock-proxy" if engine == "mock" else "imported-3dgs"
            ),
            "requested_registration_engine": reg_engine,
            "actual_registration_engine": reg.engine,
            "synthetic": is_synthetic,
            "geometry_usability": geometry_usability,
            "artifact_fidelity": {
                "full_3dgs": "full-3dgs",
                "lod_preview": "dc-point-preview",
            },
            # Current LOD artifacts are simple point PLYs; the viewer must not
            # claim anisotropic splat rendering until its capability says so.
            "render_fidelity": "dc-point-preview",
        },
    }
    manifest_path = web_dir / "recon_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    logger.info(f"重建完成: {len(merged)} 高斯 | LOD {list(lod_files)} | "
                f"manifest → {manifest_path}")
    return manifest


def _parse_splat_args(pairs: list[str]) -> list[SplatInput]:
    """Parse full JSON contracts, with conservative legacy shorthand support.

    A JSON path may contain one SplatInput object or a list.  The historical
    ``SESSION@FRAME=PLY`` form remains parseable, but its frame facts are all
    unknown and therefore require an API/JSON supplied transform before any
    metric no-op import can succeed.
    """
    out = []
    for pair in pairs:
        spec_path = Path(pair)
        if spec_path.is_file() and spec_path.suffix.lower() == ".json":
            try:
                raw = json.loads(spec_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid --splat JSON spec {spec_path}: {exc}") from exc
            records = raw if isinstance(raw, list) else [raw]
            if not records or any(not isinstance(record, dict) for record in records):
                raise ValueError("--splat JSON spec must contain an object or object list")
            out.extend(SplatInput.model_validate(record) for record in records)
            continue
        if "=" not in pair or "@" not in pair.split("=", 1)[0]:
            raise ValueError(
                "--splat must be a SplatInput JSON path (recommended) or legacy "
                f"session_id@frame_id=path.ply shorthand: {pair}"
            )
        declaration, path = pair.split("=", 1)
        session_id, frame_id = declaration.split("@", 1)
        out.append(SplatInput(
            session_id=session_id,
            path=path,
            frame_id=frame_id,
        ))
    return out


def main():
    parser = argparse.ArgumentParser(
        description="端到端重建: 照片+视频 → 统一坐标系 → 高斯泼溅 → LOD",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--photos", default="photos", help="输入图像目录")
    parser.add_argument("--out", default=DEFAULT_OUT_DIR, help="重建输出目录")
    parser.add_argument("--web", default=DEFAULT_WEB_DIR, help="Web 数据输出目录")
    parser.add_argument("--engine", default="mock", choices=["mock", "import"],
                        help="泼溅引擎 (mock=本机代理, import=导入云端训练 ply)")
    parser.add_argument("--reg-engine", default="auto",
                        choices=["auto", "colmap", "mock"], help="配准引擎")
    parser.add_argument("--splat", action="append", default=[],
                        metavar="SPEC.json",
                        help=("完整 SplatInput JSON 契约（推荐，可多次）；旧 "
                              "SESSION@FRAME=PLY 仅保留为 unknown-frame 兼容入口"))
    parser.add_argument("--base-scene", default=None,
                        help="基底场景 ply (新重建将替换其对应区域 → 变清晰)")
    parser.add_argument("--dedup-voxel", type=float, default=0.10,
                        help="拼接去重体素 (米制 target frame；非米制仅允许 0)")
    parser.add_argument("--replace-margin", type=float, default=2.0,
                        help="区域替换外扩边距 (米制 target frame；非米制仅允许 0)")
    args = parser.parse_args()

    manifest = reconstruct(
        photos_dir=args.photos, out_dir=args.out, web_dir=args.web,
        engine=args.engine, reg_engine=args.reg_engine,
        splat_map=_parse_splat_args(args.splat) or None,
        base_scene=args.base_scene, dedup_voxel=args.dedup_voxel,
        replace_margin=args.replace_margin,
    )
    print(f"\n重建完成: {manifest['gaussian_count']} 高斯")
    print(f"  LOD: {manifest['lod']}")
    print("  查看: make serve  # http://127.0.0.1:8000/web/studio/")


if __name__ == "__main__":
    main()
