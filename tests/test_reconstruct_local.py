"""scripts/reconstruct_local.py 的断点续跑 (--resume) + tee 进度测试。

核心是 fail-closed：--resume 只在**指纹逐字节相同**时跳过阶段。指纹变了 / 缺失 /
状态文件损坏 / 产物不在 → 重跑该阶段及其所有下游。绝不因为"输出文件存在"就跳过。

不真跑 COLMAP/Brush（要几小时）：把 reconstruct_local.run 换成假实现，按子命令
伪造产物并记录调用；_find/_colmap_group 也桩掉（探测真实二进制会失败）。
_select_best_colmap_model / _count_registered_images 不桩 —— 让它们跑在假 run
写出的真实 sparse/0/images.bin 上。
"""
import json
import os
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import reconstruct_local as rl  # noqa: E402


def _stage_of(cmd: list[str]) -> str | None:
    """从命令行反推它属于哪个阶段（假 run 与断言共用的唯一映射）。

    **按 token 匹配，不做整个 joined 串的子串搜索**：pytest 的 tmp_path 含测试名，
    测试名可能含 `_matcher`/`mapper` 等关键字，会通过 `str(ws)` 渗进 joined 串，
    导致 brush_argv 被误判成 colmap。COLMAP 子命令固定在 cmd[1]（紧跟二进制路径）。
    """
    if any("pipeline.ingest" in t for t in cmd):
        return "frames"
    # COLMAP subcommands sit at cmd[1] (right after the binary path).
    if len(cmd) > 1 and (cmd[1] in ("feature_extractor", "mapper")
                         or cmd[1].endswith("_matcher")):
        return "colmap"
    if "--total-steps" in cmd:  # Brush flag — exact token, not substring
        return "brush"
    if any("normalize_ply_quats" in t or "prepare_import" in t for t in cmd):
        return "prepare"
    if any("pipeline.reconstruct" in t for t in cmd):
        return "import"
    return None


class FakeRun:
    """替身 run：记录每条命令，并伪造该阶段的产物（不跑真二进制）。"""

    def __init__(self, ws: Path, web: Path):
        self.ws, self.web = ws, web
        self.calls: list[list[str]] = []
        self.fail_stage: str | None = None   # 该阶段被调用时抛 SystemExit（模拟 Brush 挂掉）

    def __call__(self, cmd, *, log=None, tee=False):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        stage = _stage_of(cmd)
        if stage == self.fail_stage:
            raise SystemExit(f"假失败: {stage}")
        # Dispatch on stage + cmd[1] (token-level), not on substring of the
        # joined command — paths under tmp_path may contain stage keywords
        # (e.g. test name with "_matcher"), which would route brush_argv into
        # the colmap branch and skip writing trained.ply.
        if stage == "colmap":
            if len(cmd) > 1 and cmd[1] == "feature_extractor":
                (self.ws / "colmap.db").write_bytes(b"fake-db")
            elif len(cmd) > 1 and cmd[1] == "mapper":
                model = self.ws / "sparse" / "0"
                model.mkdir(parents=True, exist_ok=True)
                # _count_registered_images 读头 8 字节 uint64 → 装作注册了 12 张
                (model / "images.bin").write_bytes(struct.pack("<Q", 12) + b"\x00" * 8)
            # matcher subcommand: no artifact to write (db already exists)
        elif stage == "brush":
            (self.ws / "trained.ply").write_bytes(b"fake-ply")
        elif stage == "prepare":
            (self.ws / "registration.json").write_text("{}", encoding="utf-8")
            (self.ws / "splat-input.json").write_text("{}", encoding="utf-8")
        elif stage == "import":
            (self.ws / "out").mkdir(parents=True, exist_ok=True)
            self.web.mkdir(parents=True, exist_ok=True)

    @property
    def stages(self) -> set[str]:
        return {s for s in (_stage_of(c) for c in self.calls) if s}

    def reset(self) -> None:
        self.calls.clear()


@pytest.fixture
def env(tmp_path, photos_dir, monkeypatch):
    """返回 (调用 main 的函数, FakeRun, ws, photos)。"""
    ws, web = tmp_path / "ws", tmp_path / "web"
    fake_exe = tmp_path / "bin"
    fake_exe.mkdir()
    for name in ("colmap.exe", "brush_app.exe"):
        (fake_exe / name).write_bytes(b"fake-binary")

    fake = FakeRun(ws, web)
    monkeypatch.setattr(rl, "run", fake)
    monkeypatch.setattr(rl, "_find", lambda name, *c: str(fake_exe / f"{name}.exe"))
    monkeypatch.setattr(rl, "_colmap_group", lambda colmap: "Feature")

    def call(*extra: str, photos: Path | None = None) -> int:
        fake.reset()
        src = photos or photos_dir
        return rl.main([str(src), "--work", str(ws), "--web", str(web), *extra])

    return call, fake, ws, photos_dir


ALL_STAGES = {"colmap", "brush", "prepare", "import"}
STATE = ".stage_state.json"


def _state(ws: Path) -> dict:
    return json.loads((ws / STATE).read_text(encoding="utf-8"))


# --- 向后兼容：不给 --resume 时行为与今天完全一致 ---------------------------------

def test_without_resume_runs_all_stages_even_with_valid_state(env):
    call, fake, ws, _ = env
    assert call() == 0
    assert fake.stages == ALL_STAGES
    # 第二次仍不给 --resume：即使状态文件里指纹完全匹配，也必须全跑
    assert call() == 0
    assert fake.stages == ALL_STAGES


# --- 指纹相同 → 跳过 ------------------------------------------------------------

def test_resume_skips_every_stage_when_nothing_changed(env, capsys):
    call, fake, ws, _ = env
    call()
    capsys.readouterr()
    assert call("--resume") == 0
    assert fake.stages == set(), "输入与参数都没变，不该重跑任何阶段"
    assert "跳过" in capsys.readouterr().out


def test_resume_prints_honest_fingerprint_limitation(env, capsys):
    """廉价指纹的局限必须对用户可见（不能假装是密码学校验）。"""
    call, fake, ws, _ = env
    call()
    capsys.readouterr()
    call("--resume")
    out = capsys.readouterr().out
    assert "mtime" in out and "内容" in out


# --- 指纹变了 → 不跳过（fail-closed 的命脉）--------------------------------------

def test_changed_photo_reruns_colmap_and_all_downstream(env):
    call, fake, ws, photos = env
    call()
    (photos / "IMG_000.jpg").write_bytes(b"a completely different photo")
    assert call("--resume") == 0
    assert fake.stages == ALL_STAGES, "照片换了还复用旧位姿=谎称来自这批照片"


def test_changed_photo_refreshes_ws_images_copy(env):
    """ws/images 必须与产出 sparse/0 的那批照片一致，否则 Brush 训在旧图上。"""
    call, fake, ws, photos = env
    call()
    new = b"a completely different photo"
    (photos / "IMG_000.jpg").write_bytes(new)
    call("--resume")
    assert (ws / "images" / "IMG_000.jpg").read_bytes() == new


def test_added_photo_reruns_colmap(env):
    call, fake, ws, photos = env
    call()
    (photos / "IMG_999.jpg").write_bytes(b"newly added photo")
    call("--resume")
    assert "colmap" in fake.stages


@pytest.mark.parametrize("ext", [".tif", ".tiff", ".bmp", ".webp", ".heic"])
def test_two_different_photo_sets_never_share_a_fingerprint(env, tmp_path, ext):
    """回归：指纹曾只认 .jpg/.jpeg/.png，别的格式一律数不到 → _photos_fp 恒为 []
    → 两批**彻底不同**的照片指纹完全相同 → --resume 静默复用上一批的位姿，产出一个
    谎称来自这批照片的重建。COLMAP 经 FreeImage 确实读得了 TIFF/BMP/WebP。"""
    call, fake, ws, _ = env
    a, b = tmp_path / f"a{ext[1:]}", tmp_path / f"b{ext[1:]}"
    for d, blob in ((a, b"AAA"), (b, b"BBBBBBBBBBBB")):
        d.mkdir()
        for i in range(3):  # 指纹只 stat 不解码, 内容是不是合法图不影响本测试
            (d / f"shot_{i}{ext}").write_bytes(blob * (i + 1))
    call(photos=a)
    fp_a = _state(ws)["stages"]["colmap"]["fingerprint"]
    assert call("--resume", photos=b) == 0
    assert _state(ws)["stages"]["colmap"]["fingerprint"] != fp_a, f"{ext}: 两批不同照片指纹撞了"
    assert fake.stages == ALL_STAGES, f"{ext}: 照片换了一批却复用旧位姿"


def test_fingerprint_covers_every_shared_photo_format(tmp_path):
    """指纹的扩展名集合必须**覆盖**全仓库共享的那份。方向很关键：过度包含只是多重跑
    几次（保守/fail-closed），漏掉才是 fail-open（会撒谎）。"""
    from pipeline.ingest_manifest import PHOTO_SOURCE_SUFFIXES

    assert PHOTO_SOURCE_SUFFIXES <= rl.FINGERPRINT_SUFFIXES
    d = tmp_path / "p"
    d.mkdir()
    for i, ext in enumerate(sorted(PHOTO_SOURCE_SUFFIXES)):
        (d / f"x{ext}").write_bytes(b"z" * (i + 1))
    assert len(rl._photos_fp(d)) == len(PHOTO_SOURCE_SUFFIXES)


# --- 结构性护栏：没证据 → 永不跳过（挡的是整类 bug，不只这次的扩展名落差）---------

def test_empty_photo_fingerprint_never_skips_even_when_state_matches(env, tmp_path):
    """空的照片集指纹**在原理上**证明不了"输入未变"（两批完全不同的照片都得到同一个
    空清单）→ 必须 fail-closed。将来谁再改扩展名清单、或 rglob 因权限漏掉文件，这道
    门都还在。"""
    call, fake, ws, _ = env
    d = tmp_path / "unknown_fmt"
    d.mkdir()
    for i in range(3):
        (d / f"shot_{i}.gif").write_bytes(b"g" * (i + 1))
    call(photos=d)
    assert call("--resume", photos=d) == 0  # 同一批照片、同样参数，照样不许跳
    assert "colmap" in fake.stages, "没观察到任何输入证据时不许跳过"


def test_empty_fingerprint_reason_is_visible_to_user_and_in_state(env, tmp_path, capsys):
    call, fake, ws, _ = env
    d = tmp_path / "unknown_fmt"
    d.mkdir()
    (d / "shot.gif").write_bytes(b"g")
    call(photos=d)
    assert "空清单" in _state(ws)["stages"]["colmap"]["unprovable"], "状态文件是信任根，要如实标注"
    capsys.readouterr()
    call("--resume", photos=d)
    assert "空清单" in capsys.readouterr().out


