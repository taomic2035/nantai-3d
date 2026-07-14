"""
素材注册表: 可替换的 3DGS 资产管理

- assets/registry.json 记录 asset_id → ply 文件 / 版本 / 来源
- replace() 原子替换素材并保留历史版本 → 布局 JSON 不变, 重渲染即生效
- instantiate() 把素材放置到世界坐标 (旋转/缩放/平移), 供 chunk 渲染直接拼接
- 素材来源: synthetic (内置合成) / gpt-mock (GPT 生成模拟) / real (真实重建)

素材 ply 作者约定 (handoff 文档同步此约定):
- 局部坐标系: Z 向上, 米制, XY 原点在素材水平中心, 地面 z=0
- 加载时默认 normalize 兜底 (重心归零 + 落地), 容忍不完全规范的交付物
"""
import fcntl
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import numpy as np
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator

from pipeline.gaussian_scene import GaussianScene
from pipeline.recon_schema import Sim3

DEFAULT_ASSETS_DIR = "assets"
REGISTRY_FILE = "registry.json"
LOCK_FILE = ".registry.lock"
ASSET_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
_ASSET_ID_RE = re.compile(ASSET_ID_PATTERN)


def validate_asset_id(asset_id: str) -> str:
    """Return a canonical safe asset id or fail before any path construction."""
    if not isinstance(asset_id, str) or _ASSET_ID_RE.fullmatch(asset_id) is None:
        raise ValueError(
            "asset_id 必须匹配 ^[a-z0-9][a-z0-9_-]{0,63}$ "
            f"(小写、不可含路径): {asset_id!r}"
        )
    return asset_id


class AssetVersion(BaseModel):
    """Immutable metadata for a superseded asset payload."""

    version: int = Field(ge=1)
    ply: str
    sha256: str = ""
    origin: Literal["synthetic", "gpt-mock", "real"] = "synthetic"
    registered_at: str = ""


class AssetEntry(BaseModel):
    kind: Literal["building", "vegetation", "prop", "ground", "other"] = "other"
    ply: str  # 相对 assets 目录
    version: int = Field(default=1, ge=1)
    origin: Literal["synthetic", "gpt-mock", "real"] = "synthetic"
    footprint_m: tuple[float, float, float] | None = None  # [宽, 深, 高] 名义尺寸
    sha256: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")
    registered_at: str = ""
    history: list[AssetVersion] = Field(default_factory=list)

    @field_validator("footprint_m")
    @classmethod
    def validate_footprint(cls, value):
        if value is None:
            return value
        values = np.asarray(value, dtype=np.float64)
        if not np.all(np.isfinite(values)) or np.any(values <= 0):
            raise ValueError("footprint_m 必须是三个有限正数")
        return tuple(float(item) for item in values)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_history(cls, value):
        """Load schema-v1 registries whose history entries were bare filenames."""
        if not isinstance(value, dict):
            return value
        history = value.get("history", [])
        if history and isinstance(history[0], str):
            migrated = []
            for index, filename in enumerate(history, start=1):
                migrated.append(
                    {
                        "version": index,
                        "ply": filename,
                        "sha256": "",
                        "origin": value.get("origin", "synthetic"),
                        "registered_at": "",
                    }
                )
            value = dict(value)
            value["history"] = migrated
        return value


