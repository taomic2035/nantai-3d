"""
重建管线数据契约 (pydantic)

统一世界坐标系约定 (所有模块必须遵守):
- 右手系, Z 轴向上, 单位米
- X 轴指向东, Y 轴指向北 (ENU)
- 世界原点 = 首个锚点 (GPS 参考点或第一个会话的锚点)
- 与 chunk 世界一致: chunk (cx, cy) 的世界偏移为 (cx*200, cy*200)

相机位姿约定:
- pose = camera-to-world (C2W): x_world = R @ x_cam + t
- 四元数顺序 [w, x, y, z], 单位四元数
- 相机坐标系: OpenCV 约定 (+X 右, +Y 下, +Z 前/视线方向)
"""
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator


class CameraIntrinsics(BaseModel):
    """针孔相机内参"""
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fx: float = Field(gt=0)
    fy: float = Field(gt=0)
    cx: float
    cy: float

    @classmethod
    def from_fov(cls, width: int, height: int, fov_deg: float = 60.0) -> "CameraIntrinsics":
        f = width / (2.0 * np.tan(np.radians(fov_deg) / 2.0))
        return cls(width=width, height=height, fx=f, fy=f, cx=width / 2.0, cy=height / 2.0)


class CameraPose(BaseModel):
    """单张图像在统一世界坐标系下的位姿 (camera-to-world)"""
    image: str  # 相对 photos 目录的路径
    session_id: str
    quat_wxyz: list[float] = Field(min_length=4, max_length=4)
    t_xyz: list[float] = Field(min_length=3, max_length=3)
    intrinsics: CameraIntrinsics

    @field_validator("quat_wxyz")
    @classmethod
    def _unit_quat(cls, q: list[float]) -> list[float]:
        norm = float(np.linalg.norm(q))
        if norm < 1e-8:
            raise ValueError("四元数不能为零")
        return [v / norm for v in q]

    def rotation_matrix(self) -> np.ndarray:
        w, x, y, z = self.quat_wxyz
        return np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ])

    def c2w_matrix(self) -> np.ndarray:
        m = np.eye(4)
        m[:3, :3] = self.rotation_matrix()
        m[:3, 3] = self.t_xyz
        return m


class Sim3(BaseModel):
    """相似变换 (scale, rotation, translation): x' = s * R @ x + t"""
    scale: float = Field(default=1.0, gt=0)
    quat_wxyz: list[float] = Field(default=[1.0, 0.0, 0.0, 0.0], min_length=4, max_length=4)
    t_xyz: list[float] = Field(default=[0.0, 0.0, 0.0], min_length=3, max_length=3)

    def rotation_matrix(self) -> np.ndarray:
        w, x, y, z = np.array(self.quat_wxyz) / np.linalg.norm(self.quat_wxyz)
        return np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ])

    def apply(self, pts: np.ndarray) -> np.ndarray:
        """对 (N,3) 点集应用变换"""
        return self.scale * (pts @ self.rotation_matrix().T) + np.array(self.t_xyz)


class GeoAnchor(BaseModel):
    """GPS 锚点: 会话局部坐标 → 世界 ENU 的依据"""
    lat: float
    lon: float
    alt: float = 0.0


class CaptureSession(BaseModel):
    """一次采集会话: 一个视频, 或一批同源照片

    同一会话内的帧天然共享运动连续性;
    跨会话通过联合配准或 GPS 锚点对齐到统一世界坐标系。
    """
    session_id: str
    kind: Literal["video", "photo_batch"]
    source: str  # 源视频文件名或照片目录
    images: list[str]  # 相对 photos 目录的路径, 有序 (视频按时间)
    geo_anchor: GeoAnchor | None = None


class RegistrationResult(BaseModel):
    """配准输出: 所有图像 (照片 + 视频帧) 在统一世界坐标系下的位姿"""
    schema_version: int = 1
    engine: Literal["colmap", "mock"]
    world_convention: str = "ENU, Z-up, meters, origin=first-anchor"
    geo_origin: GeoAnchor | None = None  # 世界原点对应的 GPS (若有)
    sessions: list[CaptureSession]
    poses: list[CameraPose]
    # 每个会话局部 SfM 系 → 世界系的相似变换 (联合配准时为恒等)
    session_to_world: dict[str, Sim3] = {}

    def poses_by_session(self, session_id: str) -> list[CameraPose]:
        return [p for p in self.poses if p.session_id == session_id]


# ============ GPS → ENU 工具 ============
_EARTH_R = 6378137.0  # WGS84 赤道半径


def gps_to_enu(anchor: GeoAnchor, origin: GeoAnchor) -> np.ndarray:
    """GPS 坐标 → 相对 origin 的 ENU 局部坐标 (米), 小范围平面近似"""
    d_lat = np.radians(anchor.lat - origin.lat)
    d_lon = np.radians(anchor.lon - origin.lon)
    east = d_lon * _EARTH_R * np.cos(np.radians(origin.lat))
    north = d_lat * _EARTH_R
    up = anchor.alt - origin.alt
    return np.array([east, north, up])


if __name__ == "__main__":
    # 自验证
    pose = CameraPose(
        image="a.jpg", session_id="s0",
        quat_wxyz=[1, 0, 0, 0], t_xyz=[1, 2, 3],
        intrinsics=CameraIntrinsics.from_fov(1920, 1080, 60),
    )
    assert np.allclose(pose.c2w_matrix()[:3, 3], [1, 2, 3])
    origin = GeoAnchor(lat=26.0, lon=119.0, alt=50)
    north_100m = GeoAnchor(lat=26.0 + 100 / 111319.49, lon=119.0, alt=50)
    enu = gps_to_enu(north_100m, origin)
    assert abs(enu[1] - 100) < 0.1, enu
    print("[OK] recon_schema 自验证通过")
