"""共享 fixture: 合成输入图像目录 (顶层照片 + 视频抽帧子目录)"""

import os
import tempfile
from pathlib import Path

import numpy as np
import pytest


def pytest_configure(config):
    """Keep content-addressed test paths below legacy Windows MAX_PATH."""

    if os.name != "nt" or config.option.basetemp is not None:
        return
    session_temp = tempfile.TemporaryDirectory(
        prefix="ntp-",
        dir=Path(__file__).resolve().anchor,
        ignore_cleanup_errors=True,
    )
    config._nantai_windows_session_temp = session_temp
    config.option.basetemp = session_temp.name


def pytest_unconfigure(config):
    session_temp = getattr(config, "_nantai_windows_session_temp", None)
    if session_temp is not None:
        session_temp.cleanup()


@pytest.fixture
def photos_dir(tmp_path):
    """4 张顶层照片 + 1 个视频会话 8 帧 (192x108 随机噪声 jpg)"""
    from PIL import Image

    root = tmp_path / "photos"
    (root / "vid_A").mkdir(parents=True)
    rng = np.random.default_rng(7)
    for i in range(4):
        Image.fromarray(
            rng.integers(60, 200, (108, 192, 3), dtype=np.uint8)
        ).save(root / f"IMG_{i:03d}.jpg")
    for i in range(8):
        Image.fromarray(
            rng.integers(40, 220, (108, 192, 3), dtype=np.uint8)
        ).save(root / "vid_A" / f"vid_A_frame_{i:06d}.jpg")
    return root


@pytest.fixture
def small_scene():
    """500 个随机高斯的场景"""
    from pipeline.gaussian_scene import GaussianScene

    rng = np.random.default_rng(0)
    return GaussianScene(
        xyz=rng.uniform(0, 10, (500, 3)),
        rgb=rng.uniform(0, 1, (500, 3)),
        opacity=rng.uniform(0.2, 1.0, 500),
        scale=rng.uniform(0.01, 0.5, (500, 3)),
    )