class RegistryDoc(BaseModel):
    schema_version: int = Field(default=2, ge=1)
    assets: dict[str, AssetEntry] = Field(default_factory=dict)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class AssetRegistry:
    """素材注册表 (目录 + registry.json)"""

    def __init__(self, assets_dir: str | Path = DEFAULT_ASSETS_DIR):
        self.assets_dir = Path(assets_dir)
        self.registry_path = self.assets_dir / REGISTRY_FILE
        self.lock_path = self.assets_dir / LOCK_FILE
        self._last_read_revision: str | None = None
        self.doc = self._read_doc()
        self._loaded_revision = self._last_read_revision

    def _read_doc(self) -> RegistryDoc:
        if self.registry_path.exists():
            payload = self.registry_path.read_bytes()
            doc = RegistryDoc(**json.loads(payload.decode("utf-8")))
            revision = hashlib.sha256(payload).hexdigest()
        else:
            doc = RegistryDoc()
            revision = None
        self._validate_doc_paths(doc)
        self._last_read_revision = revision
        return doc

    def _registry_revision(self) -> str | None:
        if not self.registry_path.exists():
            return None
        return sha256_file(self.registry_path)

    def _validate_doc_paths(self, doc: RegistryDoc) -> None:
        for asset_id, entry in doc.assets.items():
            validate_asset_id(asset_id)
            self._contained_payload_path(entry.ply)
            for historical in entry.history:
                self._contained_payload_path(historical.ply)

    def _contained_payload_path(self, relative_path: str) -> Path:
        path = Path(relative_path)
        if not relative_path or path.is_absolute():
            raise ValueError(f"素材 payload 路径越出素材目录: {relative_path!r}")
        root = self.assets_dir.resolve(strict=False)
        candidate = (self.assets_dir / path).resolve(strict=False)
        if not candidate.is_relative_to(root) or candidate == root:
            raise ValueError(f"素材 payload 路径越出素材目录: {relative_path!r}")
        return candidate

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        """Serialize registry read/check/write across processes and instances."""
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def save(self) -> None:
        candidate = self.doc.model_copy(deep=True)
        self._validate_doc_paths(candidate)
        with self._exclusive_lock():
            if self._registry_revision() != self._loaded_revision:
                raise ValueError("registry changed since load; refusing stale save")
            self._write_doc_atomic(candidate)
            self.doc = candidate

    def _write_doc_atomic(self, doc: RegistryDoc) -> None:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        payload = doc.model_dump_json(indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{REGISTRY_FILE}.", suffix=".tmp", dir=self.assets_dir
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, self.registry_path)
            self._loaded_revision = hashlib.sha256(
                payload.encode("utf-8")
            ).hexdigest()
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    def _stage_copy(
        self,
        source: str | Path,
        destination: Path,
        *,
        expected_sha256: str,
    ) -> Path:
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        root = self.assets_dir.resolve(strict=False)
        destination = self._contained_payload_path(
            str(destination.resolve(strict=False).relative_to(root))
        )
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=self.assets_dir
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            shutil.copy2(source, tmp_path)
            staged_sha = sha256_file(tmp_path)
            if not hmac.compare_digest(staged_sha, expected_sha256):
                raise ValueError(
                    "素材源文件在复制期间发生变化: "
                    f"expected {expected_sha256}, staged {staged_sha}"
                )
            with tmp_path.open("rb") as stream:
                os.fsync(stream.fileno())
            return tmp_path
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _atomic_copy(
        self,
        source: str | Path,
        destination: Path,
        *,
        expected_sha256: str,
    ) -> None:
        staged = self._stage_copy(
            source, destination, expected_sha256=expected_sha256
        )
        try:
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)

    def _commit_payload_and_doc(
        self,
        source: str | Path,
        destination: Path,
        doc: RegistryDoc,
        *,
        expected_sha256: str,
    ) -> None:
        """Commit a new version, rolling back its payload if registry write fails."""
        if destination.exists():
            raise FileExistsError(f"拒绝覆盖未登记的素材 payload: {destination}")
        staged = self._stage_copy(
            source, destination, expected_sha256=expected_sha256
        )
        payload_committed = False
        try:
            os.replace(staged, destination)
            payload_committed = True
            self._write_doc_atomic(doc)
        except Exception:
            if payload_committed:
                destination.unlink(missing_ok=True)
            raise
        finally:
            staged.unlink(missing_ok=True)

    @staticmethod
    def _validate_payload(source: str | Path) -> tuple[Path, str]:
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(f"素材 PLY 不存在: {source}")
        source_sha = sha256_file(source)
        GaussianScene.load_ply(source)
        confirmed_sha = sha256_file(source)
        if not hmac.compare_digest(source_sha, confirmed_sha):
            raise ValueError("素材源文件在校验期间发生变化")
        return source, source_sha

    def _active_sha256(self, entry: AssetEntry) -> str:
        payload_sha = self._payload_sha256(entry)
        if not payload_sha:
            return ""
        if entry.sha256 and payload_sha and not hmac.compare_digest(
            entry.sha256, payload_sha
        ):
            raise ValueError(
                f"active payload SHA-256 不匹配: manifest={entry.sha256}, "
                f"actual={payload_sha}"
            )
        return entry.sha256 or payload_sha

    def _payload_sha256(self, entry: AssetEntry) -> str:
        payload = self._contained_payload_path(entry.ply)
        return sha256_file(payload) if payload.is_file() else ""

    # ============ 注册 / 替换 ============
    def register(self, asset_id: str, ply_path: str | Path,
                 kind: str = "other", origin: str = "synthetic",
                 footprint_m: list[float] | None = None) -> AssetEntry:
        """注册新素材: 把 ply 拷入 assets 目录, 版本从 1 开始。
        已存在同 id 时等价于 replace()。
        """
        asset_id = validate_asset_id(asset_id)
        source, source_sha = self._validate_payload(ply_path)
        with self._exclusive_lock():
            disk_doc = self._read_doc()
            disk_revision = self._last_read_revision
            if asset_id in disk_doc.assets:
                entry = disk_doc.assets[asset_id]
                payload_sha = self._payload_sha256(entry)
                active_sha = entry.sha256 or payload_sha
                if hmac.compare_digest(active_sha, source_sha):
                    destination = self._contained_payload_path(entry.ply)
                    if not hmac.compare_digest(payload_sha, source_sha):
                        self._atomic_copy(
                            source,
                            destination,
                            expected_sha256=source_sha,
                        )
                        logger.info(
                            f"素材 payload 恢复/修复: {asset_id} v{entry.version}"
                        )
                    migrated = not entry.sha256 or not entry.registered_at
                    if not entry.sha256:
                        entry.sha256 = source_sha
                    if not entry.registered_at:
                        entry.registered_at = _now_iso()
                    if migrated:
                        disk_doc.schema_version = 2
                        self._write_doc_atomic(disk_doc)
                    else:
                        self._loaded_revision = disk_revision
                    self.doc = disk_doc
                    logger.info(
                        f"素材已是最新: {asset_id} v{entry.version} "
                        f"({source_sha[:12]})"
                    )
                    return entry.model_copy(deep=True)
                return self._replace_locked(
                    disk_doc,
                    asset_id,
                    source,
                    source_sha,
                    origin=origin,
                    expected_version=None,
                )

            dst_name = f"{asset_id}_v1.ply"
            destination = self._contained_payload_path(dst_name)
            entry = AssetEntry(
                kind=kind,
                ply=dst_name,
                version=1,
                origin=origin,
                footprint_m=footprint_m,
                sha256=source_sha,
                registered_at=_now_iso(),
            )
            next_doc = disk_doc.model_copy(deep=True)
            next_doc.schema_version = 2
            next_doc.assets[asset_id] = entry
            self._commit_payload_and_doc(
                source,
                destination,
                next_doc,
                expected_sha256=source_sha,
            )
            self.doc = next_doc
            logger.info(f"素材注册: {asset_id} v1 ({origin}) ← {source}")
            return entry.model_copy(deep=True)

    def replace(self, asset_id: str, new_ply_path: str | Path,
                origin: str = "real",
                expected_version: int | None = None) -> AssetEntry:
        """替换素材: 版本 +1, 旧 ply 存档进 history。
        引用该 asset_id 的所有布局无需改动, 重渲染即用新素材。
        """
        asset_id = validate_asset_id(asset_id)
        source, source_sha = self._validate_payload(new_ply_path)
        with self._exclusive_lock():
            disk_doc = self._read_doc()
            return self._replace_locked(
                disk_doc,
                asset_id,
                source,
                source_sha,
                origin=origin,
                expected_version=expected_version,
            )

    def _replace_locked(
        self,
        disk_doc: RegistryDoc,
        asset_id: str,
        source: Path,
        source_sha: str,
        *,
        origin: str,
        expected_version: int | None,
    ) -> AssetEntry:
        if asset_id not in disk_doc.assets:
            raise KeyError(f"素材不存在, 无法替换: {asset_id} (先 register)")
        current = disk_doc.assets[asset_id]
        if expected_version is not None and current.version != expected_version:
            raise ValueError(
                f"asset {asset_id}: expected version {expected_version}, "
                f"actual {current.version}"
            )
        active_sha = self._active_sha256(current)
        if not active_sha:
            raise ValueError(f"asset {asset_id}: active payload 缺失，拒绝替换")

        next_doc = disk_doc.model_copy(deep=True)
        entry = next_doc.assets[asset_id]
        entry.history.append(
            AssetVersion(
                version=current.version,
                ply=current.ply,
                sha256=active_sha,
                origin=current.origin,
                registered_at=current.registered_at,
            )
        )
        entry.version += 1
        entry.origin = origin  # type: ignore[assignment]
        dst_name = f"{asset_id}_v{entry.version}.ply"
        entry.ply = dst_name
        entry.sha256 = source_sha
        entry.registered_at = _now_iso()
        next_doc.schema_version = 2
        destination = self._contained_payload_path(dst_name)
        self._commit_payload_and_doc(
            source,
            destination,
            next_doc,
            expected_sha256=source_sha,
        )
        self.doc = next_doc
        logger.info(f"素材替换: {asset_id} → v{entry.version} ({origin})")
        return entry.model_copy(deep=True)

    # ============ 解析 / 实例化 ============
    def resolve(self, asset_id: str) -> Path | None:
        asset_id = validate_asset_id(asset_id)
        entry = self.doc.assets.get(asset_id)
        if entry is None:
            return None
        path = self._contained_payload_path(entry.ply)
        return path if path.is_file() else None

    def verified_sha256(self, asset_id: str) -> str | None:
        """Return the measured payload digest only when it matches the registry."""
        asset_id = validate_asset_id(asset_id)
        entry = self.doc.assets.get(asset_id)
        if entry is None or not entry.sha256:
            logger.warning(f"素材缺少可验证 SHA-256，拒绝加载: {asset_id}")
            return None
        path = self.resolve(asset_id)
        if path is None:
            return None
        actual_sha = sha256_file(path)
        if not hmac.compare_digest(actual_sha, entry.sha256):
            logger.warning(
                f"素材 SHA-256 不匹配，拒绝加载: {asset_id} "
                f"expected={entry.sha256[:12]} actual={actual_sha[:12]}"
            )
            return None
        return actual_sha

    def load_scene(self, asset_id: str, normalize: bool = True) -> GaussianScene | None:
        """加载素材为 GaussianScene (局部坐标); 找不到返回 None (调用方降级)"""
        verified_sha = self.verified_sha256(asset_id)
        if verified_sha is None:
            return None
        p = self.resolve(asset_id)
        if p is None:
            return None
        try:
            scene = GaussianScene.load_ply(p)
        except Exception as exc:
            logger.warning(f"素材 PLY 解析失败，拒绝加载: {asset_id}: {exc}")
            return None
        # Close the normal hash-then-open race before returning usable geometry.
        if not hmac.compare_digest(sha256_file(p), verified_sha):
            logger.warning(f"素材加载期间发生变化，拒绝使用: {asset_id}")
            return None
        if normalize and len(scene) > 0:
            # 兜底规范化: XY 重心归零, 最低点落地 z=0
            center = scene.xyz.mean(axis=0)
            scene.xyz[:, 0] -= center[0]
            scene.xyz[:, 1] -= center[1]
            scene.xyz[:, 2] -= scene.xyz[:, 2].min()
        return scene

    def instantiate(self, asset_id: str, pos_xy, rot_z_deg: float = 0.0,
                    scale: float = 1.0, z: float = 0.0) -> GaussianScene | None:
        """素材 → 世界坐标实例 (旋转/缩放/平移), 供场景拼接"""
        position = np.asarray(pos_xy, dtype=np.float64)
        scalars = np.asarray([rot_z_deg, scale, z], dtype=np.float64)
        if position.shape != (2,) or not np.all(np.isfinite(position)):
            raise ValueError("pos_xy 必须是两个有限数值")
        if not np.all(np.isfinite(scalars)) or scale <= 0:
            raise ValueError("rot_z_deg/scale/z 必须有限且 scale > 0")
        scene = self.load_scene(asset_id)
        if scene is None:
            return None
        half = np.radians(rot_z_deg) / 2.0
        sim3 = Sim3(
            scale=scale,
            quat_wxyz=[float(np.cos(half)), 0.0, 0.0, float(np.sin(half))],
            t_xyz=[float(position[0]), float(position[1]), float(z)],
        )
        return scene.transform(sim3)

    def list_assets(self) -> dict[str, AssetEntry]:
        return {
            asset_id: entry.model_copy(deep=True)
            for asset_id, entry in self.doc.assets.items()
        }


if __name__ == "__main__":
    # 自验证: 注册 → 解析 → 替换 → 版本递增
    import tempfile

    rng = np.random.default_rng(1)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        s = GaussianScene(rng.uniform(-3, 3, (100, 3)), rng.uniform(0, 1, (100, 3)))
        src = d / "src.ply"
        s.save_ply(src, flavor="3dgs")

        reg = AssetRegistry(d / "assets")
        reg.register("house_test", src, kind="building", origin="gpt-mock",
                     footprint_m=[8, 6, 6])
        assert reg.resolve("house_test") is not None
        inst = reg.instantiate("house_test", pos_xy=(50, 60), rot_z_deg=90, scale=2.0)
        assert inst is not None and len(inst) == 100
        assert abs(inst.xyz[:, 0].mean() - 50) < 1.0

        reg.replace("house_test", src, origin="real")
        e = reg.doc.assets["house_test"]
        assert e.version == 2 and len(e.history) == 1

        reg2 = AssetRegistry(d / "assets")  # 重新加载持久化
        assert reg2.doc.assets["house_test"].version == 2
    print("[OK] assets 自验证通过")
