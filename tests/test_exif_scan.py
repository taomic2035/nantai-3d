"""exif_scan 单元测试: EXIF 提取 + 视频元数据 + 目录扫描聚合。

覆盖 get_exif (字段提取/空 EXIF/错误处理), get_video_meta (cv2 mock/打开失败),
scan_all (文件分类/设备统计/GPS 覆盖/拍摄批次/视频统计/CSV 输出/空目录/不存在)。

本模块是 L0 采集工具; exifread/cv2 用 mock 隔离 (不依赖真实媒体文件)。
"""

from __future__ import annotations

import csv
from unittest.mock import MagicMock

import pytest

from pipeline.utils.exif_scan import (
    PHOTO_EXTS,
    VIDEO_EXTS,
    get_exif,
    get_video_meta,
    scan_all,
)


def _fake_exif_tags() -> dict:
    return {
        "Image Make": "Canon",
        "Image Model": "EOS R5",
        "EXIF FocalLength": "50",
        "GPS GPSLatitude": "[26, 30, 0]",
        "GPS GPSLongitude": "[119, 30, 0]",
        "EXIF DateTimeOriginal": "2026:07:23 10:00:00",
        "EXIF ExifImageWidth": "1920",
        "EXIF ExifImageHeight": "1080",
    }


def _fake_photo_row(path) -> dict:
    return {
        "file": path.name,
        "type": "photo",
        "path": str(path),
        "size_kb": 100.0,
        "make": "Canon",
        "model": "EOS R5",
        "focal_mm": "50",
        "gps_lat": "[26]",
        "gps_lon": "[119]",
        "datetime": "2026:07:23 10:00:00",
        "width": "1920",
        "height": "1080",
        "duration_s": "",
        "fps": "",
    }


def _fake_video_row(path) -> dict:
    return {
        "file": path.name,
        "type": "video",
        "path": str(path),
        "size_kb": 5120.0,
        "make": "",
        "model": "",
        "focal_mm": "",
        "gps_lat": "",
        "gps_lon": "",
        "datetime": "",
        "width": 1920,
        "height": 1080,
        "duration_s": 10.0,
        "fps": 30.0,
    }


# ============================================================
# get_exif
# ============================================================


class TestGetExif:
    """EXIF 字段提取 (mock exifread.process_file)。"""

    def test_extracts_fields(self, tmp_path, monkeypatch):
        photo = tmp_path / "IMG_001.jpg"
        photo.write_bytes(b"\xff\xd8" + b"x" * 2048)  # > 1KB → size_kb > 0
        monkeypatch.setattr("exifread.process_file", lambda f, details=False: _fake_exif_tags())

        result = get_exif(photo)
        assert result["file"] == "IMG_001.jpg"
        assert result["type"] == "photo"
        assert result["make"] == "Canon"
        assert result["model"] == "EOS R5"
        assert result["focal_mm"] == "50"
        assert result["gps_lat"] == "[26, 30, 0]"
        assert result["gps_lon"] == "[119, 30, 0]"
        assert result["datetime"] == "2026:07:23 10:00:00"
        assert result["width"] == "1920"
        assert result["height"] == "1080"
        assert result["size_kb"] > 0

    def test_empty_tags_return_empty_strings(self, tmp_path, monkeypatch):
        """无 EXIF 的照片: 字段为空字符串 (非 None)。"""
        photo = tmp_path / "no_exif.jpg"
        photo.write_bytes(b"\xff\xd8fake")
        monkeypatch.setattr("exifread.process_file", lambda f, details=False: {})

        result = get_exif(photo)
        assert result["make"] == ""
        assert result["model"] == ""
        assert result["gps_lat"] == ""
        assert result["datetime"] == ""

    def test_read_error_returns_error_dict(self, tmp_path, monkeypatch):
        """exifread 抛异常 → 返回带 error 的 dict (不崩溃)。"""
        photo = tmp_path / "broken.jpg"
        photo.write_bytes(b"broken")

        def raise_error(f, details=False):
            raise RuntimeError("exifread failed")

        monkeypatch.setattr("exifread.process_file", raise_error)
        result = get_exif(photo)
        assert "error" in result
        assert result["file"] == "broken.jpg"
        assert result["size_kb"] == 0


# ============================================================
# get_video_meta
# ============================================================


