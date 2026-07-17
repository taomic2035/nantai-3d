"""采集预检的行为契约。

重点不在"分数好不好看", 而在**诚实**: 无图必须 fail-closed; 缺解码器必须承认跳过而不是
静默假装通过; 耗时估计必须对得上手册 §4 实测锚点; verdict 不得假装预测 SfM 成败。
"""
from __future__ import annotations

import json
import shutil

import numpy as np
import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from pipeline import capture_quality
from pipeline.capture_quality import CaptureQualityError, analyze_capture


def _checkerboard(width: int = 320, height: int = 240, block: int = 8) -> np.ndarray:
    """高频棋盘: Laplacian 方差高 (清晰), 且 JPEG 压得动 (测试跑得快)。"""
    ys, xs = np.mgrid[0:height, 0:width]
    pattern = (((xs // block) + (ys // block)) % 2 * 255).astype(np.uint8)
    return np.dstack([pattern] * 3)


def _blurred(image: np.ndarray, sigma: float = 6.0) -> np.ndarray:
    return np.asarray(
        Image.fromarray(image).filter(
            __import__("PIL.ImageFilter", fromlist=["GaussianBlur"]).GaussianBlur(sigma)
        )
    )


def _write_sharp(path, width: int = 320, height: int = 240) -> None:
    Image.fromarray(_checkerboard(width, height)).save(path)


def _write_blurry(path, width: int = 320, height: int = 240) -> None:
    Image.fromarray(_blurred(_checkerboard(width, height))).save(path)


def _write_exif_photo(path, *, when: str | None, gps: tuple[float, float] | None) -> None:
    exif = Image.Exif()
    if when is not None:
        exif[0x8769] = {0x9003: when}
    if gps is not None:
        lat, lon = gps
        exif[0x8825] = {
            1: "N" if lat >= 0 else "S",
            2: (IFDRational(int(abs(lat))), IFDRational(0), IFDRational(0)),
            3: "E" if lon >= 0 else "W",
            4: (IFDRational(int(abs(lon))), IFDRational(0), IFDRational(0)),
        }
    Image.fromarray(_checkerboard()).save(path, exif=exif)


@pytest.fixture
def healthy_capture(tmp_path):
    """60 张 1200x900 清晰照片 —— 落在手册建议的 50~300 张区间内。"""
    root = tmp_path / "healthy"
    root.mkdir()
    seed = root / "IMG_000.jpg"
    Image.fromarray(_checkerboard(1200, 900)).save(seed, quality=92)
    for index in range(1, 60):
        shutil.copy(seed, root / f"IMG_{index:03d}.jpg")
    return root


# --- fail-closed: 没有可分析的证据就不许给结论 ---------------------------------


def test_missing_directory_fails_closed(tmp_path):
    with pytest.raises(CaptureQualityError) as excinfo:
        analyze_capture(tmp_path / "nope")
    assert "不存在" in str(excinfo.value)


def test_empty_directory_fails_closed(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CaptureQualityError):
        analyze_capture(empty)


def test_directory_without_images_fails_closed(tmp_path):
    root = tmp_path / "notes"
    root.mkdir()
    (root / "readme.txt").write_text("no photos here", encoding="utf-8", newline="\n")
    with pytest.raises(CaptureQualityError) as excinfo:
        analyze_capture(root)
    # 诚实的失败: 必须说清为什么, 而不只是抛个空异常
    assert "图片" in str(excinfo.value)


# --- 模糊度 -------------------------------------------------------------------


def test_blur_scores_separate_sharp_from_blurry(tmp_path):
    root = tmp_path / "mixed"
    root.mkdir()
    for index in range(3):
        _write_sharp(root / f"sharp_{index}.png")
    for index in range(2):
        _write_blurry(root / f"blur_{index}.png")

    report = analyze_capture(root)
    scores = report["blur"]["scores"]
    sharp = [scores[f"sharp_{i}.png"] for i in range(3)]
    blurry = [scores[f"blur_{i}.png"] for i in range(2)]
    assert min(sharp) > max(blurry) * 5, "清晰图的 Laplacian 方差应远高于模糊图"


def test_blurry_images_are_counted_and_listed(tmp_path):
    root = tmp_path / "mixed"
    root.mkdir()
    for index in range(4):
        _write_sharp(root / f"sharp_{index}.png")
    for index in range(2):
        _write_blurry(root / f"blur_{index}.png")

    report = analyze_capture(root)
    assert report["blur"]["available"] is True
    assert report["blur"]["blurry_count"] == 2
    assert sorted(report["blur"]["blurry_files"]) == ["blur_0.png", "blur_1.png"]
    assert report["blur"]["blurry_ratio"] == pytest.approx(2 / 6, abs=1e-4)


def test_blur_threshold_is_declared_heuristic(tmp_path):
    root = tmp_path / "one"
    root.mkdir()
    _write_sharp(root / "a.png")
    report = analyze_capture(root)
    # 阈值是启发式, 不是精确判据 —— 报告必须自己说出来
    assert report["blur"]["threshold_is_heuristic"] is True
    assert "启发式" in report["blur"]["threshold_note"]


# --- EXIF / GPS ---------------------------------------------------------------


def test_exif_datetime_and_gps_counted(tmp_path):
    root = tmp_path / "exif"
    root.mkdir()
    _write_exif_photo(root / "a.jpg", when="2026:07:17 10:00:00", gps=(31.0, 121.0))
    _write_exif_photo(root / "b.jpg", when="2026:07:17 10:00:05", gps=(31.0, 121.0))
    _write_exif_photo(root / "c.jpg", when="2026:07:17 10:00:10", gps=None)
    _write_exif_photo(root / "d.jpg", when=None, gps=None)

    report = analyze_capture(root)
    assert report["exif"]["with_datetime"] == 3
    assert report["exif"]["with_gps"] == 2
    assert report["exif"]["gps_ratio"] == pytest.approx(0.5)


def test_gps_presence_warns_about_accuracy_and_default_rms_gate(tmp_path):
    root = tmp_path / "gps"
    root.mkdir()
    for index in range(3):
        _write_exif_photo(root / f"g{index}.jpg", when=None, gps=(31.0, 121.0))

    report = analyze_capture(root)
    note = report["exif"]["gps_note"]
    assert "3~10" in note or "3–10" in note, "必须说明消费级 GPS 精度量级"
    assert "2.0" in note, "必须提醒默认 --max-rms 2.0 常挡住"
    assert report["exif"]["can_try_from_gps"] is True


def test_exif_probe_does_not_spam_warnings_for_images_without_exif(tmp_path, caplog):
    """PNG 没有 EXIF 是**正常情况**, 不是警告。

    实测发现: exifread 会对每张 PNG 打一条 "PNG file does not have exif data",
    12 张图就刷 12 行, 把报告淹掉且看起来像坏了。缺 EXIF 已经在报告里如实计数了。
    """
    root = tmp_path / "pngs"
    root.mkdir()
    for index in range(3):
        _write_sharp(root / f"s{index}.png")

    with caplog.at_level("WARNING"):
        report = analyze_capture(root)

    assert report["exif"]["with_datetime"] == 0
    noise = [r for r in caplog.records if "exif" in r.name.lower()]
    assert noise == [], f"预检不该刷 exifread 警告: {[r.message for r in noise]}"


def test_no_gps_reports_unavailable_without_guessing(tmp_path):
    root = tmp_path / "nogps"
    root.mkdir()
    _write_sharp(root / "a.png")
    report = analyze_capture(root)
    assert report["exif"]["with_gps"] == 0
    assert report["exif"]["can_try_from_gps"] is False


# --- 分辨率 -------------------------------------------------------------------


def test_low_megapixel_capture_is_flagged(tmp_path):
    root = tmp_path / "tiny"
    root.mkdir()
    for index in range(3):
        _write_sharp(root / f"s{index}.png", width=320, height=240)  # 0.08MP
    report = analyze_capture(root)
    assert report["resolution"]["below_min_count"] == 3
    assert any("分辨率" in reason for reason in report["verdict"]["reasons"])


def test_resolution_reports_megapixels(healthy_capture):
    report = analyze_capture(healthy_capture)
    assert report["resolution"]["median_megapixels"] == pytest.approx(1.08, abs=0.01)
    assert report["resolution"]["below_min_count"] == 0


# --- verdict 分级 -------------------------------------------------------------


def test_verdict_unlikely_for_tiny_capture(tmp_path):
    root = tmp_path / "few"
    root.mkdir()
    for index in range(5):
        _write_sharp(root / f"s{index}.png", width=1200, height=900)
    report = analyze_capture(root)
    assert report["verdict"]["level"] == "unlikely"
    assert report["verdict"]["remedies"], "结论为负必须给出可执行的补救建议"


def test_verdict_likely_for_healthy_capture(healthy_capture):
    report = analyze_capture(healthy_capture)
    assert report["verdict"]["level"] == "likely"


def test_verdict_risky_when_many_images_are_blurry(healthy_capture):
    for index in range(20):
        target = healthy_capture / f"IMG_{index:03d}.jpg"
        Image.fromarray(_blurred(_checkerboard(1200, 900))).save(target, quality=92)
    report = analyze_capture(healthy_capture)
    assert report["verdict"]["level"] in {"risky", "unlikely"}
    assert any("模糊" in reason for reason in report["verdict"]["reasons"])
    assert any("补拍" in remedy or "重拍" in remedy for remedy in report["verdict"]["remedies"])


def test_verdict_never_claims_to_predict_sfm_success(healthy_capture):
    report = analyze_capture(healthy_capture)
    assert report["verdict"]["level"] in {"likely", "risky", "unlikely", "unknown"}
    assert report["verdict"]["is_heuristic"] is True


# --- cv2 缺失时的降级 ---------------------------------------------------------


def test_numpy_backend_used_when_cv2_missing(tmp_path, monkeypatch):
    root = tmp_path / "mixed"
    root.mkdir()
    for index in range(3):
        _write_sharp(root / f"sharp_{index}.png")
    for index in range(2):
        _write_blurry(root / f"blur_{index}.png")

    monkeypatch.setattr(capture_quality, "HAS_CV2", False)
    report = analyze_capture(root)
    # cv2 没了但 PIL 还在 -> 用 numpy 卷积继续算, 而不是直接放弃
    assert report["blur"]["available"] is True
    assert report["blur"]["backend"] == "numpy"
    assert report["blur"]["blurry_count"] == 2


def test_blur_skipped_honestly_when_no_decoder(healthy_capture, monkeypatch):
    monkeypatch.setattr(capture_quality, "HAS_CV2", False)
    monkeypatch.setattr(capture_quality, "HAS_PIL", False)
    report = analyze_capture(healthy_capture)

    assert report["blur"]["available"] is False
    assert report["blur"]["backend"] is None
    assert "跳过" in report["blur"]["skipped_reason"]
    # 关键: 没测模糊度就不许给"大概率能行"的结论
    assert report["verdict"]["level"] != "likely"
    assert any("模糊度未检测" in reason for reason in report["verdict"]["reasons"])


def test_count_and_exif_still_work_without_decoder(healthy_capture, monkeypatch):
    monkeypatch.setattr(capture_quality, "HAS_CV2", False)
    monkeypatch.setattr(capture_quality, "HAS_PIL", False)
    report = analyze_capture(healthy_capture)
    assert report["count"]["images"] == 60
    assert report["resolution"]["available"] is False


# --- COLMAP 耗时估计 ----------------------------------------------------------


def test_colmap_estimate_matches_manual_anchor_100_images(tmp_path):
    estimate = capture_quality.estimate_colmap_cost(100, matcher="exhaustive")
    # 手册 §4 实测锚点: ~100 图 ≈ 20–60 min (CPU, i7-14700)
    assert estimate["minutes_low"] == pytest.approx(20, abs=1)
    assert estimate["minutes_high"] == pytest.approx(60, abs=1)


def test_colmap_estimate_matches_manual_anchor_300_images(tmp_path):
    estimate = capture_quality.estimate_colmap_cost(300, matcher="exhaustive")
    # 手册 §4 实测锚点: ~300 图 ≈ 2–5+ 小时
    assert estimate["minutes_low"] == pytest.approx(120, abs=5)
    assert estimate["minutes_high"] == pytest.approx(300, abs=5)


def test_exhaustive_estimate_grows_quadratically(tmp_path):
    small = capture_quality.estimate_colmap_cost(100, matcher="exhaustive")
    large = capture_quality.estimate_colmap_cost(400, matcher="exhaustive")
    # 穷举匹配是 O(n^2): 4x 张数远不止 4x 耗时
    assert large["minutes_low"] > small["minutes_low"] * 8


def test_sequential_matching_term_is_an_order_faster(tmp_path):
    exhaustive = capture_quality.estimate_colmap_cost(300, matcher="exhaustive")
    sequential = capture_quality.estimate_colmap_cost(300, matcher="sequential")
    # 顺序匹配省的是**匹配项** (O(n*overlap) vs O(n^2))
    assert sequential["match_minutes_low"] < exhaustive["match_minutes_low"] / 10


def test_sequential_total_speedup_is_bounded_by_feature_extraction(tmp_path):
    """总耗时**不可能**也快一个数量级 —— 特征提取 O(n) 与匹配器无关 (Amdahl 上界)。

    锁住这条是为了不让报告吹牛: 300 图上顺序匹配总体只快约 3x, 不是 10x。
    """
    exhaustive = capture_quality.estimate_colmap_cost(300, matcher="exhaustive")
    sequential = capture_quality.estimate_colmap_cost(300, matcher="sequential")
    assert sequential["extract_minutes_low"] == exhaustive["extract_minutes_low"]
    speedup = exhaustive["minutes_low"] / sequential["minutes_low"]
    assert 2.5 < speedup < 4.0

    # 图越多匹配项越占主导, 总体加速才逼近一个数量级
    big_exhaustive = capture_quality.estimate_colmap_cost(1000, matcher="exhaustive")
    big_sequential = capture_quality.estimate_colmap_cost(1000, matcher="sequential")
    assert big_exhaustive["minutes_low"] / big_sequential["minutes_low"] > 8


def test_small_batch_estimate_discloses_known_overestimate(tmp_path):
    """模型在小批量上明显过估, 必须自己说出来。

    手册唯一的硬实测是"30 图 ~46 秒"(合成小场景), 而本模型 (锚定 100/300 图的真实照片
    预期) 在 n=30 给出约 3.9 分钟 —— 过估约 5x。过估比低估安全 (不会害人白等), 但瞒着
    用户就是不诚实。
    """
    estimate = capture_quality.estimate_colmap_cost(30, matcher="exhaustive")
    assert estimate["minutes_low"] * 60 > 46, "如果模型不再过估, 这条免责声明就该改掉"
    assert estimate["small_batch_caution"] is not None
    assert "46" in estimate["small_batch_caution"], "要点名那个实测锚点"

    # 大批量不该挂这条声明 —— 那正是模型被锚定的区间
    assert capture_quality.estimate_colmap_cost(300, "exhaustive")["small_batch_caution"] is None


def test_estimate_declares_itself_a_rough_guess(healthy_capture):
    report = analyze_capture(healthy_capture)
    assert report["colmap_estimate"]["is_rough_estimate"] is True
    assert "粗估" in report["colmap_estimate"]["note"]


def test_unordered_photos_recommend_exhaustive(healthy_capture):
    report = analyze_capture(healthy_capture)
    # 没有顺序证据时不许推荐 sequential (推错匹配器会直接毁掉重建)
    assert report["colmap_estimate"]["matcher_recommended"] == "exhaustive"
    assert report["colmap_estimate"]["ordering_evidence"] == []


def test_video_frame_names_are_ordering_evidence(tmp_path):
    root = tmp_path / "frames"
    root.mkdir()
    seed = root / "frame_000000.jpg"
    Image.fromarray(_checkerboard(1200, 900)).save(seed, quality=92)
    for index in range(1, 60):
        shutil.copy(seed, root / f"frame_{index:06d}.jpg")

    report = analyze_capture(root)
    assert report["colmap_estimate"]["matcher_recommended"] == "sequential"
    assert "video-frame-names" in report["colmap_estimate"]["ordering_evidence"]


# --- 诚实声明 + 可序列化 ------------------------------------------------------


def test_report_admits_it_cannot_measure_overlap(healthy_capture):
    report = analyze_capture(healthy_capture)
    limits = " ".join(report["honesty"]["limits"])
    assert "重叠" in limits, "重叠度真正决定成败, 而单图分析测不出来 —— 必须坦白"
    assert "COLMAP" in limits


def test_report_is_json_serializable(healthy_capture):
    report = analyze_capture(healthy_capture)
    encoded = json.dumps(report, ensure_ascii=False)
    assert json.loads(encoded)["count"]["images"] == 60


# --- CLI ----------------------------------------------------------------------


def test_cli_json_output_is_machine_readable(healthy_capture, capsys):
    from scripts.check_capture import main

    assert main([str(healthy_capture), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"]["level"] == "likely"


def test_cli_human_report_is_chinese_and_mentions_limits(healthy_capture, capsys):
    from scripts.check_capture import main

    assert main([str(healthy_capture)]) == 0
    out = capsys.readouterr().out
    assert "结论" in out
    assert "重叠" in out, "人类报告也必须坦白测不出重叠度"


def test_cli_fails_closed_with_actionable_message(tmp_path, capsys):
    from scripts.check_capture import main

    assert main([str(tmp_path / "missing")]) == 2
    assert "不存在" in capsys.readouterr().err
