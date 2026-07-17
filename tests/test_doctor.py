"""环境体检 (scripts/doctor.py) 的契约测试。

核心被测契约是**诚实性**, 不是"能不能探到东西":
- 探测不到 (异常/超时) → status 必须是 unknown, 绝不因为"大概率没有"就报 missing;
- 探不到版本/SIFT 选项组 → 必须留 None, 绝不回落到一个好看的默认值;
- 能力小结里, 探测不确定的步骤必须进 unclear, 不能被算进 can 或 cannot。

全部探测经 Probes 注入, 因此**不依赖本机真装了 COLMAP/Brush/N 卡** —— 本文件在
任何机器上结论都一样。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import doctor

VALID = {"ok", "missing", "degraded", "unknown"}

# 实测采样自本机 third/colmap/bin/colmap.exe feature_extractor -h (COLMAP 4.1.0)
COLMAP_HELP = (
    "I20260717 14:20:06.914179 20564 option_manager.cc:1214] "
    "COLMAP 4.1.0 (Commit fa8e3b3 on 2026-06-26 without CUDA)\n"
    "  --FeatureExtraction.use_gpu arg (=1)\n"
)
# 旧版 COLMAP 的选项组命名
COLMAP_HELP_LEGACY = (
    "COLMAP 3.8 (Commit deadbeef on 2023-01-01 with CUDA)\n"
    "  --SiftExtraction.use_gpu arg (=1)\n"
)
BRUSH_VERSION = "brush-cli 0.3.0\n"
NVIDIA_SMI_CSV = "NVIDIA GeForce RTX 4090, 550.54.14\n"

_GB = 1024 ** 3


def _completed(cmd, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(list(cmd), returncode, stdout, stderr)


class FakeRegistry:
    """AssetRegistry 的最小替身: 只暴露 doctor 用到的 .doc.assets / .verified_sha256"""

    def __init__(self, entries: dict, verified: dict | None = None):
        self.doc = type("Doc", (), {"assets": entries})()
        self._verified = verified or {}

    def verified_sha256(self, asset_id: str):
        return self._verified.get(asset_id)


def _entry(sha: str = "a" * 64):
    return type("Entry", (), {"sha256": sha})()


def make_probes(
    *,
    colmap: bool = True,
    colmap_on_path: bool = False,
    brush: bool = True,
    nvidia: bool = True,
    colmap_help: str = COLMAP_HELP,
    missing_modules: tuple[str, ...] = (),
    run_raises: Exception | None = None,
    run_returncode: int = 0,
    import_raises: Exception | None = None,
    disk_free: int = 500 * _GB,
    disk_raises: Exception | None = None,
    registry=None,
    registry_raises: Exception | None = None,
    registry_exists: bool = True,
) -> doctor.Probes:
    """构造一组可控探测; 默认 = 万事俱备的理想机器。"""

    def which(name: str):
        if name == "nvidia-smi":
            return "C:/Windows/system32/nvidia-smi.exe" if nvidia else None
        if name == "colmap":
            return "C:/tools/colmap.exe" if (colmap and colmap_on_path) else None
        if name == "brush_app":
            return None
        return None

    def is_file(path: Path) -> bool:
        text = str(path).replace("\\", "/")
        if "colmap" in text:
            return colmap and not colmap_on_path
        if "brush" in text:
            return brush
        if text.endswith("registry.json"):
            return registry_exists
        return False

    def run(cmd):
        if run_raises is not None:
            raise run_raises
        joined = " ".join(str(c) for c in cmd)
        if "nvidia-smi" in joined:
            return _completed(cmd, stdout=NVIDIA_SMI_CSV, returncode=run_returncode)
        if "brush" in joined:
            return _completed(cmd, stdout=BRUSH_VERSION, returncode=run_returncode)
        # COLMAP 把 banner 打到 stderr —— 实测如此, 别只读 stdout
        return _completed(cmd, stderr=colmap_help, returncode=run_returncode)

    def import_module(name: str):
        if import_raises is not None:
            raise import_raises
        if name in missing_modules:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return type("Mod", (), {"__version__": "9.9.9"})()

    def disk_free_bytes(path: Path) -> int:
        if disk_raises is not None:
            raise disk_raises
        return disk_free

    def open_registry(path: Path):
        if registry_raises is not None:
            raise registry_raises
        return registry if registry is not None else FakeRegistry({"a": _entry()})

    return doctor.Probes(
        which=which,
        is_file=is_file,
        run=run,
        import_module=import_module,
        disk_free_bytes=disk_free_bytes,
        open_registry=open_registry,
    )


def _diagnose(**kw):
    verify_assets = kw.pop("verify_assets", False)
    return doctor.diagnose(root=Path("D:/repo"), probes=make_probes(**kw),
                           verify_assets=verify_assets)


class TestReportShape:
    def test_every_check_has_status_and_chinese_detail(self):
        report = _diagnose()
        assert set(report["checks"]) == {
            "colmap", "brush", "gpu", "python_deps", "assets_registry", "disk",
        }
        for name, check in report["checks"].items():
            assert check["status"] in VALID, name
            assert check["detail"].strip(), name

    def test_missing_checks_carry_a_remedy(self):
        report = _diagnose(colmap=False, brush=False, nvidia=False)
        for name in ("colmap", "brush", "gpu"):
            check = report["checks"][name]
            assert check["status"] == "missing", name
            assert check.get("remedy"), f"{name} 缺失却没告诉用户怎么补"

    def test_report_is_json_serializable(self):
        # --json 依赖它; 混进 Path/自定义对象会在 CLI 里才炸
        json.dumps(_diagnose(), ensure_ascii=False)


class TestColmap:
    def test_found_in_third_reports_version_and_sift_group(self):
        check = _diagnose()["checks"]["colmap"]
        assert check["status"] == "ok"
        assert check["version"] == "4.1.0"
        assert check["sift_group"] == "Feature"
        assert check["source"] == "third"

    def test_legacy_build_reports_sift_group(self):
        check = _diagnose(colmap_help=COLMAP_HELP_LEGACY)["checks"]["colmap"]
        assert check["sift_group"] == "Sift"
        assert check["version"] == "3.8"

    def test_falls_back_to_path_when_third_is_empty(self):
        check = _diagnose(colmap_on_path=True)["checks"]["colmap"]
        assert check["status"] == "ok"
        assert check["source"] == "PATH"

    def test_missing_says_mock_is_not_a_real_reconstruction(self):
        check = _diagnose(colmap=False)["checks"]["colmap"]
        assert check["status"] == "missing"
        assert "mock" in (check["detail"] + check["remedy"]).lower()
        assert "非真实重建" in check["detail"] + check["remedy"]

    def test_binary_present_but_unrunnable_is_degraded_not_ok(self):
        check = _diagnose(run_raises=OSError("exec format error"))["checks"]["colmap"]
        assert check["status"] == "degraded"

    def test_sift_group_stays_unknown_when_help_fails(self):
        # 反例护栏: pipeline.registration._colmap_sift_group 探测失败时兜底返回
        # 'Feature'(为了让重建能跑下去)。体检是报告, 不是兜底 —— 探不到就必须留 None。
        check = _diagnose(run_raises=subprocess.TimeoutExpired("colmap", 30))
        colmap = check["checks"]["colmap"]
        assert colmap["sift_group"] is None
        assert colmap["version"] is None
        assert "未知" in colmap["detail"]

    def test_cuda_flag_read_from_banner(self):
        assert _diagnose()["checks"]["colmap"]["cuda"] is False
        assert _diagnose(colmap_help=COLMAP_HELP_LEGACY)["checks"]["colmap"]["cuda"] is True

    def test_cuda_stays_unknown_when_banner_is_silent(self):
        check = _diagnose(colmap_help="COLMAP 9.9\n")["checks"]["colmap"]
        assert check["cuda"] is None


class TestBrush:
    def test_found_reports_version(self):
        check = _diagnose()["checks"]["brush"]
        assert check["status"] == "ok"
        assert check["version"] == "brush-cli 0.3.0"

    def test_missing_says_training_needs_cloud_gpu(self):
        check = _diagnose(brush=False)["checks"]["brush"]
        assert check["status"] == "missing"
        assert "云" in check["detail"] + check["remedy"]

    def test_version_probe_failure_keeps_binary_ok_but_version_unknown(self):
        check = _diagnose(run_raises=OSError("boom"))["checks"]["brush"]
        assert check["status"] == "degraded"
        assert check["version"] is None


class TestGpu:
    def test_nvidia_smi_ok_reports_gpu_name(self):
        check = _diagnose()["checks"]["gpu"]
        assert check["status"] == "ok"
        assert "RTX 4090" in check["detail"]

    def test_no_nvidia_smi_is_missing_and_names_the_evidence(self):
        # 本机现实 (Intel UHD 770)。nvidia-smi 随 NVIDIA 驱动安装 —— 探不到它,
        # 结论"无可用 CUDA 栈"是从证据推出来的, 不是猜的; detail 必须写明这条推理。
        check = _diagnose(nvidia=False)["checks"]["gpu"]
        assert check["status"] == "missing"
        assert "nvidia-smi" in check["detail"]
        assert "gsplat" in check["detail"] + check["remedy"]

    def test_nvidia_smi_present_but_failing_is_degraded(self):
        # 驱动装了但不响应 —— 既不是"有 GPU"也不是"没 GPU"
        check = _diagnose(run_returncode=9)["checks"]["gpu"]
        assert check["status"] == "degraded"

    def test_nvidia_smi_probe_exception_is_unknown(self):
        check = _diagnose(run_raises=subprocess.TimeoutExpired("nvidia-smi", 30))
        assert check["checks"]["gpu"]["status"] == "unknown"


class TestPythonDeps:
    def test_all_importable_is_ok(self):
        assert _diagnose()["checks"]["python_deps"]["status"] == "ok"

    def test_missing_required_is_missing_and_names_the_package(self):
        check = _diagnose(missing_modules=("plyfile",))["checks"]["python_deps"]
        assert check["status"] == "missing"
        assert "plyfile" in check["detail"]
        assert check["missing_required"] == ["plyfile"]

    def test_missing_optional_only_is_degraded(self):
        check = _diagnose(missing_modules=("trimesh",))["checks"]["python_deps"]
        assert check["status"] == "degraded"
        assert check["missing_required"] == []
        assert "trimesh" in check["missing_optional"]

    def test_non_import_error_is_unknown_not_missing(self):
        # 装了但坏了 (DLL 加载失败等) != 没装。别把"坏"报成"缺"。
        check = _diagnose(import_raises=RuntimeError("DLL load failed"))
        assert check["checks"]["python_deps"]["status"] == "unknown"

    def test_broken_install_importerror_is_unknown_not_missing(self):
        # Windows 上 "DLL load failed while importing cv2" 抛的是 ImportError 而非
        # ModuleNotFoundError。若按 ImportError 一律判 missing, 就会叫用户去 pip
        # install 一个**已经装了**的包 —— 一条看着合理却把人带偏的结论。
        check = _diagnose(import_raises=ImportError("DLL load failed while importing cv2"))
        deps = check["checks"]["python_deps"]
        assert deps["status"] == "unknown"
        assert deps["missing_required"] == []
        assert any("cv2" in item for item in deps["broken"])


class TestAssetsRegistry:
    def test_counts_entries(self):
        registry = FakeRegistry({"a": _entry(), "b": _entry()})
        check = _diagnose(registry=registry)["checks"]["assets_registry"]
        assert check["status"] == "ok"
        assert check["count"] == 2

    def test_absent_registry_is_missing_not_an_error(self):
        check = _diagnose(registry_exists=False)["checks"]["assets_registry"]
        assert check["status"] == "missing"

    def test_unparsable_registry_is_degraded(self):
        check = _diagnose(registry_raises=ValueError("bad json"))["checks"]["assets_registry"]
        assert check["status"] == "degraded"
        assert "bad json" in check["detail"]

    def test_sha_not_verified_by_default_and_says_so(self):
        # 默认不哈希 (大 PLY 很慢)。没校验就不准声称"已校验" —— 必须写明。
        check = _diagnose(registry=FakeRegistry({"a": _entry()}))["checks"]["assets_registry"]
        assert check["sha_mismatched"] is None
        assert "未校验" in check["detail"]

    def test_sha_mismatch_is_degraded_when_verifying(self):
        registry = FakeRegistry({"a": _entry(), "b": _entry()},
                                verified={"a": "a" * 64})  # b 校验不过
        check = _diagnose(registry=registry, verify_assets=True)["checks"]["assets_registry"]
        assert check["status"] == "degraded"
        assert check["sha_mismatched"] == ["b"]

    def test_all_sha_verified_is_ok(self):
        registry = FakeRegistry({"a": _entry()}, verified={"a": "a" * 64})
        check = _diagnose(registry=registry, verify_assets=True)["checks"]["assets_registry"]
        assert check["status"] == "ok"
        assert check["sha_mismatched"] == []

    def test_entry_without_declared_sha_is_flagged(self):
        registry = FakeRegistry({"a": _entry(sha="")})
        check = _diagnose(registry=registry)["checks"]["assets_registry"]
        assert check["status"] == "degraded"
        assert check["without_sha"] == ["a"]


class TestDisk:
    def test_plenty_is_ok(self):
        check = _diagnose(disk_free=500 * _GB)["checks"]["disk"]
        assert check["status"] == "ok"
        assert check["free_gb"] == pytest.approx(500, abs=0.5)

    def test_low_space_is_degraded(self):
        check = _diagnose(disk_free=3 * _GB)["checks"]["disk"]
        assert check["status"] == "degraded"

    def test_probe_failure_is_unknown(self):
        check = _diagnose(disk_raises=OSError("no such drive"))["checks"]["disk"]
        assert check["status"] == "unknown"


class TestSummary:
    def _text(self, summary):
        return " ".join(summary["can"] + summary["cannot"] + summary["unclear"])

    def test_fully_equipped_machine_can_do_sfm_and_local_training(self):
        summary = _diagnose()["summary"]
        assert any("SfM" in item for item in summary["can"])
        assert summary["unclear"] == []

    def test_without_colmap_real_sfm_moves_to_cannot(self):
        summary = _diagnose(colmap=False)["summary"]
        assert not any("SfM" in item for item in summary["can"])
        assert any("SfM" in item for item in summary["cannot"])

    def test_without_gpu_and_brush_training_must_go_cloud(self):
        summary = _diagnose(nvidia=False, brush=False)["summary"]
        assert any("训练" in item and "云" in item for item in summary["cannot"])

    def test_no_cuda_but_brush_present_keeps_training_local_with_caveat(self):
        # 本机现实: 无 CUDA + 有 Brush → 能本机训练, 但必须标"受限"
        summary = _diagnose(nvidia=False)["summary"]
        training = [item for item in summary["can"] if "训练" in item]
        assert training and "受限" in training[0]

    def test_unknown_probe_lands_in_unclear_not_can_or_cannot(self):
        # 最关键的一条: 探测失败时 doctor 不许替用户下结论
        summary = _diagnose(run_raises=subprocess.TimeoutExpired("x", 1))["summary"]
        assert summary["unclear"], "探测不确定却没有任何 unclear 条目"
        assert not any("SfM" in item for item in summary["can"] + summary["cannot"])

    def test_deps_broken_makes_pipeline_steps_unclear(self):
        summary = _diagnose(import_raises=RuntimeError("DLL load failed"))["summary"]
        assert any("摄取" in item for item in summary["unclear"])


class TestCli:
    def test_json_mode_emits_parsable_report(self, capsys):
        code = doctor.main(["--json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert set(payload["checks"]) >= {"colmap", "brush", "gpu"}

    def test_human_report_is_chinese_and_mentions_every_check(self, capsys):
        assert doctor.main([]) == 0
        out = capsys.readouterr().out
        for word in ("环境体检", "COLMAP", "Brush", "GPU", "能力小结"):
            assert word in out

    def test_exit_code_is_zero_even_when_everything_is_missing(self, monkeypatch, capsys):
        # 语义: 退出码报的是"体检本身跑成没跑成", 不是"这台机器合不合格"。
        # "缺 COLMAP" 是体检的**结论**, 不是体检的失败。
        monkeypatch.setattr(doctor, "_default_probes",
                            lambda: make_probes(colmap=False, brush=False, nvidia=False))
        assert doctor.main([]) == 0
        out = capsys.readouterr().out
        assert "[缺失]" in out          # 缺件如实报出来了
        assert "非真实重建" in out       # 且说清了后果, 不是丢个红叉了事