class TestGetVideoMeta:
    """视频元数据提取 (mock cv2.VideoCapture)。"""

    def test_extracts_video_fields(self, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fakevideo" + b"\x00" * 2048)  # > 1KB

        fake_cap = MagicMock()
        fake_cap.isOpened.return_value = True
        # cap.get 顺序: FPS, FRAME_COUNT, WIDTH, HEIGHT
        fake_cap.get.side_effect = [30.0, 300, 1920, 1080]
        monkeypatch.setattr("cv2.VideoCapture", lambda path: fake_cap)

        result = get_video_meta(video)
        assert result["file"] == "clip.mp4"
        assert result["type"] == "video"
        assert result["fps"] == 30.0
        assert result["duration_s"] == 10.0  # 300 / 30.0
        assert result["width"] == 1920
        assert result["height"] == 1080
        assert result["size_kb"] > 0

    def test_open_failed_returns_error(self, tmp_path, monkeypatch):
        video = tmp_path / "bad.mp4"
        video.write_bytes(b"bad")
        fake_cap = MagicMock()
        fake_cap.isOpened.return_value = False
        monkeypatch.setattr("cv2.VideoCapture", lambda path: fake_cap)

        result = get_video_meta(video)
        assert result["error"] == "open_failed"


# ============================================================
# scan_all
# ============================================================


class TestScanAll:
    """目录扫描聚合 (mock get_exif/get_video_meta)。"""

    def test_classifies_photos_and_videos(self, tmp_path, monkeypatch):
        (tmp_path / "a.jpg").write_bytes(b"x")
        (tmp_path / "b.png").write_bytes(b"x")
        (tmp_path / "c.mp4").write_bytes(b"x")
        (tmp_path / "ignore.txt").write_bytes(b"x")  # 非媒体

        monkeypatch.setattr("pipeline.utils.exif_scan.get_exif", _fake_photo_row)
        monkeypatch.setattr("pipeline.utils.exif_scan.get_video_meta", _fake_video_row)

        summary = scan_all(tmp_path)
        assert summary["total"] == 3
        assert summary["photo_count"] == 2
        assert summary["video_count"] == 1

    def test_aggregates_devices_and_gps(self, tmp_path, monkeypatch):
        (tmp_path / "a.jpg").write_bytes(b"x")
        (tmp_path / "b.jpg").write_bytes(b"x")
        (tmp_path / "c.mp4").write_bytes(b"x")

        monkeypatch.setattr("pipeline.utils.exif_scan.get_exif", _fake_photo_row)
        monkeypatch.setattr("pipeline.utils.exif_scan.get_video_meta", _fake_video_row)

        summary = scan_all(tmp_path)
        # 两个 Canon EOS R5 照片
        assert summary["devices"]["Canon EOS R5"] == 2
        # 两张照片都有 GPS, 视频无
        assert summary["gps_count"] == 2
        assert summary["gps_coverage"] == "2/3"

    def test_shoot_days_aggregation(self, tmp_path, monkeypatch):
        (tmp_path / "a.jpg").write_bytes(b"x")

        monkeypatch.setattr("pipeline.utils.exif_scan.get_exif", _fake_photo_row)
        monkeypatch.setattr("pipeline.utils.exif_scan.get_video_meta", _fake_video_row)

        summary = scan_all(tmp_path)
        assert "2026:07:23" in summary["shoot_days"]

    def test_video_stats(self, tmp_path, monkeypatch):
        (tmp_path / "a.mp4").write_bytes(b"x")
        (tmp_path / "b.mp4").write_bytes(b"x")

        monkeypatch.setattr("pipeline.utils.exif_scan.get_exif", _fake_photo_row)
        monkeypatch.setattr("pipeline.utils.exif_scan.get_video_meta", _fake_video_row)

        summary = scan_all(tmp_path)
        # 两个视频, 每个 10s → 20s
        assert summary["video_total_duration_s"] == 20.0
        # 每个 5120 KB → 10240 KB = 10 MB
        assert summary["video_total_size_mb"] == 10.0

    def test_csv_output(self, tmp_path, monkeypatch):
        (tmp_path / "a.jpg").write_bytes(b"x")

        monkeypatch.setattr("pipeline.utils.exif_scan.get_exif", _fake_photo_row)
        monkeypatch.setattr("pipeline.utils.exif_scan.get_video_meta", _fake_video_row)

        csv_path = tmp_path / "report.csv"
        scan_all(tmp_path, output=csv_path)
        assert csv_path.exists()
        with csv_path.open(encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["file"] == "a.jpg"
        assert rows[0]["make"] == "Canon"

    def test_empty_dir_returns_empty(self, tmp_path):
        summary = scan_all(tmp_path)
        assert summary["items"] == []
        assert summary["gps_count"] == 0

    def test_nonexistent_dir_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            scan_all(tmp_path / "nonexistent")

    def test_photo_and_video_exts_disjoint(self):
        """照片和视频扩展名集合不相交。"""
        assert PHOTO_EXTS.isdisjoint(VIDEO_EXTS)
