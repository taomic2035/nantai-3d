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
    """从命令行反推它属于哪个阶段（假 run 与断言共用的唯一映射）。"""
    joined = " ".join(cmd)
    if "pipeline.ingest" in joined:
        return "frames"
    if any(t in joined for t in ("feature_extractor", "_matcher", "mapper")):
        return "colmap"
    if "--total-steps" in joined:
        return "brush"
    if any(t in joined for t in ("normalize_ply_quats", "prepare_import")):
        return "prepare"
    if "pipeline.reconstruct" in joined:
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
        if "feature_extractor" in " ".join(cmd):
            (self.ws / "colmap.db").write_bytes(b"fake-db")
        elif "mapper" in " ".join(cmd):
            model = self.ws / "sparse" / "0"
            model.mkdir(parents=True, exist_ok=True)
            # _count_registered_images 读头 8 字节 uint64 → 装作注册了 12 张
            (model / "images.bin").write_bytes(struct.pack("<Q", 12) + b"\x00" * 8)
        elif stage == "brush":
            (self.ws / "trained.ply").write_bytes(b"fake-ply")
        elif "prepare_import" in " ".join(cmd):
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
