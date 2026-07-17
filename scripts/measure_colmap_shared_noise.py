"""实测两次【真实】COLMAP 重建之间共享相机中心的 Sim3 残差 —— 跨批次对齐的地基。

## 为什么这个脚本先于一切实现

``pipeline.alignment.align_to_reference`` 在拿到本脚本的输出之前【默认 raise】。
理由: 跨批次对齐的所有误差预算【随共享相机中心噪声线性缩放】, 而这个噪声在真实
南台照片上【没有人测过】。现有的 1.49mm / 45.13m = 6.8e-05 来自 Blender 渲的
24 张 / emission-only / 高频程序纹理场景 —— 那是 SfM 的【最优情况】, 且【那次测量
本身不在仓库里、不可复核】(见 docs/verification/2026-07-17-derived-mode-control-point-floor.md)。
真实照片有:

  - 视角相关高光 (瓦屋面、湿地面)
  - 曝光变化 (自动曝光在不同朝向漂移)
  - 运动模糊
  - 夯土墙/瓦屋面的【重复自相似纹理】(误匹配的主要来源)
  - 植被摆动 (违反刚性场景假设)

噪声必然更高, 但【高多少无人知】。未标定就上线 = 给未知误差盖米制章, 正好违背本
仓库存在的理由。

## 本脚本还要回答一个可能【推翻整个方案】的问题

村庄尺度的 BA drift 完全未测。上述 6.8e-05 只在 45m 尺度确认了"残差是无结构噪声,
Sim3 就是全部故事"。整村是几百米、上千张的长走廊采集, 两个批次【各自】带自己的
累积 drift -> 两者之差可能【不再是一个全局 Sim3】, 而是 Sim3 + 低频 warp。
而【正是 drift 才迫使你分批】—— 所以这个风险不是理论洁癖, 它是本方案的对手。

故本脚本除了残差量级, 还输出两个【结构性】判据:

  ``residual_distance_corr``
      残差大小与"离重叠带质心的距离"的相关。纯噪声 -> 趋近 0。若显著为正, 说明
      残差有【空间结构】, 即单个全局 Sim3 没有吸收掉全部差异 (drift 的指纹)。
  ``affine_rms_m``
      改用 12 自由度仿射 (而非 7 自由度 Sim3) 拟合后的残差。若仿射【显著】优于
      Sim3, 说明差异里有 Sim3 表达不了的成分 -> 需要分段/局部对齐, 本方案要重设计。

**本脚本只【测量并如实报告】, 不裁决 go/no-go。** 判据的阈值标定不出来就不编 ——
数字给人看, 由人结合真实采集决定。这也是为什么输出里没有 "passed" 字段。

## 批次 A 必须【已对齐到米制 ENU】—— 否则量不出米

SfM 的 gauge 是【任意】的 (COLMAP 常把双视图基线取单位长)。所以 ``pose.t_xyz``
是任意单位, 直接拿它算残差再叫"米"就是【凭空造米】—— 正是本仓库存在的理由要挡
的事。实测: 同一个真实 7.09cm 的批间不一致, 三种 COLMAP gauge 下直读 t_xyz 分别
得 0.000677 / 0.067697 / 6.769743 —— 同一个物理事实差 4 个数量级。而这个数正是
上线前置门的钥匙: 操作者读到"0.68mm, 比理论 1.49mm 还好, 上线", 真实噪声却是
7cm (低报 100 倍); gauge 反向偏时又会把健康采集报成灾难。

故本脚本【要求 A 已经过 align_registration 对齐到米制 ENU 世界】(实测控制点 /
RTK / GPS 锚都行), 并把共享中心【过 A 的 pose_to_world】拿到真实米坐标当靶标。
残差因此落在 A 的米制世界里, ``*_m`` 才名副其实。B 不需要对齐: 拟合 B->A-world
的 Sim3 会吸收掉 B 的任意 gauge, 残差仍在 A 的米里。A 未对齐 -> fail-closed。

这【不】循环: 对齐 A 走的是 align_registration (实测控制点/GPS), 不需要本标定;
只有跨批次的 align_to_reference 才需要。

## 产出的记录【绑死在 A 的那一次对齐上】

记录里的 ``metric_basis`` 写的是 A 的 ``pose_to_world.transform_id`` (内容寻址),
而 ``load_shared_noise_calibration`` 会拿【消费时的参考批】逐字复核它。后果是实际的:

  - **A 重新对齐过 (换了控制点/改了 max_rms), 旧记录立即作废** —— 因为它的米是用
    【旧的 A 的尺】量的。请对新的 A 重跑本脚本。
  - **一份记录不能给另一对批次开门**。A->B 的记录开不了 B->C 的门: 那是 B 的世界,
    不是 A 的世界。链式缝合 (A->B->C) 每一跳各需一份记录。

这【不是】防伪: 手里握着 A 的人照样能算出真 transform_id 再手写假数字。它挡的是
【张冠李戴与陈旧】, 而"测量真的做过"仍是【操作者的声称】—— 与手写一份 enu_xyz 控制点
冒充实测同属一类。别把它当防伪读。

## 想要的是漫游而不是测量?

那就【不需要本脚本】。``pipeline.alignment.merge_for_preview`` 把批次缝进同一个任意
坐标系, 不声称米制, 故不需要本标定 —— 漫游只需要各批次落在同一个坐标系里。本脚本
及它服务的米制路, 只在你要【测量】时才需要。

## 用法

    # 前提: batch01 已用实测控制点/GPS 对齐 (registration.json 里 alignment_status=ALIGNED)
    .venv\\Scripts\\python scripts/measure_colmap_shared_noise.py \\
        --registration-a out/batch01/aligned_registration.json \\
        --registration-b out/batch02/registration.json \\
        --out calibration/colmap_shared_noise.json

A 是【已对齐到米制 ENU】的批次; B 是另一次独立重建 (sfm-local 即可)。两者必须
【共享一部分重叠影像】。注意: 若 mapper 把一批拆成 sparse/0 和 sparse/1, 每个子
模型各有独立 gauge, 批内就无法用一个 Sim3 统一 —— 该情形本脚本【未验证】, 而弱
纹理村落上分裂很可能发生。

已知诚实限制:

  - **一致 != 正确**: 本脚本量的是两次重建的【一致性】, 不是绝对精度。两次可以高度
    一致却【同时错】(同一套重复纹理在两次里以同样方式误匹配)。一致性是必要条件,
    不是充分条件。
  - **米的天花板是 A 的锚定**: 残差以 A 的世界为尺, A 若靠消费级 GPS 锚定 (3-10m),
    这里的"米"就带着 A 的尺度误差 —— 报出的是【相对 A 的尺】的米。故记录同时带上
    无量纲的 ``relative_rms``, 它不随任何 gauge 或 A 的尺度误差浮动。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from pipeline.alignment import (
    AlignmentError,
    SharedNoiseCalibration,
    _residuals,
    _source_span,
    control_points_from_shared_images,
    umeyama_sim3,
)
from pipeline.recon_schema import RegistrationResult

_MIN_SHARED_IMAGES = 8


def shared_centres(
    reg_a: RegistrationResult, reg_b: RegistrationResult
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """共享影像的中心: src = B 的 pose_frame (任意), dst = A 的【米制 ENU 世界】。

    dst 走 ``control_points_from_shared_images``, 它强制 A 已对齐且 world_frame 是
    米制 ENU, 否则 fail-closed —— 未对齐的 A 只有任意单位, 拿它当尺量出来的数不是米。
    src 留在 B 的任意 gauge 里是【对的】: 拟合的 Sim3 会吸收 B 的 gauge, 残差落在
    dst 空间 = A 的米制世界。
    """
    targets = control_points_from_shared_images(reg_a, reg_b)
    if len(targets) < _MIN_SHARED_IMAGES:
        raise AlignmentError(
            f"两批只共享 {len(targets)} 张影像 (<{_MIN_SHARED_IMAGES}): 不足以标定共享"
            "中心噪声。重叠带必须【非共面】且足够密 —— 这也是采集时的硬要求"
        )
    b_by_image = {pose.image: pose for pose in reg_b.poses}
    shared = [cp.image for cp in targets]
    src = np.array([b_by_image[i].t_xyz for i in shared], dtype=np.float64)
    dst = np.array([cp.enu_xyz for cp in targets], dtype=np.float64)
    return src, dst, shared


def fit_affine(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """12 自由度仿射最小二乘 (对照组): dst ~= M @ src + t。"""
    design = np.hstack([src, np.ones((len(src), 1))])
    solution, *_ = np.linalg.lstsq(design, dst, rcond=None)
    return design @ solution


def measure(reg_a: RegistrationResult, reg_b: RegistrationResult) -> dict:
    """量 B -> A的米制世界 的 Sim3 残差及其结构性, 单位【米】(见模块 docstring)。

    注意这里的拟合只为【测量】, 不产生任何声称: 不发世界帧, 不盖米制章。
    """
    src, dst, shared = shared_centres(reg_a, reg_b)

    scale, rotation, translation = umeyama_sim3(src, dst)
    if not np.isfinite(scale) or scale <= 0:
        raise AlignmentError(f"共享中心拟合出非正尺度 {scale}: 两批可能并非同一场景")
    per_point, rms, max_residual = _residuals(src, dst, scale, rotation, translation)

    # 场景尺度: A 侧共享中心的最大跨度 (最大奇异值 ~ 主方向展布)。
    singular = _source_span(dst)
    extent = float(np.linalg.norm(dst.max(axis=0) - dst.min(axis=0)))

    # 结构性判据 1: 残差 vs 离质心距离的相关。纯噪声 -> ~0。
    distance = np.linalg.norm(dst - dst.mean(axis=0), axis=1)
    if distance.std() > 0 and per_point.std() > 0:
        corr = float(np.corrcoef(distance, per_point)[0, 1])
    else:
        corr = 0.0

    # 结构性判据 2: 12 自由度仿射能否显著优于 7 自由度 Sim3。
    affine_pred = fit_affine(src, dst)
    affine_rms = float(np.sqrt((np.linalg.norm(dst - affine_pred, axis=1) ** 2).mean()))

    return {
        "record_version": 2,
        "measured_on": __import__("datetime").date.today().isoformat(),
        "source": (
            f"{reg_a.engine}:{reg_a.pose_frame.frame_id} vs "
            f"{reg_b.engine}:{reg_b.pose_frame.frame_id}"
        ),
        "n_shared_images": len(shared),
        # 这批 *_m 在哪个米制世界里量的, 以及它凭什么是米。transform_id 内容寻址,
        # 消费者可拿 A 复核 —— 而不是靠本脚本自称。
        "reference_world_frame_id": reg_a.world_frame.frame_id,
        "metric_basis": f"reference-pose-to-world:{reg_a.pose_to_world.transform_id}",
        "shared_centre_rms_m": rms,
        "shared_centre_max_m": max_residual,
        "scene_extent_m": extent,
        # 无量纲, 不随 gauge 或 A 的尺度误差浮动 —— 唯一的 gauge 无关判据, 故入记录。
        "relative_rms": rms / extent if extent > 0 else float("nan"),
        "residual_distance_corr": corr,
        "affine_rms_m": affine_rms,
        # 以下仅供人读, 不进 SharedNoiseCalibration。
        "_sim3_scale": scale,
        "_source_singular_values": [float(v) for v in singular.tolist()],
        "_affine_improvement": (rms - affine_rms) / rms if rms > 0 else 0.0,
        "_shared_images": shared,
    }


def _report(record: dict) -> None:
    print("=== 两次独立 COLMAP 重建: 共享相机中心 Sim3 残差实测 ===")
    print(f"共享影像            : {record['n_shared_images']}")
    print(f"米制基准            : {record['reference_world_frame_id']} "
          f"({record['metric_basis']})")
    print(f"场景跨度            : {record['scene_extent_m']:.3f} m")
    print(f"Sim3 残差 RMS       : {record['shared_centre_rms_m']:.6f} m")
    print(f"Sim3 残差 max       : {record['shared_centre_max_m']:.6f} m")
    print(f"相对残差 (RMS/跨度) : {record['relative_rms']:.3e}  (无量纲, gauge 无关)")
    print(f"拟合尺度            : {record['_sim3_scale']:.6f}")
    print("--- 结构性判据 (回答: 村庄尺度上单个全局 Sim3 还够不够) ---")
    print(f"残差~距离 相关      : {record['residual_distance_corr']:+.4f}")
    print(f"仿射(12dof) 残差    : {record['affine_rms_m']:.6f} m")
    print(f"仿射相对改善        : {record['_affine_improvement']:+.1%}")
    print()
    print("怎么读 (本脚本【不】替你裁决):")
    print("  - 相关趋近 0 且仿射改善很小 -> 残差是无结构噪声, Sim3 就是全部故事。")
    print("  - 相关显著为正 或 仿射显著更好 -> 差异含 Sim3 表达不了的低频 warp,")
    print("    即批次各自的 BA drift 已经压过噪声。这种情况下【单个全局 Sim3 不够】,")
    print("    跨批次对齐方案需要重新设计 (局部/分段对齐), 不要硬上。")
    print("  - 一致 != 正确: 两次重建可能以同样方式误匹配重复纹理而【同时错】。")
    print("  - 上面的米是【以 A 的世界为尺】的米: A 若靠消费级 GPS 锚定, 这些数就带着")
    print("    A 的尺度误差。gauge 无关的判据只有 relative_rms。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="实测两次真实 COLMAP 重建的共享相机中心噪声 (跨批次对齐的前置标定)"
    )
    parser.add_argument("--registration-a", required=True,
                        help="批次 A 的 registration.json; 必须【已对齐到米制 ENU】"
                             "(alignment_status=ALIGNED) —— 残差以它的世界为尺")
    parser.add_argument("--registration-b", required=True,
                        help="批次 B 的 registration.json (sfm-local 即可); "
                             "必须与 A 共享重叠影像")
    parser.add_argument("--out", default=None,
                        help="标定记录输出路径 (默认 calibration/colmap_shared_noise.json)")
    args = parser.parse_args(argv)

    reg_a = RegistrationResult.model_validate_json(
        Path(args.registration_a).read_text(encoding="utf-8"))
    reg_b = RegistrationResult.model_validate_json(
        Path(args.registration_b).read_text(encoding="utf-8"))
    record = measure(reg_a, reg_b)
    _report(record)

    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent / "calibration"
        / "colmap_shared_noise.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    # 【经消费方的 schema 写出】: 产出与消费共用一个 SharedNoiseCalibration, 本脚本
    # 因此在结构上【写不出】一份前置门载不进去的记录。
    #
    # 这不是洁癖。实跑发现的断链: 早先直接 json.dumps(record) 会把 '_' 前缀的人读字段
    # 一起写进文件, 而 SharedNoiseCalibration 是 extra='forbid' -> 按文档跑完标定后
    # align_to_reference 【永远】报"无法解析或不自洽", 整个上线前置门按它自己的文档
    # 流程不可满足。'_' 字段只给人看 (已在 _report 里打印), 不进信任根。
    calibration = SharedNoiseCalibration.model_validate(
        {k: v for k, v in record.items() if not k.startswith("_")}
    )
    # LF: 标定记录是信任根, 跨平台按字节可复现。
    out.write_text(
        json.dumps(json.loads(calibration.model_dump_json()), indent=2,
                   ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\n[OK] 标定记录已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