def test_colmap_param_change_reruns_colmap_and_downstream(env):
    call, fake, ws, _ = env
    call()
    assert call("--resume", "--colmap-gpu") == 0
    assert fake.stages == ALL_STAGES


def test_brush_param_change_reruns_brush_and_downstream_but_not_colmap(env):
    """上游没变 → 复用；上游重跑 → 下游必须跟着跑（不能留下上下游不一致的重建）。"""
    call, fake, ws, _ = env
    call()
    assert call("--resume", "--steps", "9000") == 0
    assert fake.stages == {"brush", "prepare", "import"}
    assert "colmap" not in fake.stages, "COLMAP 输入没变，几小时不该白跑"


def test_max_res_change_reruns_brush_and_downstream(env):
    call, fake, ws, _ = env
    call()
    call("--resume", "--max-res", "512")
    assert fake.stages == {"brush", "prepare", "import"}


def test_import_param_change_reruns_import_only(env):
    call, fake, ws, _ = env
    call()
    call("--resume", "--chunk-size-m", "20")
    assert fake.stages == {"import"}


def test_binary_swap_reruns_colmap(env, tmp_path):
    """换了 COLMAP 二进制 → 结果可能不同 → 不许复用旧位姿。"""
    call, fake, ws, _ = env
    call()
    (tmp_path / "bin" / "colmap.exe").write_bytes(b"a different colmap build")
    call("--resume")
    assert fake.stages == ALL_STAGES


# --- 状态缺失/损坏 → fail-closed 重跑，不炸 -------------------------------------

@pytest.mark.parametrize("blob", [
    "{ not json at all",
    '{"version": 1}',                                    # 缺 stages
    '{"version": 99, "stages": {}}',                     # 版本不认识
    '{"version": 1, "stages": {"colmap": "not-a-dict"}}',
    '{"version": 1, "stages": {"colmap": {"finished_at": "x"}}}',  # 缺 fingerprint
    "",
])
def test_corrupt_state_file_reruns_everything_without_crashing(env, blob):
    call, fake, ws, _ = env
    call()
    (ws / STATE).write_text(blob, encoding="utf-8")
    assert call("--resume") == 0
    assert fake.stages == ALL_STAGES


def test_missing_state_file_reruns_everything(env):
    call, fake, ws, _ = env
    call()
    (ws / STATE).unlink()
    assert call("--resume") == 0
    assert fake.stages == ALL_STAGES


def test_corrupt_state_explains_why_it_cannot_be_reused(env, capsys):
    call, fake, ws, _ = env
    call()
    (ws / STATE).write_text("{ garbage", encoding="utf-8")
    capsys.readouterr()
    call("--resume")
    assert "损坏" in capsys.readouterr().out


# --- 产物不在 → 重跑（指纹匹配也不行）------------------------------------------

def test_missing_output_reruns_stage_even_though_fingerprint_matches(env):
    call, fake, ws, _ = env
    call()
    (ws / "trained.ply").unlink()
    call("--resume")
    assert fake.stages == {"brush", "prepare", "import"}


def test_empty_colmap_model_reruns_colmap(env):
    """sparse/0 在、但没有已注册影像 → 产物不可信，不许跳过。"""
    call, fake, ws, _ = env
    call()
    (ws / "sparse" / "0" / "images.bin").write_bytes(struct.pack("<Q", 0))
    call("--resume")
    assert "colmap" in fake.stages


# --- 崩溃安全：上游重跑后中断，不能留下"下游已完成"的假记录 ---------------------

def test_stage_rerun_invalidates_downstream_state_before_running(env):
    call, fake, ws, photos = env
    call()
    assert set(_state(ws)["stages"]) == ALL_STAGES
    # 换照片 → COLMAP 必须重跑；让 Brush 挂掉（显存不足的真实场景）
    (photos / "IMG_000.jpg").write_bytes(b"a completely different photo")
    fake.fail_stage = "brush"
    with pytest.raises(SystemExit):
        call("--resume")
    stages = set(_state(ws)["stages"])
    assert stages == {"colmap"}, f"上游重跑后下游记录必须先被抹掉，实际留下 {stages}"


def test_resume_after_brush_failure_skips_colmap(env):
    """头牌场景：COLMAP 跑了几小时、Brush 显存不足挂掉 → 重跑不该重做 COLMAP。"""
    call, fake, ws, _ = env
    fake.fail_stage = "brush"
    with pytest.raises(SystemExit):
        call()
    fake.fail_stage = None
    assert call("--resume") == 0
    assert fake.stages == {"brush", "prepare", "import"}


# --- 状态文件字节约定 -----------------------------------------------------------

def test_state_file_is_lf_and_content_addressed(env):
    call, fake, ws, _ = env
    call()
    raw = (ws / STATE).read_bytes()
    assert b"\r\n" not in raw, "状态文件用 LF（跨平台字节可复现，本仓库惯例）"
    entry = _state(ws)["stages"]["colmap"]
    assert len(entry["fingerprint"]) == 64 and int(entry["fingerprint"], 16) >= 0


def test_fingerprints_are_stable_across_identical_runs(env):
    call, fake, ws, _ = env
    call()
    before = _state(ws)["stages"]["colmap"]["fingerprint"]
    call()
    assert _state(ws)["stages"]["colmap"]["fingerprint"] == before


# --- tee：既到终端也到日志 ------------------------------------------------------
# 这些测试跑真子进程（毫秒级），只用 ASCII —— 子进程的控制台编码不是本次改动的变量。

def test_tee_writes_full_output_to_log_and_shows_progress(tmp_path, capsys):
    log = tmp_path / "t.log"
    rl.run([sys.executable, "-c", "print('FIRST'); print('LAST')"], log=log, tee=True)
    raw = log.read_bytes()
    assert b"FIRST" in raw and b"LAST" in raw, "日志必须是全量"
    assert "LAST" in capsys.readouterr().out, "终端必须看得到进展（否则用户只能盯几小时空屏）"


def test_tee_shows_carriage_return_progress_lines(tmp_path, capsys):
    """COLMAP 用 \\r 原地刷进度：不能因为没有 \\n 就一个字都不显示。"""
    log = tmp_path / "t.log"
    rl.run([sys.executable, "-c",
            r"import sys; sys.stdout.write('P 1/2\rP 2/2\r\n')"], log=log, tee=True)
    assert "P " in capsys.readouterr().out


def test_tee_log_keeps_raw_subprocess_bytes(tmp_path):
    """日志字节与不开 tee 时一致：不替第三方二进制猜编码。"""
    plain, teed = tmp_path / "a.log", tmp_path / "b.log"
    code = r"import sys; sys.stdout.buffer.write(b'\xff\xfe raw bytes\n')"
    rl.run([sys.executable, "-c", code], log=plain)
    rl.run([sys.executable, "-c", code], log=teed, tee=True)
    assert teed.read_bytes() == plain.read_bytes() == b"\xff\xfe raw bytes\n"


def test_tee_failure_still_raises_with_log_tail(tmp_path):
    log = tmp_path / "t.log"
    with pytest.raises(SystemExit, match="UNIQUE-ERROR"):
        rl.run([sys.executable, "-c", "import sys; print('UNIQUE-ERROR'); sys.exit(3)"],
               log=log, tee=True)


def test_non_tee_logging_unchanged(tmp_path, capsys):
    """不开 tee 时与今天一致：输出只进日志，不进终端。"""
    log = tmp_path / "t.log"
    rl.run([sys.executable, "-c", "print('LOG-ONLY')"], log=log)
    assert b"LOG-ONLY" in log.read_bytes()
    body = capsys.readouterr().out.split("\n", 1)[1]  # 首行是 run 回显的命令本身
    assert "LOG-ONLY" not in body


# ============================================================
# _find + _colmap_group: binary discovery + GPU flag heuristic
# ============================================================

class TestFind:
    """_find resolves binaries via file-exists check then shutil.which."""

    def test_find_returns_candidate_path_when_file_exists(self, tmp_path):
        fake_bin = tmp_path / "colmap.exe"
        fake_bin.write_bytes(b"fake")
        result = rl._find("colmap", fake_bin)
        assert result == str(fake_bin)

    def test_find_falls_back_to_shutil_which(self, monkeypatch, tmp_path):
        nonexistent = tmp_path / "missing.exe"
        monkeypatch.setattr(rl.shutil, "which", lambda name: "/usr/bin/colmap")
        result = rl._find("colmap", nonexistent)
        assert result == "/usr/bin/colmap"

    def test_find_raises_system_exit_when_not_found(self, monkeypatch, tmp_path):
        nonexistent = tmp_path / "missing.exe"
        monkeypatch.setattr(rl.shutil, "which", lambda name: None)
        with pytest.raises(SystemExit, match="找不到 colmap"):
            rl._find("colmap", nonexistent)


class TestColmapGroup:
    """_colmap_group probes COLMAP feature_extractor help to pick 'Sift' vs 'Feature'."""

    def test_returns_sift_when_only_legacy_use_gpu_present(self, monkeypatch):
        class _Fake:
            def __init__(self, stdout, stderr=""):
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = 0

        # Legacy COLMAP: only SiftExtraction.use_gpu, no FeatureExtraction.use_gpu
        monkeypatch.setattr(
            rl.subprocess, "run",
            lambda *a, **k: _Fake(
                stdout="SiftExtraction.use_gpu\n  Whether to use GPU"))
        assert rl._colmap_group("fake-colmap") == "Sift"

    def test_returns_feature_when_both_use_gpu_present(self, monkeypatch):
        class _Fake:
            def __init__(self, stdout, stderr=""):
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = 0

        monkeypatch.setattr(
            rl.subprocess, "run",
            lambda *a, **k: _Fake(
                stdout="FeatureExtraction.use_gpu\nSiftExtraction.use_gpu"))
        assert rl._colmap_group("fake-colmap") == "Feature"

    def test_returns_feature_when_neither_present(self, monkeypatch):
        class _Fake:
            def __init__(self, stdout, stderr=""):
                self.stdout = stdout
                self.stderr = stderr
                self.returncode = 0

        monkeypatch.setattr(
            rl.subprocess, "run",
            lambda *a, **k: _Fake(stdout="some other help text"))
        assert rl._colmap_group("fake-colmap") == "Feature"

    def test_returns_feature_on_subprocess_error(self, monkeypatch):
        def _raise(*a, **k):
            raise OSError("colmap not found")

        monkeypatch.setattr(rl.subprocess, "run", _raise)
        # Fail-closed: default to 'Feature' (current COLMAP convention)
        assert rl._colmap_group("fake-colmap") == "Feature"

    def test_returns_feature_on_timeout(self, monkeypatch):
        def _raise(*a, **k):
            raise rl.subprocess.TimeoutExpired(cmd=a, timeout=30)

        monkeypatch.setattr(rl.subprocess, "run", _raise)
        assert rl._colmap_group("fake-colmap") == "Feature"


