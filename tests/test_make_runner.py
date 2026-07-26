"""make.py 跨平台任务运行器的测试。

make.py 是 Windows 上替代 GNU make 的主入口（README 推荐用法）。
覆盖 target 分发逻辑、UTF-8 子进程环境、clean 目录删除。
不真跑子进程命令（用 monkeypatch 替换 TARGETS 函数）。
"""

import importlib.util
import os
from pathlib import Path

import pytest


def _load_make_module():
    """从仓库根按文件路径加载 make.py（它不在 Python 包内）。"""
    spec = importlib.util.spec_from_file_location(
        "make_runner", Path(__file__).resolve().parent.parent / "make.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def make():
    return _load_make_module()


class TestMainDispatch:
    def test_no_args_prints_help(self, make, capsys):
        assert make.main(["make.py"]) == 0
        assert "targets:" in capsys.readouterr().out

    def test_help_arg(self, make, capsys):
        assert make.main(["make.py", "help"]) == 0
        assert "targets:" in capsys.readouterr().out

    def test_dash_h(self, make, capsys):
        assert make.main(["make.py", "-h"]) == 0
        assert "targets:" in capsys.readouterr().out

    def test_double_dash_help(self, make, capsys):
        assert make.main(["make.py", "--help"]) == 0
        assert "targets:" in capsys.readouterr().out

    def test_unknown_target_returns_2(self, make, capsys):
        assert make.main(["make.py", "bogus"]) == 2
        err = capsys.readouterr().err
        assert "unknown target" in err
        assert "bogus" in err

    def test_multiple_targets_run_in_order(self, make, monkeypatch):
        """多 target 按顺序执行。"""
        calls = []
        monkeypatch.setitem(make.TARGETS, "setup", lambda: calls.append("setup"))
        monkeypatch.setitem(make.TARGETS, "lint", lambda: calls.append("lint"))
        assert make.main(["make.py", "setup", "lint"]) == 0
        assert calls == ["setup", "lint"]

    def test_all_targets_have_callable(self, make):
        """TARGETS 字典每个值都是可调用对象。"""
        for name, fn in make.TARGETS.items():
            assert callable(fn), f"target {name!r} 不是可调用对象"

    def test_help_lists_all_targets(self, make, capsys):
        make.main(["make.py", "help"])
        out = capsys.readouterr().out
        for name in make.TARGETS:
            assert name in out
        assert "real-canary" in out
        assert "real-scene" in out


class TestRealSceneDispatch:
    def test_real_canary_binds_committed_source_and_subtarget(
        self,
        make,
        monkeypatch,
    ):
        calls = []
        monkeypatch.setattr(
            make,
            "run",
            lambda command, **_kwargs: calls.append(command),
        )

        assert make.main(
            [
                "make.py",
                "real-canary",
                "RUN_ID=canary-001",
                "fetch",
            ]
        ) == 0

        assert calls == [[
            make.PY,
            "scripts/real_scene.py",
            "fetch",
            "--source",
            "config/real-scene/nerfstudio-poster.json",
            "--run-id",
            "canary-001",
        ]]

    def test_real_scene_passes_cross_platform_key_value_options(
        self,
        make,
        monkeypatch,
    ):
        calls = []
        monkeypatch.setattr(
            make,
            "run",
            lambda command, **_kwargs: calls.append(command),
        )

        assert make.main(
            [
                "make.py",
                "real-scene",
                "SOURCE=private/source.json",
                "MEDIA_ROOT=/private/capture",
                "RIGHTS=.nantai-studio/private/rights.json",
                "POLICY=.nantai-studio/private/policy.json",
                "CONTROL_POINTS=.nantai-studio/private/points.json",
                "GEO_ORIGIN=31.2,121.5,4.0",
                "REMOTE_CONFIG=.nantai-studio/private/remote.json",
                "CHUNK_SIZE=37.5",
                "import",
            ]
        ) == 0

        command = calls[0]
        assert command[:3] == [
            make.PY,
            "scripts/real_scene.py",
            "import",
        ]
        assert command[command.index("--source") + 1] == (
            "private/source.json"
        )
        assert command[command.index("--geo-origin") + 1] == (
            "31.2,121.5,4.0"
        )
        assert command[command.index("--chunk-size") + 1] == "37.5"

    @pytest.mark.parametrize(
        "args",
        [
            ["real-scene", "fetch"],
            ["real-scene", "SOURCE=a", "SOURCE=b", "fetch"],
            ["real-scene", "SOURCE=a", "fetch", "sfm"],
            ["real-scene", "SOURCE=a", "UNKNOWN=x", "fetch"],
            ["real-canary", "SOURCE=a", "fetch"],
            ["real-canary", "RESUME=1", "RETRY=1", "fetch"],
        ],
    )
    def test_real_target_rejects_ambiguous_or_unsafe_args(
        self,
        make,
        capsys,
        args,
    ):
        assert make.main(["make.py", *args]) == 2
        assert "real-" in capsys.readouterr().err


class TestEnv:
    def test_utf8_forced(self, make):
        assert make.ENV["PYTHONUTF8"] == "1"
        assert make.ENV["PYTHONIOENCODING"] == "utf-8"

    def test_env_inherits_os_environ(self, make):
        """ENV 继承 os.environ（验证一个稳定 key），并追加 UTF-8 开关。"""
        # ENV 在模块加载时从 os.environ 快照; 只验证继承确实发生了
        # (不遍历全部 os.environ, 因为 pytest 可能在加载后注入新变量)。
        if "PATH" in os.environ:
            assert make.ENV.get("PATH") == os.environ["PATH"]


class TestPreviewReleaseTargets:
    def test_build_preview_uses_default_archive(self, make, monkeypatch):
        calls = []
        monkeypatch.delenv("DIST", raising=False)
        monkeypatch.delenv("ARCHIVE", raising=False)
        monkeypatch.setattr(make, "run", lambda command, **_kwargs: calls.append(command))

        make.build_preview()

        assert calls == [[
            make.PY,
            "scripts/build_preview_release.py",
            "--output",
            ".nantai-studio/releases/v1.0.0-preview.2/"
            "nantai-3d-v1.0.0-preview.2-runtime.zip",
        ]]

    def test_build_preview_honours_dist_or_archive(self, make, monkeypatch):
        calls = []
        monkeypatch.setenv("DIST", "custom-dist")
        monkeypatch.delenv("ARCHIVE", raising=False)
        monkeypatch.setattr(make, "run", lambda command, **_kwargs: calls.append(command))

        make.build_preview()
        monkeypatch.setenv("ARCHIVE", "explicit/runtime.zip")
        make.build_preview()

        assert calls == [
            [
                make.PY,
                "scripts/build_preview_release.py",
                "--output",
                "custom-dist/nantai-3d-v1.0.0-preview.2-runtime.zip",
            ],
            [
                make.PY,
                "scripts/build_preview_release.py",
                "--output",
                "explicit/runtime.zip",
            ],
        ]

    def test_verify_preview_uses_selected_archive(self, make, monkeypatch):
        calls = []
        monkeypatch.setenv("ARCHIVE", "candidate/runtime.zip")
        monkeypatch.setattr(make, "run", lambda command, **_kwargs: calls.append(command))

        make.verify_preview()

        assert calls == [[
            make.PY,
            "scripts/verify_preview_release.py",
            "candidate/runtime.zip",
        ]]


class TestClean:
    def test_clean_removes_existing_dirs(self, make, monkeypatch, tmp_path):
        """clean() 删除 ROOT 下指定目录（存在的才删）。"""
        monkeypatch.setattr(make, "ROOT", tmp_path)
        for name in ("corpus", "layouts", "verification/output"):
            d = tmp_path / name
            d.mkdir(parents=True)
            (d / "f.txt").write_text("x")
        make.clean()
        assert not (tmp_path / "corpus").exists()
        assert not (tmp_path / "layouts").exists()
        # clean 删 verification/output (整个 output 子目录), verification 本身可能残留
        assert not (tmp_path / "verification" / "output").exists()

    def test_clean_ignores_missing_dirs(self, make, monkeypatch, tmp_path):
        """clean() 对不存在的目录不报错。"""
        monkeypatch.setattr(make, "ROOT", tmp_path)
        make.clean()  # 不应 raise
