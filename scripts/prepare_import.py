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

用法:
    python scripts/prepare_import.py trained/point_cloud.ply [--synthetic]
    # 生成 recon/registration.json + recon/splat-input.json, 并打印导入命令
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

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


def _local_frame(synthetic: bool) -> CoordinateFrame:
    """外部训练产物的默认坐标契约：任意尺度、无地理对齐（诚实，不假装米制）。

    synthetic=True 只改申报的来源 provenance（SYNTHETIC + 证据标签），刻意不改
    units/metric_status —— 申报合成是降级声明，不得顺带夹带任何米制提升。
    """
    return CoordinateFrame(
        frame_id="synthetic-local" if synthetic else "sfm-local",
        handedness=Handedness.RIGHT,
        axes=AxisConvention.SFM_ARBITRARY,
        units=CoordinateUnits.ARBITRARY,
        metric_status=MetricStatus.ARBITRARY,
        geo_aligned=GeoAlignment.UNALIGNED,
        provenance=FrameProvenance.SYNTHETIC if synthetic else FrameProvenance.SFM,
        evidence=["external-3dgs-import", "synthetic-source-declared"]
        if synthetic else ["external-3dgs-import"],
    )


def _write_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")


def prepare(ply: Path, out_dir: Path, session_id: str,
            synthetic: bool = False) -> tuple[Path, Path]:
    frame = _local_frame(synthetic)
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
    args = ap.parse_args(argv)
    if not args.ply.is_file():
        raise SystemExit(f"文件不存在: {args.ply}")
    if args.colmap_sparse is not None and not _check_consistency(
        args.ply, args.colmap_sparse
    ):
        return 1
    reg_path, splat_path = prepare(args.ply, args.out_dir, args.session_id,
                                   synthetic=args.synthetic)
    print(f"[OK] 已生成:\n  {reg_path}\n  {splat_path}\n")
    print("下一步导入 (非米制 frame 必须 --dedup-voxel 0):")
    print(f"  python -m pipeline.reconstruct --engine import "
          f"--registration {reg_path} --splat {splat_path} "
          f"--dedup-voxel 0 --replace-margin 0 --photos photos")
    print("然后:  python make.py serve   # http://127.0.0.1:8000/web/studio/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
