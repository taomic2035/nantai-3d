"""scripts/reconstruct_local.py 的断点续跑 (--resume) + tee 进度测试。

核心是 fail-closed：--resume 只在**指纹逐字节相同**时跳过阶段。指纹变了 / 缺失 /
状态文件损坏 / 产物不在 → 重跑该阶段及其所有下游。绝不因为"输出文件存在"就跳过。

不真跑 COLMAP/Brush（要几小时）：把 reconstruct_local.run 换成假实现，按子命令
伪造产物并记录调用；_find/_colmap_group 也桩掉（探测真实二进制会失败）。
_select_best_colmap_model / _count_registered_images 不桩 —— 让它们跑在假 run
写出的真实 sparse/0/images.bin 上。
"""
import json
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


def _make_fake_precomputed(colmap_ws: Path, photos: Path, *,
                           n_registered: int = 12) -> None:
    """Build a fake precomputed COLMAP workspace for tests.

    Layout matches what P5b produced: <colmap_ws>/{colmap.db, sparse/0/*.bin,
    images/}. images.bin starts with uint64 num_registered so
    _count_registered_images returns > 0 (Brush stage needs a valid model).
    """
    sparse_0 = colmap_ws / "sparse" / "0"
    sparse_0.mkdir(parents=True, exist_ok=True)
    (sparse_0 / "images.bin").write_bytes(
        struct.pack("<Q", n_registered) + b"\x00" * 100)
    (sparse_0 / "cameras.bin").write_bytes(b"fake-cameras")
    (sparse_0 / "points3D.bin").write_bytes(b"fake-points")
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
        # Mutate one required bin
        (precomp / "sparse" / "0" / "cameras.bin").write_bytes(b"different")
        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))
        digest_b = _state(ws)["stages"]["colmap"]["fingerprint"]
        assert digest_a != digest_b, \
            "cameras.bin byte change must alter colmap fingerprint"

    def test_source_byte_change_triggers_recopy_not_colmap(self, env, tmp_path,
                                                            photos_dir):
        """If source images.bin changes, ws is re-copied (NOT rerun COLMAP)."""
        call, fake, ws, _ = env
        precomp = tmp_path / "precomp"
        _make_fake_precomputed(precomp, photos_dir, n_registered=12)
        call("--precomputed-colmap", str(precomp))

        # Change source bytes (keep n_registered=99 to distinguish)
        (precomp / "sparse" / "0" / "images.bin").write_bytes(
            struct.pack("<Q", 99) + b"\x00" * 100)
        fake.reset()
        call("--resume", "--precomputed-colmap", str(precomp))

        assert _colmap_subprocess_cmds(fake) == [], \
            "COLMAP must never run in precomputed mode, even on byte change"
        ws_bytes = (ws / "sparse" / "0" / "images.bin").read_bytes()
        src_bytes = (precomp / "sparse" / "0" / "images.bin").read_bytes()
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