# ============================================================
# --precomputed-colmap: byte-bound skip of COLMAP stage (P7a)
#
# Reviewer-Codex-030 finding P0: production reconstruct_local.py --resume
# computes its own digest, so externally written stage fingerprints like
# 'p7_reused_from_p5b' silently fail and COLMAP reruns. P7a adds a *supported*
# fail-closed precomputed-COLMAP input boundary: COLMAP never runs, the colmap
# stage fingerprint is the real SHA-256 of source bytes (not a fake string),
# and the only "rerun" path is byte-precise re-copy from source.
# ============================================================

import shutil  # noqa: E402  (late import keeps top-of-file minimal)

PRECOMPUTED_REQUIRED = ("cameras.bin", "images.bin", "points3D.bin")
PRECOMPUTED_OPTIONAL = ("frames.bin", "rigs.bin", "project.ini")

# Photo extensions mirrored from pipeline.ingest_manifest.PHOTO_SOURCE_SUFFIXES
# (kept local to the test to avoid an extra cross-module import in the helper).
_FAKE_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".heic"}


def _write_fake_cameras_bin(path: Path, *, non_finite: bool = False) -> None:
    """Write a minimal-but-real COLMAP cameras.bin (1 PINHOLE camera).

    PINHOLE model=1, params=[focal_x, focal_y, cx, cy]. With non_finite=True
    the first param is NaN so P7a-6 finite check fails.
    """
    buf = bytearray()
    buf += struct.pack("<Q", 1)          # num_cameras
    buf += struct.pack("<I", 1)          # camera_id
    buf += struct.pack("<i", 1)          # model = PINHOLE
    buf += struct.pack("<Q", 192)        # width
    buf += struct.pack("<Q", 108)        # height
    # COLMAP 4.x 不存储 num_params；由 model=1 (PINHOLE) 查表得 nparams=4
    if non_finite:
        buf += struct.pack("<4d", float("nan"), 100.0, 96.0, 54.0)
    else:
        buf += struct.pack("<4d", 100.0, 100.0, 96.0, 54.0)
    path.write_bytes(bytes(buf))


def _write_fake_images_bin(path: Path, image_names: list[str], *,
                           header_count: int | None = None) -> None:
    """Write a real-format COLMAP images.bin.

    Each image: image_id(uint32) + qvec(4*float64, identity) + tvec(3*float64,
    zero) + camera_id(uint32=1) + null-terminated name + num_points2D(uint64=0).
    header_count overrides the uint64 num_reg_images (default = len(image_names));
    used to inject count-mismatch for RED tests.
    """
    buf = bytearray()
    buf += struct.pack("<Q", header_count if header_count is not None
                       else len(image_names))
    for i, name in enumerate(image_names):
        buf += struct.pack("<I", i + 1)              # image_id
        buf += struct.pack("<4d", 1.0, 0.0, 0.0, 0.0)  # qw,qx,qy,qz
        buf += struct.pack("<3d", 0.0, 0.0, 0.0)       # tx,ty,tz
        buf += struct.pack("<I", 1)                    # camera_id
        buf += name.encode("utf-8") + b"\x00"          # null-terminated
        buf += struct.pack("<Q", 0)                    # num_points2D
    path.write_bytes(bytes(buf))


def _photo_names_in(photos: Path) -> list[str]:
    """Sorted relative posix paths of every supported photo under photos/."""
    names = []
    for p in sorted(photos.rglob("*")):
        if p.is_file() and p.suffix.lower() in _FAKE_PHOTO_EXTS:
            names.append(p.relative_to(photos).as_posix())
    return names


def _make_fake_precomputed(colmap_ws: Path, photos: Path, *,
                           n_registered: int | None = None,
                           image_names_override: list[str] | None = None,
                           duplicate_name: bool = False,
                           phantom_name: str | None = None,
                           non_finite_camera: bool = False,
                           header_count: int | None = None) -> None:
    """Build a fake precomputed COLMAP workspace with REAL binary format.

    Default writes semantically-valid cameras.bin/images.bin so P7a-6
    semantic validation passes. Use kwargs to inject corruption for RED tests:

    - n_registered: kept for backward compat; when set AND image_names is
      derived from photos, the header count is set to n_registered while the
      actual records equal len(image_names). To test count mismatch pass
      n_registered != len(photos).
    - image_names_override: explicit image_name list (default = derive from
      photos, so names line up with files on disk).
    - duplicate_name: append a duplicate of the first image_name (ghost track).
    - phantom_name: append an image_name that has no matching file in photos
      (phantom image).
    - non_finite_camera: cameras.bin first param becomes NaN.
    - header_count: explicit override of the images.bin header count.
    """
    sparse_0 = colmap_ws / "sparse" / "0"
    sparse_0.mkdir(parents=True, exist_ok=True)

    if image_names_override is not None:
        names = list(image_names_override)
    else:
        names = _photo_names_in(photos)
    if duplicate_name and names:
        names = names + [names[0]]
    if phantom_name is not None:
        names = names + [phantom_name]

    if header_count is not None:
        hc = header_count
    elif n_registered is not None:
        # Backward-compat: n_registered overrides the header count. The actual
        # records are len(names); if n_registered != len(names) the parser
        # will detect a count mismatch.
        hc = n_registered
    else:
        hc = len(names)

    _write_fake_images_bin(sparse_0 / "images.bin", names, header_count=hc)
    _write_fake_cameras_bin(sparse_0 / "cameras.bin", non_finite=non_finite_camera)
    (sparse_0 / "points3D.bin").write_bytes(struct.pack("<Q", 0))
    (sparse_0 / "project.ini").write_text("[test]\n", encoding="utf-8")
    (sparse_0 / "frames.bin").write_bytes(b"fake-frames")
    (sparse_0 / "rigs.bin").write_bytes(b"fake-rigs")
    img_dir = colmap_ws / "images"
    if img_dir.exists():
        shutil.rmtree(img_dir)
    shutil.copytree(photos, img_dir)
    (colmap_ws / "colmap.db").write_bytes(b"fake-db")


def _colmap_subprocess_cmds(fake) -> list[list[str]]:
    """Filter fake.calls to only COLMAP subprocess commands.

    Uses _stage_of (token-level) so paths under tmp_path that happen to
    contain `_matcher`/`mapper` (e.g. from the test name) don't cause
    false positives.
    """
    return [c for c in fake.calls if _stage_of(c) == "colmap"]


