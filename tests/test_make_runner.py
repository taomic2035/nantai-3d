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