class TestPrecomputedColmapBoundary:
    """--precomputed-colmap: COLMAP never runs; bytes bind the fingerprint."""

    def test_skips_colmap_subprocesses_but_runs_downstream(self, env, tmp_path,
                                                            photos_dir):
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)

        call("--precomputed-colmap", str(precomp))

        assert _colmap_subprocess_cmds(fake) == [], \
            "precomputed mode must never run feature_extractor/matcher/mapper"
        assert {"brush", "prepare", "import"} <= fake.stages, \
            "downstream stages must still run"

    def test_fingerprint_is_real_sha256_not_fake_string(self, env, tmp_path,
                                                         photos_dir):
        """Reviewer-Codex-030 P0: prior P7 wrote 'p7_reused_from_p5b' as the
        colmap fingerprint. The fingerprint must be a real digest of source
        bytes so any byte change forces downstream rerun."""
        call, fake, ws, photos = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos)
        call("--precomputed-colmap", str(precomp))

        entry = _state(ws)["stages"]["colmap"]
        fp = entry["fingerprint"]
        assert len(fp) == 64
        int(fp, 16)  # raises if not hex
        assert "reused" not in fp.lower()
        assert "p7_" not in fp.lower()
        assert "unprovable" not in entry, \
            "precomputed bytes ARE observed; fingerprint must not be unprovable"

    def test_fingerprint_binds_all_required_bin_shas(self, env, tmp_path,
                                                      photos_dir):
        """Fingerprint payload must include SHA-256 of cameras.bin, images.bin,
        points3D.bin so a byte change in any of them changes the digest."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))

        digest_a = _state(ws)["stages"]["colmap"]["fingerprint"]
        # Mutate one required bin: flip a byte in cameras.bin width field
        # (offset 16, uint64). Stays finite/valid so P7a-6 semantic check
        # passes, but SHA changes → fingerprint must change.
        cam_path = precomp / "sparse" / "0" / "cameras.bin"
        cam_buf = bytearray(cam_path.read_bytes())
        cam_buf[16] ^= 0x01
        cam_path.write_bytes(bytes(cam_buf))
        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))
        digest_b = _state(ws)["stages"]["colmap"]["fingerprint"]
        assert digest_a != digest_b, \
            "cameras.bin byte change must alter colmap fingerprint"

    def test_source_byte_change_triggers_recopy_not_colmap(self, env, tmp_path,
                                                            photos_dir):
        """If source sparse/0 bytes change, ws is re-copied (NOT rerun COLMAP)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))

        # Change source bytes: flip cameras.bin width byte (stays finite/valid
        # so P7a-6 semantic validation passes, but SHA changes → fingerprint
        # changes → byte-exact re-copy, never COLMAP).
        cam_path = precomp / "sparse" / "0" / "cameras.bin"
        cam_buf = bytearray(cam_path.read_bytes())
        cam_buf[16] ^= 0x01
        cam_path.write_bytes(bytes(cam_buf))
        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))

        assert _colmap_subprocess_cmds(fake) == [], \
            "COLMAP must never run in precomputed mode, even on byte change"
        ws_bytes = (ws / "sparse" / "0" / "cameras.bin").read_bytes()
        src_bytes = (precomp / "sparse" / "0" / "cameras.bin").read_bytes()
        assert ws_bytes == src_bytes, \
            "source byte change must trigger byte-exact re-copy into ws"

    def test_resume_skips_when_source_and_ws_match(self, env, tmp_path,
                                                    capsys, photos_dir):
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        capsys.readouterr()

        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))
        assert "colmap" not in fake.stages
        assert _colmap_subprocess_cmds(fake) == []
        assert "跳过" in capsys.readouterr().out

    def test_ws_corruption_triggers_recopy(self, env, tmp_path, photos_dir):
        """If ws/sparse/0/images.bin is corrupted (bytes differ from source),
        --resume must detect mismatch and re-copy from source (NOT run COLMAP)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))

        # Corrupt ws copy
        (ws / "sparse" / "0" / "images.bin").write_bytes(b"corrupted")
        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []
        ws_bytes = (ws / "sparse" / "0" / "images.bin").read_bytes()
        src_bytes = (precomp / "sparse" / "0" / "images.bin").read_bytes()
        assert ws_bytes == src_bytes, \
            "ws corruption must trigger byte-exact re-copy from source"

    def test_missing_required_bin_fails_closed(self, env, tmp_path, photos_dir):
        """Missing cameras.bin / images.bin / points3D.bin -> SystemExit,
        and COLMAP must not have run before the failure."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        for required in PRECOMPUTED_REQUIRED:
            (precomp / "sparse" / "0" / required).unlink()
            with pytest.raises(SystemExit, match=required):
                call("--precomputed-colmap", str(precomp))
            assert _colmap_subprocess_cmds(fake) == [], \
                f"missing {required}: COLMAP must not run"
            fake.reset()
            _make_fake_precomputed(precomp, photos_dir)

    def test_missing_sparse_dir_fails_closed(self, env, tmp_path, photos_dir):
        call, fake, ws, _ = env
        precomp = tmp_path / "no_sparse"
        precomp.mkdir()
        # has images/ but no sparse/0/
        shutil.copytree(photos_dir, precomp / "images")
        with pytest.raises(SystemExit, match="sparse"):
            call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []

    def test_missing_images_dir_fails_closed(self, env, tmp_path, photos_dir):
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        shutil.rmtree(precomp / "images")
        with pytest.raises(SystemExit, match="images"):
            call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []

    def test_photos_mismatch_fails_closed(self, env, tmp_path, photos_dir):
        """--photos different from <precomp>/images/ -> SystemExit (would lie
        about which photos produced the sparse model)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        # Build a different photo set: same names, different bytes
        other = tmp_path / "other_photos"
        other.mkdir()
        for p in photos_dir.iterdir():
            if p.is_file():
                (other / p.name).write_bytes(b"different " + p.read_bytes())
        with pytest.raises(SystemExit, match="不一致"):
            call("--precomputed-colmap", str(precomp), photos=other)
        assert _colmap_subprocess_cmds(fake) == []

    def test_not_a_directory_fails_closed(self, env, tmp_path, photos_dir):
        call, fake, ws, _ = env
        not_dir = tmp_path / "file.txt"
        not_dir.write_text("not a dir")
        with pytest.raises(SystemExit, match="--precomputed-colmap"):
            call("--precomputed-colmap", str(not_dir))

    def test_optional_bins_bound_when_present(self, env, tmp_path, photos_dir):
        """frames.bin, rigs.bin, project.ini are optional but, if present,
        their SHAs must be in the fingerprint."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)  # writes optional bins
        call("--precomputed-colmap", str(precomp))
        digest_a = _state(ws)["stages"]["colmap"]["fingerprint"]

        # Mutate an optional bin
        (precomp / "sparse" / "0" / "frames.bin").write_bytes(b"changed")
        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))
        digest_b = _state(ws)["stages"]["colmap"]["fingerprint"]
        assert digest_a != digest_b, \
            "frames.bin (optional) byte change must still alter fingerprint"

    def test_colmap_db_bound_when_present(self, env, tmp_path, photos_dir):
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        digest_a = _state(ws)["stages"]["colmap"]["fingerprint"]

        (precomp / "colmap.db").write_bytes(b"different-db")
        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))
        digest_b = _state(ws)["stages"]["colmap"]["fingerprint"]
        assert digest_a != digest_b, \
            "colmap.db byte change must alter colmap fingerprint"

    def test_optional_bin_absent_does_not_break_run(self, env, tmp_path,
                                                     photos_dir):
        """If optional bins are absent, run still succeeds and fingerprint
        excludes them (no KeyError)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        for opt in PRECOMPUTED_OPTIONAL:
            (precomp / "sparse" / "0" / opt).unlink()
        # Should succeed
        assert call("--precomputed-colmap", str(precomp)) == 0
        assert _colmap_subprocess_cmds(fake) == []

    def test_ws_images_matches_source_after_copy(self, env, tmp_path,
                                                  photos_dir):
        """ws/images/ must be a byte-exact copy of <precomp>/images/ so Brush
        trains on the same photos that produced sparse/0."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))

        for src in (precomp / "images").iterdir():
            if not src.is_file():
                continue
            assert (ws / "images" / src.name).read_bytes() == src.read_bytes(), \
                f"ws/images/{src.name} must match <precomp>/images/{src.name}"

    def test_fingerprint_stable_across_identical_runs(self, env, tmp_path,
                                                       photos_dir):
        """Same source, same params -> identical fingerprint across runs."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        fp_a = _state(ws)["stages"]["colmap"]["fingerprint"]
        call("--precomputed-colmap", str(precomp))
        fp_b = _state(ws)["stages"]["colmap"]["fingerprint"]
        assert fp_a == fp_b

    def test_state_file_lf_newline_preserved(self, env, tmp_path, photos_dir):
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        raw = (ws / STATE).read_bytes()
        assert b"\r\n" not in raw, \
            "precomputed mode must keep LF-only state file (cross-platform)"

    def test_source_photo_byte_change_alters_fingerprint(self, env, tmp_path,
                                                          photos_dir):
        """REVIEW-CODEX-030 P7a-1: per-photo SHA-256 binding. The cheap
        fingerprint (path/size/mtime) cannot detect a same-size same-mtime
        byte swap in source images/ — Brush would silently train on photos
        that didn't produce sparse/0. Bind per-photo SHA-256 so any byte
        change alters the digest.

        Tamper the same photo in BOTH precomp/images/ and --photos (so the
        source/caller SHA comparison still passes), and assert the fingerprint
        changed because the photo bytes changed."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        digest_a = _state(ws)["stages"]["colmap"]["fingerprint"]

        # Flip one byte in the same photo in both source and --photos dir,
        # keeping size + mtime identical (defeats the cheap fp).
        name = next(p.name for p in (precomp / "images").glob("IMG_*.jpg"))
        for d in (precomp / "images", photos_dir):
            photo = d / name
            data = bytearray(photo.read_bytes())
            data[0] ^= 0xFF
            st = photo.stat()
            photo.write_bytes(bytes(data))
            os.utime(photo, ns=(st.st_atime_ns, st.st_mtime_ns))
        # Sanity: cheap fingerprint (path/size/mtime) is unchanged by the swap.
        assert rl._photos_fp(precomp / "images") == rl._photos_fp(photos_dir)

        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))
        digest_b = _state(ws)["stages"]["colmap"]["fingerprint"]
        assert digest_a != digest_b, \
            "source photo byte change (same size/mtime) must alter fingerprint"

    def test_ws_photo_byte_change_triggers_recopy(self, env, tmp_path,
                                                   photos_dir):
        """REVIEW-CODEX-030 P7a-1: ws/images photo bytes tampered (same
        name/size/mtime) must be detected by post-copy validation → re-copy,
        not silent skip. The cheap fp can't see this; SHA-256 can."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))

        # Tamper ws copy: flip a byte, keep size + mtime.
        photo = next((ws / "images").glob("IMG_*.jpg"))
        data = bytearray(photo.read_bytes())
        data[0] ^= 0xFF
        st = photo.stat()
        photo.write_bytes(bytes(data))
        os.utime(photo, ns=(st.st_atime_ns, st.st_mtime_ns))

        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == [], "COLMAP must never run"
        # ws must be re-copied from source (untampered).
        src_bytes = (precomp / "images" / photo.name).read_bytes()
        assert photo.read_bytes() == src_bytes, \
            "ws photo byte tamper (same size/mtime) must trigger re-copy"

    def test_work_equals_precomputed_rejected(self, env, tmp_path, photos_dir):
        """REVIEW-CODEX-030 P7a-5: --work == --precomputed-colmap →
        rmtree(ws/images) would delete source images. Reject before any rmtree."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        web = tmp_path / "web2"
        with pytest.raises(SystemExit, match="重叠|overlap"):
            rl.main([str(photos_dir), "--work", str(ws), "--web", str(web),
                     "--precomputed-colmap", str(ws)])
        assert _colmap_subprocess_cmds(fake) == []

    def test_photos_inside_work_rejected(self, env, tmp_path, photos_dir):
        """REVIEW-CODEX-030 P7a-5: --photos inside --work → rmtree(ws/images)
        could delete source photos. Reject before any rmtree."""
        call, fake, ws, _ = env
        photos_in_work = ws / "images"
        photos_in_work.mkdir(parents=True, exist_ok=True)
        for p in photos_dir.iterdir():
            if p.is_file():
                shutil.copy2(p, photos_in_work / p.name)
        web = tmp_path / "web2"
        with pytest.raises(SystemExit, match="重叠|overlap"):
            rl.main([str(photos_in_work), "--work", str(ws), "--web", str(web)])
        assert _colmap_subprocess_cmds(fake) == []

    def test_work_inside_precomputed_rejected(self, env, tmp_path, photos_dir):
        """REVIEW-CODEX-030 P7a-5: --work inside --precomputed-colmap →
        rmtree could delete source. Reject before any rmtree."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        work_in_precomp = precomp / "sub_ws"
        web = tmp_path / "web2"
        with pytest.raises(SystemExit, match="重叠|overlap"):
            rl.main([str(photos_dir), "--work", str(work_in_precomp),
                     "--web", str(web),
                     "--precomputed-colmap", str(precomp)])
        assert _colmap_subprocess_cmds(fake) == []

    def test_precomputed_extras_bind_caller_argv(self, env, tmp_path,
                                                  photos_dir):
        """REVIEW-CODEX-030 P7a-3: caller_argv must be in precomputed
        colmap extras (not only in brush extras) so the exact caller
        that consumed the source is auditable at the colmap stage."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        entry = _state(ws)["stages"]["colmap"]
        assert "caller_argv" in entry, \
            "precomputed colmap extras must bind caller_argv for audit"
        assert "--precomputed-colmap" in entry["caller_argv"]
        assert str(precomp) in entry["caller_argv"]
        # Must not leak pytest's sys.argv.
        assert not any("pytest" in t for t in entry["caller_argv"])

    def test_precomputed_binary_sha_in_fingerprint(self, env, tmp_path,
                                                     photos_dir):
        """REVIEW-CODEX-030 P7a-3: binary identity in the precomputed
        fingerprint must be SHA-256, not (name/size/mtime). A same-size
        same-mtime byte swap of the COLMAP binary must alter the digest."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        digest_a = _state(ws)["stages"]["colmap"]["fingerprint"]
        # Tamper the fake COLMAP binary: flip a byte, keep size + mtime.
        colmap_exe = tmp_path / "bin" / "colmap.exe"
        data = bytearray(colmap_exe.read_bytes())
        data[0] ^= 0xFF
        st = colmap_exe.stat()
        colmap_exe.write_bytes(bytes(data))
        os.utime(colmap_exe, ns=(st.st_atime_ns, st.st_mtime_ns))
        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))
        digest_b = _state(ws)["stages"]["colmap"]["fingerprint"]
        assert digest_a != digest_b, \
            "binary byte change (same size/mtime) must alter fingerprint"

    def test_precomputed_caller_flag_in_fingerprint(self, env, tmp_path,
                                                     photos_dir):
        """REVIEW-CODEX-030 P7a-3: caller_argv is part of the precomputed
        colmap fingerprint, so changing a caller flag (--sequential)
        alters the digest (the exact caller that consumed the source is
        bound, not just the source bytes)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        digest_a = _state(ws)["stages"]["colmap"]["fingerprint"]
        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp), "--sequential")
        digest_b = _state(ws)["stages"]["colmap"]["fingerprint"]
        assert digest_a != digest_b, \
            "caller flag change (--sequential) must alter precomputed fingerprint"


class TestPrecomputedSemanticValidation:
    """REVIEW-CODEX-030 P7a-6: sparse/0 bytes-bound is not enough. A valid
    recovered camera track must also be semantically consistent:
    - images.bin parses fully (header count == actual records);
    - every image_name resolves to a file in photos/;
    - no duplicate image_name (ghost track);
    - cameras.bin params are all finite (no NaN/Inf).

    Each RED case must SystemExit before COLMAP ever runs."""

    def test_valid_sparse_passes_semantic_validation(self, env, tmp_path,
                                                      photos_dir):
        """Baseline: a well-formed sparse/0 + matching photos passes both
        source-side and ws-side semantic validation and reaches downstream."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []
        assert {"brush", "prepare", "import"} <= fake.stages

    def test_count_mismatch_header_larger_than_records(self, env, tmp_path,
                                                         photos_dir):
        """images.bin header claims 99 images but only 12 records exist →
        parser runs off the end → SystemExit. Equating the first 8 bytes
        with a valid track is exactly what P7a-6 forbids."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir, header_count=99)
        with pytest.raises(SystemExit, match="images.bin|语义"):
            call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == [], \
            "semantic failure must fail before any COLMAP subprocess"

    def test_count_mismatch_header_smaller_than_records(self, env, tmp_path,
                                                         photos_dir):
        """Header claims 3 but 12 records exist → parser stops early,
        silently dropping 9 images. The dropped records are unbound, so
        this must also fail-closed (the cheap header-only check would have
        silently accepted a partial track)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir, header_count=3)
        with pytest.raises(SystemExit, match="images.bin|语义"):
            call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []

    def test_phantom_image_name_not_in_photos(self, env, tmp_path, photos_dir):
        """images.bin references 'GHOST_NO_FILE.jpg' which does not exist in
        photos/ → Brush would train on a pose for a non-existent photo."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir,
                               phantom_name="GHOST_NO_FILE.jpg")
        with pytest.raises(SystemExit, match="不存在|phantom|images"):
            call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []

    def test_duplicate_image_name_ghost_track(self, env, tmp_path, photos_dir):
        """Two records with the same image_name → same photo counted twice,
        which corrupts the recovered track."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir, duplicate_name=True)
        with pytest.raises(SystemExit, match="重复|duplicate"):
            call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []

    def test_non_finite_camera_param_rejected(self, env, tmp_path, photos_dir):
        """cameras.bin with NaN focal length → pose unusable numerically."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir, non_finite_camera=True)
        with pytest.raises(SystemExit, match="非有限|NaN|cameras"):
            call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []

    def test_truncated_images_bin_rejected(self, env, tmp_path, photos_dir):
        """A images.bin truncated mid-record (e.g. copy interrupted) must
        not be accepted as a valid track."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        img_path = precomp / "sparse" / "0" / "images.bin"
        full = img_path.read_bytes()
        # Truncate to header + half a record (corrupt)
        img_path.write_bytes(full[:24])
        with pytest.raises(SystemExit, match="images.bin|语义"):
            call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []

    def test_truncated_cameras_bin_rejected(self, env, tmp_path, photos_dir):
        """A cameras.bin too short to hold even num_cameras → fail-closed."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        (precomp / "sparse" / "0" / "cameras.bin").write_bytes(b"\x00\x00")
        with pytest.raises(SystemExit, match="cameras.bin|语义"):
            call("--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == []

    def test_ws_semantic_corruption_triggers_fail_closed(self, env, tmp_path,
                                                          photos_dir):
        """If ws/sparse/0/images.bin is byte-identical to source (SHA matches)
        but semantically corrupted by an external actor post-copy, the
        ws-side semantic check must still catch it. Simulate by manually
        corrupting ws images.bin SHA AND semantic in one shot: replace with
        a header-only blob that parses as count=0 (passes SHA only if source
        also changed). To stay realistic, change source identically first
        so _validate_ws_precomputed passes, then check semantics still fire."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))

        # Corrupt BOTH source and ws images.bin identically (so byte SHA
        # matches between them) into a semantically-invalid header-only blob.
        bad = struct.pack("<Q", 5)  # claims 5 images, no records
        (precomp / "sparse" / "0" / "images.bin").write_bytes(bad)
        (ws / "sparse" / "0" / "images.bin").write_bytes(bad)
        fake.reset()
        with pytest.raises(SystemExit, match="images.bin|语义"):
            call("--resume", "--precomputed-colmap", str(precomp))
        assert _colmap_subprocess_cmds(fake) == [], \
            "ws-side semantic check must fire after byte validation passes"


class TestColmapExtrasBoundary:
    """REVIEW-CODEX-030 P1 (P6c): COLMAP normal branch must bind real
    subprocess argv + matcher subcommand + UTC + log SHA in extras, so
    matcher identity is measured (not inferred from log text)."""

    def test_colmap_extras_bound_on_normal_run(self, env):
        call, fake, ws, _ = env
        call()
        entry = _state(ws)["stages"]["colmap"]
        assert "extras" not in entry  # extras are flattened into entry directly
        for key in ("colmap_matcher_subcommand",
                    "colmap_feature_extractor_argv",
                    "colmap_matcher_argv",
                    "colmap_mapper_argv",
                    "colmap_started_at",
                    "colmap_finished_at",
                    "colmap_returncode",
                    "colmap_log_sha256",
                    "colmap_binary_sha256",
                    "colmap_registered_images",
                    "colmap_images_input_count",
                    "caller_argv"):
            assert key in entry, f"colmap extras missing {key}"

    def test_matcher_subcommand_matches_argv(self, env):
        """matcher subcommand in argv[1] must equal colmap_matcher_subcommand."""
        call, fake, ws, _ = env
        call()
        entry = _state(ws)["stages"]["colmap"]
        matcher = entry["colmap_matcher_subcommand"]
        matcher_argv = entry["colmap_matcher_argv"]
        assert matcher_argv[1] == matcher, \
            "argv[1] must be the matcher subcommand name"
        assert matcher in ("sequential_matcher", "exhaustive_matcher")

    def test_small_photo_set_uses_exhaustive_matcher(self, env, photos_dir):
        """n <= 400 and not ordered -> exhaustive_matcher (not inferred)."""
        call, fake, ws, _ = env
        call()  # photos_dir has 3 photos -> exhaustive
        entry = _state(ws)["stages"]["colmap"]
        assert entry["colmap_matcher_subcommand"] == "exhaustive_matcher"
        assert entry["colmap_matcher_argv"][1] == "exhaustive_matcher"

    def test_ordered_video_uses_sequential_matcher(self, env, tmp_path):
        """REVIEW-CODEX-030 P1: P6b inferred sequential from pair count; the
        real evidence is the argv, which we now bind."""
        call, fake, ws, _ = env
        # Create a tiny video file so is_video() returns True
        video = tmp_path / "fake.mp4"
        video.write_bytes(b"\x00" * 1024)  # content doesn't matter; is_video
        # checks extension only (see pipeline.ingest.is_video)
        # But reconstruct_local needs frames from it; FakeRun will fake the
        # frames stage. Actually, frames stage runs pipeline.ingest which
        # calls extract_video_frames — that needs a real video.
        # Skip this test path: instead test ordered=True via --sequential flag.
        call("--sequential")
        entry = _state(ws)["stages"]["colmap"]
        assert entry["colmap_matcher_subcommand"] == "sequential_matcher"
        assert entry["colmap_matcher_argv"][1] == "sequential_matcher"

    def test_colmap_log_sha_is_real_sha256(self, env):
        call, fake, ws, _ = env
        call()
        entry = _state(ws)["stages"]["colmap"]
        log_sha = entry["colmap_log_sha256"]
        assert len(log_sha) == 64
        int(log_sha, 16)
        # Re-verify: log file SHA must match
        clog = ws / "colmap.log"
        if clog.is_file():
            import hashlib
            assert hashlib.sha256(clog.read_bytes()).hexdigest() == log_sha

    def test_colmap_argv_includes_image_path_and_db(self, env, photos_dir):
        call, fake, ws, _ = env
        call()
        entry = _state(ws)["stages"]["colmap"]
        feat_argv = entry["colmap_feature_extractor_argv"]
        matcher_argv = entry["colmap_matcher_argv"]
        mapper_argv = entry["colmap_mapper_argv"]
        assert "--database_path" in feat_argv
        assert "--image_path" in feat_argv
        assert "--database_path" in matcher_argv
        assert "--database_path" in mapper_argv
        assert "--image_path" in mapper_argv
        assert "--output_path" in mapper_argv

    def test_caller_argv_bound_in_colmap_extras(self, env, photos_dir):
        """caller_argv must be the argv actually passed to main(), not
        pytest's sys.argv. When invoked via rl.main([...]) in tests, argv[0]
        is the photos positional arg (not the script name); when invoked from
        the CLI, sys.argv[0] is the script path. Either way, the real argv
        is bound — not the test runner's."""
        call, fake, ws, _ = env
        call("--sequential")
        entry = _state(ws)["stages"]["colmap"]
        caller = entry["caller_argv"]
        assert "--sequential" in caller
        assert str(photos_dir) in caller  # photos positional arg is bound
        # Must NOT leak pytest's sys.argv (would contain "pytest" / "-m"):
        assert not any("pytest" in t for t in caller), \
            "caller_argv must be reconstruct_local's argv, not pytest's sys.argv"


class TestBrushExportSnapshot:
    """REVIEW-CODEX-030 P0 #5: Brush export PLY SHA must bind an immutable
    snapshot (trained.brush-export.ply), because prepare stage's
    normalize_ply_quats.py overwrites trained.ply in place."""

    def test_brush_export_snapshot_created(self, env):
        call, fake, ws, _ = env
        call()
        assert (ws / "trained.brush-export.ply").is_file(), \
            "Brush export must snapshot to trained.brush-export.ply"

    def test_brush_extras_bind_snapshot_not_trained(self, env):
        call, fake, ws, _ = env
        call()
        entry = _state(ws)["stages"]["brush"]
        assert entry["brush_export_ply_path"] == "trained.brush-export.ply"
        snap_sha = entry["brush_export_ply_sha256"]
        assert len(snap_sha) == 64
        int(snap_sha, 16)
        # Snapshot SHA must match re-hash
        import hashlib
        actual = hashlib.sha256(
            (ws / "trained.brush-export.ply").read_bytes()).hexdigest()
        assert actual == snap_sha, "snapshot SHA must be re-verifiable"

    def test_snapshot_survives_trained_ply_mutation(self, env):
        """If trained.ply is overwritten after Brush (simulating
        normalize_ply_quats.py), the snapshot must remain byte-stable."""
        call, fake, ws, _ = env
        call()
        snap_before = (ws / "trained.brush-export.ply").read_bytes()
        snap_sha_before = _state(ws)["stages"]["brush"]["brush_export_ply_sha256"]
        # Simulate prepare stage overwrite
        (ws / "trained.ply").write_bytes(b"different content after normalize")
        snap_after = (ws / "trained.brush-export.ply").read_bytes()
        assert snap_before == snap_after, \
            "snapshot must be independent of trained.ply mutations"
        import hashlib
        assert hashlib.sha256(snap_after).hexdigest() == snap_sha_before

    def test_no_trained_ply_sha256_field_collision(self, env):
        """Old field name trained_ply_sha256 must not exist (it bound a
        mutable file); only brush_export_ply_sha256 is valid."""
        call, fake, ws, _ = env
        call()
        entry = _state(ws)["stages"]["brush"]
        assert "trained_ply_sha256" not in entry, \
            "old field bound mutable trained.ply; must use brush_export_ply_sha256"
        assert "trained_ply_size_bytes" not in entry

    def test_brush_log_sha_re_verifiable(self, env):
        call, fake, ws, _ = env
        call()
        entry = _state(ws)["stages"]["brush"]
        log_sha = entry["brush_log_sha256"]
        if (ws / "brush.log").is_file():
            import hashlib
            actual = hashlib.sha256((ws / "brush.log").read_bytes()).hexdigest()
            assert actual == log_sha

    def test_brush_extras_have_utc_timestamps(self, env):
        call, fake, ws, _ = env
        call()
        entry = _state(ws)["stages"]["brush"]
        start = entry["brush_started_at"]
        end = entry["brush_finished_at"]
        assert start.endswith("+00:00") or start.endswith("Z")
        assert end.endswith("+00:00") or end.endswith("Z")
        assert start <= end


class TestSourceManifestMaterialization:
    """REVIEW-CODEX-030 P7a-2: the fingerprint digest is just a sha256 string.
    A reviewer cannot recover the payload (which files, which SHAs, which argv)
    from the digest alone. The complete source manifest must be materialized
    into a content-addressed machine report (independent JSON file named by
    its own SHA-256) so review can independently verify the payload."""

    def _manifest_files(self, ws: Path) -> list[Path]:
        return sorted(ws.glob("source_manifest_*.json"))

    def test_source_manifest_file_written(self, env, tmp_path, photos_dir):
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        files = self._manifest_files(ws)
        assert len(files) == 1, f"expected exactly 1 source manifest, got {files}"

    def test_manifest_contains_all_source_hashes_and_intent(self, env, tmp_path,
                                                            photos_dir):
        """The materialized report must contain the recoverable payload:
        all source file SHAs, photos_sha256, caller_argv, colmap_binary_sha256,
        manifest_sha256 (self-address), mode, source_root."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        report = json.loads(self._manifest_files(ws)[0].read_text("utf-8"))
        for key in ("cameras.bin_sha256", "images.bin_sha256",
                    "points3D.bin_sha256", "photos_sha256",
                    "caller_argv", "colmap_binary_sha256",
                    "manifest_sha256", "mode", "source_root",
                    "materialized_at_utc"):
            assert key in report, f"manifest missing recoverable field: {key}"
        assert report["mode"] == "precomputed"
        assert "--precomputed-colmap" in report["caller_argv"]

    def test_manifest_sha_matches_filename(self, env, tmp_path, photos_dir):
        """Content-addressed: the manifest_sha256 field must equal the SHA
        suffix in the filename, and re-hashing the payload must reproduce it."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        path = self._manifest_files(ws)[0]
        manifest_sha = path.stem.removeprefix("source_manifest_")
        report = json.loads(path.read_text("utf-8"))
        assert report["manifest_sha256"] == manifest_sha
        # Re-derive: payload = report minus manifest_sha256 + materialized_at_utc
        payload = {k: v for k, v in report.items()
                   if k not in ("manifest_sha256", "materialized_at_utc")}
        assert rl._digest(payload) == manifest_sha, \
            "re-hashing the payload must reproduce the content-address"

    def test_manifest_idempotent_same_source(self, env, tmp_path, photos_dir):
        """Re-running with the same source → no error, same file, same SHA
        (write-once semantics, not a fresh file each run)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        files_a = self._manifest_files(ws)
        sha_a = json.loads(files_a[0].read_text("utf-8"))["manifest_sha256"]
        # Second run (--resume, same source) must not error or create a 2nd file
        call("--resume", "--precomputed-colmap", str(precomp))
        files_b = self._manifest_files(ws)
        assert len(files_b) == 1, "idempotent re-run must not create duplicate"
        sha_b = json.loads(files_b[0].read_text("utf-8"))["manifest_sha256"]
        assert sha_a == sha_b

    def test_manifest_sha_recorded_in_colmap_extras(self, env, tmp_path,
                                                    photos_dir):
        """The materialized manifest SHA must be cross-referenced in
        .stage_state.json colmap entry so a reviewer can link the digest to
        the recoverable report. Extras are merged into the stage entry
        directly (not nested under an 'extras' key)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        colmap_entry = _state(ws)["stages"]["colmap"]
        recorded_sha = colmap_entry["source_manifest_sha256"]
        report_sha = json.loads(
            self._manifest_files(ws)[0].read_text("utf-8"))["manifest_sha256"]
        assert recorded_sha == report_sha, \
            "colmap entry must cross-reference the materialized manifest SHA"

    def test_manifest_write_once_rejects_hash_conflict(self, env, tmp_path,
                                                      photos_dir):
        """If a file with the target name already exists but its
        manifest_sha256 differs (hash collision / tampering), the caller must
        fail-closed instead of silently overwriting the audit evidence."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        path = self._manifest_files(ws)[0]
        # Corrupt the manifest_sha256 field in the existing file
        report = json.loads(path.read_text("utf-8"))
        report["manifest_sha256"] = "0" * 64
        path.write_text(json.dumps(report), encoding="utf-8")
        with pytest.raises(SystemExit, match="冲突|conflict|manifest"):
            call("--resume", "--precomputed-colmap", str(precomp))

    def test_different_source_produces_different_manifest(self, env, tmp_path,
                                                          photos_dir):
        """Changing the source bytes must produce a different manifest SHA
        and a separate file (so both old and new evidence are preserved)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        files_a = self._manifest_files(ws)
        sha_a = json.loads(files_a[0].read_text("utf-8"))["manifest_sha256"]
        # Change a source sparse file byte → different source → different SHA
        (precomp / "sparse" / "0" / "points3D.bin").write_bytes(b"changed")
        call("--resume", "--precomputed-colmap", str(precomp))
        files_b = self._manifest_files(ws)
        assert len(files_b) == 2, "new source must produce a new manifest file"
        sha_b = json.loads(
            [f for f in files_b if f != files_a[0]][0].read_text("utf-8")
            )["manifest_sha256"]
        assert sha_a != sha_b, \
            "different source bytes must yield different manifest SHA"


# ---------------------------------------------------------------------------
# HANDOFF-GLM-008 Task 2 item 2 — real COLMAP binary fixture
# ---------------------------------------------------------------------------
# The fake-camera/image writers above and the production parser are authored
# by the same lane. A parser that agrees with its sibling writer but
# disagrees with real COLMAP would pass every fake-fixture test. These tests
# load a fixture produced by the pinned local COLMAP 4.1.0 (Commit fa8e3b3,
# `third/colmap/bin/colmap.exe`) from independently authored text sources, so
# a parser misaligned with the official binary format fails here.

_REAL_COLMAP_FIXTURE_DIR = ROOT / "tests" / "fixtures" / "colmap" / "bin"


def _maybe_skip_if_fixture_missing():
    """Skip tests if the real COLMAP fixture was not regenerated locally.

    The .bin files are committed to the repo, so this should only fire on a
    shallow checkout or someone manually deleting them.
    """
    if not (_REAL_COLMAP_FIXTURE_DIR / "cameras.bin").is_file():
        pytest.skip(
            "real COLMAP fixture tests/fixtures/colmap/bin/cameras.bin missing; "
            "regenerate via the command in tests/fixtures/colmap/README.md")


class TestRealColmapFixture:
    """Parser must accept the byte-exact output of `colmap model_converter`
    on hand-written text sources covering all 12 accepted camera models."""

    def test_parse_real_cameras_bin_all_twelve_models(self):
        _maybe_skip_if_fixture_missing()
        cams = rl._parse_colmap_cameras_bin(
            _REAL_COLMAP_FIXTURE_DIR / "cameras.bin")
        # 12 cameras, one per accepted model id 0..11
        assert len(cams) == 12
        # Build a {model_id: camera_dict} map. Camera ids in the fixture are
        # 1..12 but the model id is what matters for the parameter-count
        # contract.
        expected_nparams = {0: 3, 1: 4, 2: 4, 3: 5, 4: 8, 5: 8, 6: 12,
                             7: 5, 8: 4, 9: 5, 10: 12, 11: 16}
        seen_models = {cam["model"] for cam in cams}
        assert seen_models == set(expected_nparams.keys()), \
            f"missing models: {set(expected_nparams) - seen_models}; " \
            f"extra: {seen_models - set(expected_nparams)}"
        for cam in cams:
            model = cam["model"]
            assert len(cam["params"]) == expected_nparams[model], \
                f"model={model}: expected {expected_nparams[model]} params, " \
                f"got {len(cam['params'])}"
            # Fixture was authored with width=1024, height=768, focal=1024
            assert cam["width"] == 1024
            assert cam["height"] == 768
            # Focal is params[0] for every COLMAP model; must be positive.
            assert cam["params"][0] == 1024.0
            # Finite check (NaN/Inf must already be rejected, but assert here
            # too so a regression that lets non-finite through is caught).
            import math
            for p in cam["params"]:
                assert math.isfinite(p), \
                    f"model={model} has non-finite param {p}"

    def test_parse_real_images_bin_and_cross_reference_cameras(self):
        _maybe_skip_if_fixture_missing()
        cams = rl._parse_colmap_cameras_bin(
            _REAL_COLMAP_FIXTURE_DIR / "cameras.bin")
        imgs = rl._parse_colmap_images_bin(
            _REAL_COLMAP_FIXTURE_DIR / "images.bin")
        # 12 images, one bound to each camera id 1..12
        assert len(imgs) == 12
        cam_ids = {c["camera_id"] for c in cams}
        for img in imgs:
            assert img["camera_id"] in cam_ids, \
                f"image {img['image_id']} refs missing camera {img['camera_id']}"
            # Identity quaternion (qw=1, qx=qy=qz=0) — must be normalizable
            # and finite. Parser returns a list, not a tuple.
            qvec = img["qvec"]
            assert list(qvec) == [1.0, 0.0, 0.0, 0.0]
            tvec = img["tvec"]
            # Translations were authored as iid, 2*iid, 3*iid — all finite.
            import math
            assert all(math.isfinite(v) for v in tvec)
            # Name format img_XXX.jpg — safe relative path, no traversal.
            name = img["name"]
            assert name.startswith("img_") and name.endswith(".jpg")
            assert ".." not in Path(name).parts
            assert not name.startswith(("/", "\\"))

    def test_real_fixture_passes_semantic_validation(self, tmp_path):
        """The real COLMAP fixture must pass _validate_sparse_semantics when
        paired with a photos directory containing all referenced image_name
        files. This is the integration-level proof that the parser + semantic
        checker agree with real COLMAP output."""
        _maybe_skip_if_fixture_missing()
        # Build a photos dir with all 12 referenced image_name files. The
        # semantic checker only requires the files to exist (it doesn't parse
        # their bytes — that's _photos_sha256's job).
        photos = tmp_path / "photos"
        photos.mkdir()
        for i in range(1, 13):
            (photos / f"img_{i:03d}.jpg").write_bytes(b"fake-photo-bytes")
        # Copy the real fixture into a sparse/0 layout the validator expects.
        sparse_0 = tmp_path / "sparse" / "0"
        sparse_0.mkdir(parents=True)
        for name in ("cameras.bin", "images.bin", "points3D.bin"):
            src = _REAL_COLMAP_FIXTURE_DIR / name
            (sparse_0 / name).write_bytes(src.read_bytes())
        # Should not raise.
        rl._validate_sparse_semantics(sparse_0, photos)

    def test_unknown_model_id_rejected_with_hand_crafted_bytes(self, tmp_path):
        """Unknown model id (e.g. 99) must be rejected — the parser must not
        infer parameter count from remaining bytes. This is a byte-level
        adversarial fixture: we craft a cameras.bin whose header looks valid
        but whose model id is not in COLMAP's accepted table."""
        buf = bytearray()
        buf += struct.pack("<Q", 1)          # num_cameras = 1
        buf += struct.pack("<I", 1)          # camera_id = 1
        buf += struct.pack("<i", 99)         # model = 99 (unknown)
        buf += struct.pack("<Q", 1024)       # width
        buf += struct.pack("<Q", 768)        # height
        # Append 8 doubles so the file has plausible remaining bytes — a
        # parser that inferred nparams from len(data)-pos would accept this.
        buf += struct.pack("<8d", *([1024.0] * 8))
        bad = tmp_path / "cameras_unknown_model.bin"
        bad.write_bytes(bytes(buf))
        with pytest.raises(ValueError, match="model=99"):
            rl._parse_colmap_cameras_bin(bad)

    def test_real_cameras_bin_byte_size_matches_model_table(self):
        """Independent cross-check: the file size of real COLMAP cameras.bin
        must equal 8 (header) + sum of per-camera record sizes computed from
        the _COLMAP_MODEL_NUM_PARAMS table. A wrong table would either fail
        to parse (caught above) or miscount bytes (caught here)."""
        _maybe_skip_if_fixture_missing()
        cams = rl._parse_colmap_cameras_bin(
            _REAL_COLMAP_FIXTURE_DIR / "cameras.bin")
        expected_size = 8  # uint64 num_cameras header
        for cam in cams:
            # camera_id(uint32) + model(int32) + width(uint64) + height(uint64)
            # + params(double * nparams)
            nparams = len(cam["params"])
            expected_size += 4 + 4 + 8 + 8 + 8 * nparams
        actual_size = (_REAL_COLMAP_FIXTURE_DIR / "cameras.bin").stat().st_size
        assert actual_size == expected_size, \
            f"file size {actual_size} != expected {expected_size} " \
            f"computed from parsed records"


# ============================================================
# HANDOFF-GLM-008 Task 3 — transactional three-target replacement
#
# REVIEW-CODEX-030 P0 (commit 0978ee7 held): three independent renames
# are not one atomic replacement. Codex injected a failure into the
# database replacement after the sparse directory swap and measured
# mixed_generation=true (sparse=NEW, db=OLD, images=OLD). The fix is
# a transaction journal (prepared/swapping/verified/committed) with
# full rollback and restart recovery.
#
# Required RED coverage:
# - stale optional files removed when source drops them
# - missing optional files when source never had them
# - absent-source database with stale destination database
# - interrupted staging copy rolls back to previous state
# - failure at every swap step rolls back all three targets
# - process-restart recovery restores the last complete generation
# - validation failure in staging rolls back
# - post-swap destination validation failure rolls back
# - failed run preserves the last verified destination
# - no COLMAP subprocess runs in any failure path
# ============================================================


def _change_source_to_trigger_recopy(precomp: Path) -> None:
    """Flip a byte in source cameras.bin width field (stays finite/valid
    so P7a-6 semantic validation still passes) so the fingerprint changes
    and the production caller triggers a fresh re-copy."""
    cam_path = precomp / "sparse" / "0" / "cameras.bin"
    buf = bytearray(cam_path.read_bytes())
    buf[16] ^= 0x01
    cam_path.write_bytes(bytes(buf))


class TestPrecomputedTransactionReplacement:
    """HANDOFF-GLM-008 Task 3: three-target replacement must be transactional.

    Each RED case must SystemExit before any COLMAP subprocess and must
    leave the destination in a coherent state (either old or new, never
    a mixture)."""

    def _first_run(self, env, tmp_path, photos_dir) -> Path:
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        call("--precomputed-colmap", str(precomp))
        return precomp

    def _snapshot_dst(self, ws: Path) -> dict:
        """Snapshot the byte-state of the destination for later comparison."""
        snap = {}
        for name in PRECOMPUTED_REQUIRED + PRECOMPUTED_OPTIONAL:
            p = ws / "sparse" / "0" / name
            snap[f"sparse/{name}"] = rl._sha256_file(p) if p.is_file() else None
        db = ws / "colmap.db"
        snap["colmap.db"] = db.read_bytes() if db.is_file() else None
        snap["images_files"] = sorted(
            p.name for p in (ws / "images").iterdir() if p.is_file()) \
            if (ws / "images").is_dir() else None
        return snap

    def _assert_dst_matches_snapshot(self, ws: Path, snap: dict) -> None:
        for name in PRECOMPUTED_REQUIRED + PRECOMPUTED_OPTIONAL:
            p = ws / "sparse" / "0" / name
            actual = rl._sha256_file(p) if p.is_file() else None
            assert actual == snap[f"sparse/{name}"], \
                f"dst sparse/{name} changed when it should not (rollback failed)"
        db = ws / "colmap.db"
        actual_db = db.read_bytes() if db.is_file() else None
        assert actual_db == snap["colmap.db"], \
            "dst colmap.db changed when it should not (rollback failed)"
        actual_imgs = sorted(
            p.name for p in (ws / "images").iterdir() if p.is_file()) \
            if (ws / "images").is_dir() else None
        assert actual_imgs == snap["images_files"], \
            "dst images/ changed when it should not (rollback failed)"

    def test_stale_optional_files_removed_when_source_drops_them(self, env,
                                                                  tmp_path,
                                                                  photos_dir):
        """REVIEW-CODEX-030 P7a-4: when source drops frames.bin/rigs.bin/
        project.ini, ws must not retain stale copies. The previous three
        independent renames did not remove stale optional files because
        _validate_ws_precomputed only checked files in the manifest."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)
        # Sanity: first run installed optional bins
        for opt in PRECOMPUTED_OPTIONAL:
            assert (ws / "sparse" / "0" / opt).is_file(), \
                f"sanity: first run should install {opt}"

        # Drop all optional bins from source
        for opt in PRECOMPUTED_OPTIONAL:
            (precomp / "sparse" / "0" / opt).unlink()
        # Re-run: fingerprint changes (optional SHAs removed) → trigger re-copy
        call("--resume", "--precomputed-colmap", str(precomp))

        # ws must not have stale optional files
        for opt in PRECOMPUTED_OPTIONAL:
            assert not (ws / "sparse" / "0" / opt).is_file(), \
                f"stale optional {opt} must be removed when source drops it"
        # Required bins still present
        for req in PRECOMPUTED_REQUIRED:
            assert (ws / "sparse" / "0" / req).is_file()
        # No staging/backup/journal leftover
        assert not (ws / ".staging_precomputed").exists()
        assert not (ws / ".precomputed_backup").exists()
        assert not (ws / ".precomputed_txn.json").is_file()

    def test_missing_optional_files_when_source_never_had_them(self, env,
                                                                tmp_path,
                                                                photos_dir):
        """Source never had frames.bin → ws must not have it either."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir)
        for opt in PRECOMPUTED_OPTIONAL:
            (precomp / "sparse" / "0" / opt).unlink()
        call("--precomputed-colmap", str(precomp))
        for opt in PRECOMPUTED_OPTIONAL:
            assert not (ws / "sparse" / "0" / opt).is_file(), \
                f"{opt} should not exist when source never had it"

    def test_absent_source_db_removes_stale_destination_db(self, env, tmp_path,
                                                             photos_dir):
        """REVIEW-CODEX-030 P7a-4: if source has no colmap.db but ws has a
        stale one from a previous run, the swap must remove the stale db
        (otherwise the next stage may bind a stale database)."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)
        assert (ws / "colmap.db").is_file()  # sanity

        # Drop colmap.db from source
        (precomp / "colmap.db").unlink()
        # Re-run: fingerprint changes → trigger re-copy
        call("--resume", "--precomputed-colmap", str(precomp))

        assert not (ws / "colmap.db").is_file(), \
            "stale colmap.db must be removed when source has no db"
        # Required bins still present
        for req in PRECOMPUTED_REQUIRED:
            assert (ws / "sparse" / "0" / req).is_file()

    def test_interrupted_staging_copy_rolls_back(self, env, tmp_path,
                                                  photos_dir, monkeypatch):
        """Failure during staging copytree (e.g. disk full mid-copy) must
        not leave a partial staging dir; the destination must remain
        unchanged."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)
        _change_source_to_trigger_recopy(precomp)

        # Inject failure into copytree during staging prepare
        original_copytree = shutil.copytree
        call_count = [0]

        def fail_first_copytree(*args, **kwargs):
            call_count[0] += 1
            # Only fail the staging images copy (the only copytree in
            # _copy_precomputed_to_ws); let later copytrees succeed.
            if call_count[0] == 1:
                raise OSError("injected copytree failure")
            return original_copytree(*args, **kwargs)

        monkeypatch.setattr(shutil, "copytree", fail_first_copytree)

        with pytest.raises(SystemExit, match="拷贝|copy|staging|prepare|copytree"):
            call("--resume", "--precomputed-colmap", str(precomp))

        # Destination must equal the snapshot (full rollback)
        self._assert_dst_matches_snapshot(ws, snap)
        # No staging/backup/journal leftover
        assert not (ws / ".staging_precomputed").exists()
        assert not (ws / ".precomputed_backup").exists()
        assert not (ws / ".precomputed_txn.json").is_file()
        assert _colmap_subprocess_cmds(fake) == []

    def test_failure_after_sparse_swap_rolls_back_all_three(self, env, tmp_path,
                                                              photos_dir,
                                                              monkeypatch):
        """REVIEW-CODEX-030 P0 (0978ee7 held): Codex injected failure into
        the database replacement after the sparse swap and measured
        mixed_generation=true. Fix must roll back sparse too."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)
        _change_source_to_trigger_recopy(precomp)

        # Inject failure into _swap_db so it raises after sparse is swapped
        def fail_swap_db(*args, **kwargs):
            raise SystemExit("injected swap_db failure")
        monkeypatch.setattr(rl, "_swap_db", fail_swap_db)

        with pytest.raises(SystemExit, match="swap|替换|rolled|回滚"):
            call("--resume", "--precomputed-colmap", str(precomp))

        # All three targets must be rolled back to the snapshot
        self._assert_dst_matches_snapshot(ws, snap)
        assert not (ws / ".staging_precomputed").exists()
        assert not (ws / ".precomputed_backup").exists()
        assert not (ws / ".precomputed_txn.json").is_file()
        assert _colmap_subprocess_cmds(fake) == []

    def test_failure_after_db_swap_rolls_back_all_three(self, env, tmp_path,
                                                         photos_dir,
                                                         monkeypatch):
        """Failure during the third swap (images) must roll back sparse and
        colmap.db too — never leave mixed generations."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)
        _change_source_to_trigger_recopy(precomp)

        def fail_swap_images(*args, **kwargs):
            raise SystemExit("injected swap_images failure")
        monkeypatch.setattr(rl, "_swap_images", fail_swap_images)

        with pytest.raises(SystemExit, match="swap|替换|rolled|回滚"):
            call("--resume", "--precomputed-colmap", str(precomp))

        self._assert_dst_matches_snapshot(ws, snap)
        assert not (ws / ".staging_precomputed").exists()
        assert not (ws / ".precomputed_backup").exists()
        assert not (ws / ".precomputed_txn.json").is_file()
        assert _colmap_subprocess_cmds(fake) == []

    def test_failure_during_sparse_swap_rolls_back(self, env, tmp_path,
                                                    photos_dir, monkeypatch):
        """Failure during the first swap (sparse) must leave the destination
        unchanged — no rollback needed, but no partial install either."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)
        _change_source_to_trigger_recopy(precomp)

        def fail_swap_sparse(*args, **kwargs):
            raise SystemExit("injected swap_sparse failure")
        monkeypatch.setattr(rl, "_swap_sparse", fail_swap_sparse)

        with pytest.raises(SystemExit, match="swap|替换|rolled|回滚"):
            call("--resume", "--precomputed-colmap", str(precomp))

        self._assert_dst_matches_snapshot(ws, snap)
        assert not (ws / ".staging_precomputed").exists()
        assert not (ws / ".precomputed_backup").exists()
        assert not (ws / ".precomputed_txn.json").is_file()
        assert _colmap_subprocess_cmds(fake) == []

    def test_validation_failure_in_staging_rolls_back(self, env, tmp_path,
                                                       photos_dir, monkeypatch):
        """If staging semantic validation fails (source semantics corrupted
        between manifest build and staging copy), destination must be
        unchanged. No swap should happen."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)
        _change_source_to_trigger_recopy(precomp)

        def fail_validate(sparse_0, photos):
            raise SystemExit("injected staging semantic failure")
        monkeypatch.setattr(rl, "_validate_sparse_semantics", fail_validate)

        with pytest.raises(SystemExit, match="staging|语义|prepare|语义校验"):
            call("--resume", "--precomputed-colmap", str(precomp))

        # Destination unchanged — no swap happened
        self._assert_dst_matches_snapshot(ws, snap)
        assert not (ws / ".staging_precomputed").exists()
        assert not (ws / ".precomputed_backup").exists()
        assert _colmap_subprocess_cmds(fake) == []

    def test_post_swap_validation_failure_rolls_back(self, env, tmp_path,
                                                      photos_dir, monkeypatch):
        """REVIEW-CODEX-030 P0 (0978ee7 held): swap completed but
        destination validation fails (e.g. byte SHA mismatch from disk
        corruption during rename). The transaction must roll back to the
        previous committed generation, never leave the new-but-invalid
        destination."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)
        _change_source_to_trigger_recopy(precomp)

        def fail_post_swap(*args, **kwargs):
            raise SystemExit("injected post-swap validation failure")
        monkeypatch.setattr(rl, "_verify_destination_post_swap", fail_post_swap)

        with pytest.raises(SystemExit,
                           match="post-swap|verify|validation|回滚"):
            call("--resume", "--precomputed-colmap", str(precomp))

        # Destination must be rolled back to the snapshot
        self._assert_dst_matches_snapshot(ws, snap)
        assert not (ws / ".staging_precomputed").exists()
        assert not (ws / ".precomputed_backup").exists()
        assert not (ws / ".precomputed_txn.json").is_file()
        assert _colmap_subprocess_cmds(fake) == []

    def test_restart_recovery_restores_committed_generation(self, env, tmp_path,
                                                              photos_dir):
        """REVIEW-CODEX-030 P0 (0978ee7 held): startup deletes *.old before
        deciding whether an interrupted transaction must be recovered. Fix
        must first inspect the journal state: if state=swapping, restore
        backup → destination and clean up staging, regardless of whether
        the process restart decides to run colmap."""
        call, fake, ws, _ = env
        self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)

        # Simulate a crashed swap: backup has OLD sparse/db/images,
        # destination has partial NEW content (only sparse installed, db
        # and images missing), journal state=swapping.
        backup = ws / ".precomputed_backup"
        backup.mkdir(parents=True)
        (ws / "sparse" / "0").rename(backup / "sparse_0")
        (ws / "colmap.db").rename(backup / "colmap_db")
        (ws / "images").rename(backup / "images")
        # Destination now has empty sparse; pretend install_sparse ran
        # with NEW (different-bytes) content.
        new_sparse = ws / "sparse" / "0"
        new_sparse.mkdir(parents=True)
        new_buf = bytearray((backup / "sparse_0" / "cameras.bin").read_bytes())
        new_buf[16] ^= 0xFF
        (new_sparse / "cameras.bin").write_bytes(bytes(new_buf))
        # Journal says swapping — process died mid-install.
        journal = {
            "version": 1,
            "state": "swapping",
            "expected_sparse_files": list(PRECOMPUTED_REQUIRED),
            "has_db": True,
            "started_at_utc": "2026-07-25T00:00:00+00:00",
        }
        (ws / ".precomputed_txn.json").write_text(
            json.dumps(journal), encoding="utf-8")

        # Recovery must restore the committed generation from backup.
        rl._recover_precomputed_transaction(ws)

        # Destination must equal the snapshot (old committed generation)
        self._assert_dst_matches_snapshot(ws, snap)
        # Cleanup happened
        assert not (ws / ".staging_precomputed").exists()
        assert not (ws / ".precomputed_backup").exists()
        assert not (ws / ".precomputed_txn.json").is_file()

    def test_restart_recovery_with_no_journal_is_noop(self, env, tmp_path,
                                                       photos_dir):
        """No journal → no recovery needed. Must not raise or alter dst."""
        call, fake, ws, _ = env
        self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)
        # No journal exists
        assert not (ws / ".precomputed_txn.json").is_file()
        rl._recover_precomputed_transaction(ws)
        # Destination unchanged
        self._assert_dst_matches_snapshot(ws, snap)

    def test_restart_recovery_with_corrupt_journal_cleans_up(self, env, tmp_path,
                                                               photos_dir):
        """Corrupt journal (not JSON) → conservative cleanup: remove staging
        and backup, delete journal. Destination stays as-is."""
        call, fake, ws, _ = env
        self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)
        # Write corrupt journal
        (ws / ".precomputed_txn.json").write_text("not json {", encoding="utf-8")
        # Add stray staging and backup dirs
        (ws / ".staging_precomputed").mkdir()
        (ws / ".staging_precomputed" / "junk").write_text("junk", encoding="utf-8")
        (ws / ".precomputed_backup").mkdir()
        (ws / ".precomputed_backup" / "junk").write_text("junk", encoding="utf-8")
        rl._recover_precomputed_transaction(ws)
        # Destination unchanged
        self._assert_dst_matches_snapshot(ws, snap)
        # Stray dirs cleaned up
        assert not (ws / ".staging_precomputed").exists()
        assert not (ws / ".precomputed_backup").exists()
        assert not (ws / ".precomputed_txn.json").is_file()

    def test_failed_run_preserves_last_verified_destination(self, env, tmp_path,
                                                              photos_dir,
                                                              monkeypatch):
        """A failed re-copy must leave the last verified destination intact
        (not partial new content, not empty). This is the property reviewers
        rely on: 'a failed run preserves a coherent verified destination'."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)
        snap = self._snapshot_dst(ws)

        # Second run: change source + fail swap → destination must remain
        # at the snapshot (the last verified generation).
        _change_source_to_trigger_recopy(precomp)
        def fail_swap_db(*args, **kwargs):
            raise SystemExit("injected swap_db failure")
        monkeypatch.setattr(rl, "_swap_db", fail_swap_db)

        with pytest.raises(SystemExit):
            call("--resume", "--precomputed-colmap", str(precomp))

        self._assert_dst_matches_snapshot(ws, snap)

    def test_no_colmap_runs_in_any_failure_path(self, env, tmp_path,
                                                  photos_dir, monkeypatch):
        """REVIEW-CODEX-030 P7a-4 boundary: 'A failed run must leave the
        last verified destination intact and must never run COLMAP.'
        Cover three failure points (staging validation, sparse swap,
        post-swap validation) and assert no COLMAP subprocess ran."""
        call, fake, ws, _ = env
        precomp = self._first_run(env, tmp_path, photos_dir)

        failure_injections = [
            ("staging validation", "_validate_sparse_semantics",
             lambda *a, **k: SystemExit("injected")),
            ("sparse swap", "_swap_sparse",
             lambda *a, **k: SystemExit("injected")),
            ("post-swap validation", "_verify_destination_post_swap",
             lambda *a, **k: SystemExit("injected")),
        ]
        for label, attr, raiser in failure_injections:
            _change_source_to_trigger_recopy(precomp)
            fake.reset()

            def make_fail(_raiser=raiser):
                def fail(*args, **kwargs):
                    raise _raiser()
                return fail
            monkeypatch.setattr(rl, attr, make_fail())

            with pytest.raises(SystemExit):
                call("--resume", "--precomputed-colmap", str(precomp))
            assert _colmap_subprocess_cmds(fake) == [], \
                f"COLMAP must never run in precomputed failure path ({label})"


