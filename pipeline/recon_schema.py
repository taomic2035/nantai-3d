"""Versioned reconstruction and coordinate-system contracts.

No engine name implies a coordinate system.  Every registration result names
the frame containing its camera poses, states whether scale is metric, and
records whether the axes are geographically aligned.  Unknown legacy data is
kept usable for inspection, but is never promoted to ENU/metres implicitly.

Camera poses use camera-to-world (C2W), OpenCV camera axes (+X right, +Y down,
+Z forward), and quaternions in ``[w, x, y, z]`` order.
"""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

import numpy as np
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class Handedness(StrEnum):
    RIGHT = "right"
    LEFT = "left"
    UNKNOWN = "unknown"


class AxisConvention(StrEnum):
    ENU_Z_UP = "enu-z-up"
    LOCAL_Z_UP = "local-z-up"
    SFM_ARBITRARY = "sfm-arbitrary"
    UNKNOWN = "unknown"


class CoordinateUnits(StrEnum):
    METERS = "meters"
    ARBITRARY = "arbitrary"
    UNKNOWN = "unknown"


class MetricStatus(StrEnum):
    METRIC = "metric"
    ARBITRARY = "arbitrary"
    UNKNOWN = "unknown"


class GeoAlignment(StrEnum):
    ALIGNED = "aligned"
    UNALIGNED = "unaligned"
    UNKNOWN = "unknown"


class FrameProvenance(StrEnum):
    MEASURED = "measured"
    SYNTHETIC = "synthetic"
    SFM = "sfm"
    UNKNOWN = "unknown"


class AlignmentStatus(StrEnum):
    ALIGNED = "aligned"
    UNALIGNED = "unaligned"
    SYNTHETIC = "synthetic"
    UNKNOWN = "unknown"


class TransformMethod(StrEnum):
    IDENTITY = "identity"
    GPS_ANCHOR = "gps-anchor"
    SYNTHETIC_LAYOUT = "synthetic-layout"
    EXTERNAL_SIM3 = "external-sim3"
    CONTROL_POINTS = "control-points"
    UNKNOWN = "unknown"


class CoordinateFrame(BaseModel):
    """Machine-readable statement about a three-dimensional coordinate frame."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    frame_id: str = Field(min_length=1)
    handedness: Handedness
    axes: AxisConvention
    units: CoordinateUnits
    metric_status: MetricStatus
    # Kept as ``geo_aligned`` for schema compatibility; the enum is intentional:
    # legacy inputs can be unknown rather than coerced to false.
    geo_aligned: GeoAlignment
    provenance: FrameProvenance
    evidence: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _consistent_claims(self) -> CoordinateFrame:
        if self.handedness is Handedness.LEFT:
            raise ValueError("Nantai coordinate frames must be right-handed")
        if self.handedness is Handedness.UNKNOWN and any(
            value != unknown
            for value, unknown in (
                (self.axes, AxisConvention.UNKNOWN),
                (self.units, CoordinateUnits.UNKNOWN),
                (self.metric_status, MetricStatus.UNKNOWN),
                (self.geo_aligned, GeoAlignment.UNKNOWN),
                (self.provenance, FrameProvenance.UNKNOWN),
            )
        ):
            raise ValueError("unknown handedness is only valid for a fully unknown legacy frame")
        if self.metric_status is MetricStatus.METRIC and self.units is not CoordinateUnits.METERS:
            raise ValueError("metric status requires units=meters")
        if (
            self.metric_status is MetricStatus.ARBITRARY
            and self.units is not CoordinateUnits.ARBITRARY
        ):
            raise ValueError("arbitrary metric status requires units=arbitrary")
        if self.geo_aligned is GeoAlignment.ALIGNED:
            if self.axes is not AxisConvention.ENU_Z_UP:
                raise ValueError("geo-aligned frames must use ENU Z-up axes")
            if (
                self.units is not CoordinateUnits.METERS
                or self.metric_status is not MetricStatus.METRIC
            ):
                raise ValueError("geo-aligned frames must be metric metres")
        return self


class CameraIntrinsics(BaseModel):
    """Pinhole camera intrinsics."""

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fx: float = Field(gt=0)
    fy: float = Field(gt=0)
    cx: float
    cy: float

    @field_validator("fx", "fy", "cx", "cy")
    @classmethod
    def _finite(cls, value: float) -> float:
        value = float(value)
        if not np.isfinite(value):
            raise ValueError("camera intrinsics must be finite")
        return value

    @classmethod
    def from_fov(cls, width: int, height: int, fov_deg: float = 60.0) -> CameraIntrinsics:
        if not np.isfinite(fov_deg) or not 0 < fov_deg < 180:
            raise ValueError("fov_deg must be finite and between 0 and 180")
        focal = width / (2.0 * np.tan(np.radians(fov_deg) / 2.0))
        return cls(width=width, height=height, fx=focal, fy=focal,
                   cx=width / 2.0, cy=height / 2.0)


class CameraPose(BaseModel):
    """One image pose in the registration result's ``pose_frame``."""

    image: str
    session_id: str
    quat_wxyz: list[float] = Field(min_length=4, max_length=4)
    t_xyz: list[float] = Field(min_length=3, max_length=3)
    intrinsics: CameraIntrinsics
    # COLMAP calibration identity is optional for legacy/mock poses, but when
    # present it must remain lossless enough to recover the per-image camera
    # model, including distortion parameters encoded in ``camera_params``.
    camera_id: int | None = Field(default=None, gt=0)
    camera_model: str | None = Field(default=None, min_length=1)
    camera_params: tuple[float, ...] | None = None

    @field_validator("quat_wxyz")
    @classmethod
    def _unit_quat(cls, values: list[float]) -> list[float]:
        q = np.asarray(values, dtype=np.float64)
        if not np.all(np.isfinite(q)):
            raise ValueError("quaternion values must be finite")
        norm = float(np.linalg.norm(q))
        if norm < 1e-8:
            raise ValueError("quaternion cannot be zero")
        return (q / norm).tolist()

    @field_validator("t_xyz")
    @classmethod
    def _finite_translation(cls, values: list[float]) -> list[float]:
        t = np.asarray(values, dtype=np.float64)
        if not np.all(np.isfinite(t)):
            raise ValueError("translation values must be finite")
        return t.tolist()

    @field_validator("camera_params")
    @classmethod
    def _finite_camera_params(
        cls, values: tuple[float, ...] | None
    ) -> tuple[float, ...] | None:
        if values is None:
            return None
        params = np.asarray(values, dtype=np.float64)
        if params.ndim != 1 or len(params) == 0 or not np.all(np.isfinite(params)):
            raise ValueError("camera_params must be a non-empty finite sequence")
        return tuple(params.tolist())

    @model_validator(mode="after")
    def _complete_camera_calibration(self) -> CameraPose:
        calibration = (self.camera_id, self.camera_model, self.camera_params)
        if any(value is not None for value in calibration) and not all(
            value is not None for value in calibration
        ):
            raise ValueError(
                "camera_id, camera_model, and camera_params must be provided together"
            )
        return self

    def rotation_matrix(self) -> np.ndarray:
        return _quat_to_rotation_matrix(self.quat_wxyz)

    def c2w_matrix(self) -> np.ndarray:
        matrix = np.eye(4)
        matrix[:3, :3] = self.rotation_matrix()
        matrix[:3, 3] = self.t_xyz
        return matrix


def _quat_to_rotation_matrix(values: list[float] | np.ndarray) -> np.ndarray:
    q = np.asarray(values, dtype=np.float64)
    q = q / np.linalg.norm(q)
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def _canonical_float(value: float) -> float:
    """Normalize signed zero without quantizing content-addressed geometry."""

    value = float(value)
    return 0.0 if value == 0 else value


def _rotation_matrix_to_quat(matrix: np.ndarray) -> list[float]:
    """Convert a validated proper rotation matrix to a unit wxyz quaternion."""

    r = np.asarray(matrix, dtype=np.float64)
    trace = float(np.trace(r))
    if trace > 0:
        s = np.sqrt(trace + 1.0) * 2
        q = [0.25 * s, (r[2, 1] - r[1, 2]) / s,
             (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [(r[2, 1] - r[1, 2]) / s, 0.25 * s,
             (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s]
    elif r[1, 1] > r[2, 2]:
        s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 2] - r[2, 0]) / s, (r[0, 1] + r[1, 0]) / s,
             0.25 * s, (r[1, 2] + r[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[1, 0] - r[0, 1]) / s, (r[0, 2] + r[2, 0]) / s,
             (r[1, 2] + r[2, 1]) / s, 0.25 * s]
    q_arr = np.asarray(q, dtype=np.float64)
    return (q_arr / np.linalg.norm(q_arr)).tolist()


class Sim3(BaseModel):
    """Proper similarity transform ``x' = scale * R @ x + t``.

    ``rotation_matrix_xyz`` is accepted for external tools that emit matrices;
    it is validated and canonicalised into ``quat_wxyz``.  Reflections and
    non-orthogonal matrices are rejected before any geometry is mutated.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scale: float = Field(default=1.0, gt=0)
    quat_wxyz: tuple[float, float, float, float] = Field(
        default_factory=lambda: (1.0, 0.0, 0.0, 0.0), min_length=4, max_length=4
    )
    t_xyz: tuple[float, float, float] = Field(
        default_factory=lambda: (0.0, 0.0, 0.0), min_length=3, max_length=3
    )
    rotation_matrix_xyz: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ] | None = None

    @field_validator("scale")
    @classmethod
    def _finite_scale(cls, value: float) -> float:
        value = float(value)
        if not np.isfinite(value):
            raise ValueError("scale must be finite")
        return value

    @field_validator("quat_wxyz")
    @classmethod
    def _valid_quaternion(
        cls, values: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        q = np.asarray(values, dtype=np.float64)
        if not np.all(np.isfinite(q)):
            raise ValueError("quaternion values must be finite")
        norm = float(np.linalg.norm(q))
        if norm < 1e-8:
            raise ValueError("quaternion cannot be zero")
        q = q / norm
        # q and -q encode the same rotation.  Pick one hemisphere so model
        # dumps, transform ids, and downstream audit logs do not depend on an
        # external tool's arbitrary sign choice.
        for component in q:
            if abs(component) <= 1e-15:
                continue
            if component < 0:
                q = -q
            break
        q[np.abs(q) <= 1e-15] = 0.0
        return tuple(q.tolist())

    @field_validator("t_xyz")
    @classmethod
    def _valid_translation(
        cls, values: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        t = np.asarray(values, dtype=np.float64)
        if not np.all(np.isfinite(t)):
            raise ValueError("translation values must be finite")
        return tuple(t.tolist())

    @field_validator("rotation_matrix_xyz")
    @classmethod
    def _proper_rotation(
        cls,
        value: tuple[
            tuple[float, float, float],
            tuple[float, float, float],
            tuple[float, float, float],
        ] | None,
    ) -> tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ] | None:
        if value is None:
            return None
        rotation = np.asarray(value, dtype=np.float64)
        if rotation.shape != (3, 3) or not np.all(np.isfinite(rotation)):
            raise ValueError("rotation matrix must be a finite 3x3 matrix")
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-7):
            raise ValueError("rotation matrix must be orthogonal")
        determinant = float(np.linalg.det(rotation))
        if determinant <= 0 or not np.isclose(determinant, 1.0, atol=1e-7):
            raise ValueError("rotation matrix must be proper with determinant +1")
        return tuple(tuple(row) for row in rotation.tolist())

    @model_validator(mode="after")
    def _canonicalise_rotation(self) -> Sim3:
        if self.rotation_matrix_xyz is None:
            return self
        rotation = np.asarray(self.rotation_matrix_xyz, dtype=np.float64)
        derived = _rotation_matrix_to_quat(rotation)
        if "quat_wxyz" in self.model_fields_set:
            supplied = _quat_to_rotation_matrix(self.quat_wxyz)
            if not np.allclose(supplied, rotation, atol=1e-7):
                raise ValueError("quat_wxyz and rotation_matrix_xyz describe different rotations")
        object.__setattr__(self, "quat_wxyz", tuple(derived))
        return self

    def rotation_matrix(self) -> np.ndarray:
        if self.rotation_matrix_xyz is not None:
            return np.asarray(self.rotation_matrix_xyz, dtype=np.float64)
        return _quat_to_rotation_matrix(self.quat_wxyz)

    def apply(self, points: np.ndarray) -> np.ndarray:
        points = np.asarray(points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 3 or not np.all(np.isfinite(points)):
            raise ValueError("Sim3 input must be a finite (N, 3) point array")
        return self.scale * (points @ self.rotation_matrix().T) + np.asarray(self.t_xyz)


class FrameTransform(BaseModel):
    """Auditable mapping between two named coordinate frames."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    transform_id: str | None = None
    source_frame: str = Field(min_length=1)
    target_frame: str = Field(min_length=1)
    sim3: Sim3
    method: TransformMethod
    evidence: tuple[str, ...] = Field(default_factory=tuple)

    def _derived_id(self) -> str:
        rotation = self.sim3.rotation_matrix()
        payload = {
            "source_frame": self.source_frame,
            "target_frame": self.target_frame,
            "scale": _canonical_float(self.sim3.scale),
            "rotation_matrix_xyz": [
                [_canonical_float(value) for value in row] for row in rotation
            ],
            "t_xyz": [_canonical_float(value) for value in self.sim3.t_xyz],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode("utf-8")
        return f"xf-{hashlib.sha256(encoded).hexdigest()[:20]}"

    @model_validator(mode="after")
    def _content_addressed_id(self) -> FrameTransform:
        if self.source_frame == self.target_frame and not (
            self.sim3.scale == 1.0
            and np.array_equal(self.sim3.rotation_matrix(), np.eye(3))
            and all(value == 0.0 for value in self.sim3.t_xyz)
        ):
            raise ValueError("non-identity same-frame transform is invalid")
        expected = self._derived_id()
        if self.transform_id is not None and self.transform_id != expected:
            raise ValueError("transform_id must be content-derived")
        object.__setattr__(self, "transform_id", expected)
        return self


class SplatInput(BaseModel):
    """A splat artifact plus the frame declaration required to import it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    source_frame: CoordinateFrame
    transform: FrameTransform | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_frame_id(cls, raw: Any) -> Any:
        """Keep the old API conservative instead of guessing metric facts.

        ``frame_id=...`` remains parseable for callers that also provide an
        explicit transform.  It becomes a fully unknown source-frame contract,
        so it can never be used as a no-op promotion to metres.  New callers
        must pass ``source_frame=CoordinateFrame(...)``.
        """
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        legacy_frame_id = data.pop("frame_id", None)
        source = data.get("source_frame")
        if source is None and legacy_frame_id is not None:
            data["source_frame"] = {
                "frame_id": legacy_frame_id,
                "handedness": "unknown",
                "axes": "unknown",
                "units": "unknown",
                "metric_status": "unknown",
                "geo_aligned": "unknown",
                "provenance": "unknown",
                "evidence": ["legacy-frame-id-only"],
            }
        elif source is not None and legacy_frame_id is not None:
            source_id = (
                source.frame_id if isinstance(source, CoordinateFrame)
                else source.get("frame_id") if isinstance(source, dict)
                else None
            )
            if source_id != legacy_frame_id:
                raise ValueError("legacy frame_id conflicts with source_frame.frame_id")
        return data

    @property
    def frame_id(self) -> str:
        """Read-only compatibility alias; new code should use source_frame."""
        return self.source_frame.frame_id

    @property
    def uses_legacy_frame_id(self) -> bool:
        return "legacy-frame-id-only" in self.source_frame.evidence

    @model_validator(mode="after")
    def _matching_source_frame(self) -> SplatInput:
        if self.transform is not None and self.transform.source_frame != self.frame_id:
            raise ValueError("transform source_frame must match SplatInput frame_id")
        return self


class GeoAnchor(BaseModel):
    """GPS anchor used as alignment evidence, not an automatic frame upgrade."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt: float = 0.0

    @field_validator("lat", "lon", "alt")
    @classmethod
    def _finite(cls, value: float) -> float:
        value = float(value)
        if not np.isfinite(value):
            raise ValueError("GPS coordinates must be finite")
        return value


class ControlPoint(BaseModel):
    """One SfM<->ENU correspondence used to fit a Sim3 alignment.

    The source side is either an explicit ``source_xyz`` in the registration's
    ``pose_frame`` or an ``image`` name resolved to that pose's camera centre
    (``CameraPose.t_xyz``).  The target side is either an explicit ``enu_xyz`` in
    metres or a ``geo`` anchor reduced through ``gps_to_enu`` against a shared
    ``geo_origin``.  Exactly one of each side must be supplied; every supplied
    coordinate must be finite.  Nothing here promotes a frame to metres -- it is
    only evidence handed to the fitter, which stays fail-closed on its own gates.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)
    source_xyz: tuple[float, float, float] | None = None
    image: str | None = None
    enu_xyz: tuple[float, float, float] | None = None
    geo: GeoAnchor | None = None

    @field_validator("source_xyz", "enu_xyz")
    @classmethod
    def _finite_triple(
        cls, value: tuple[float, float, float] | None
    ) -> tuple[float, float, float] | None:
        if value is None:
            return None
        coords = np.asarray(value, dtype=np.float64)
        if coords.shape != (3,) or not np.all(np.isfinite(coords)):
            raise ValueError("control-point coordinates must be finite (x, y, z)")
        return tuple(coords.tolist())

    @model_validator(mode="after")
    def _exactly_one_per_side(self) -> ControlPoint:
        if (self.source_xyz is None) == (self.image is None):
            raise ValueError(
                "control point requires exactly one source: source_xyz or image"
            )
        if (self.enu_xyz is None) == (self.geo is None):
            raise ValueError(
                "control point requires exactly one target: enu_xyz or geo"
            )
        return self


class Sim3AlignmentEvidence(BaseModel):
    """Machine-readable record of a Umeyama SfM->ENU Sim3 fit and its gates.

    Serialised onto ``FrameTransform.evidence`` and the measured world frame's
    ``evidence`` via the ``sim3.alignment.v1=<json>`` convention so any consumer
    can re-derive residuals, degeneracy margins, and whether the fit passed the
    RMS gate.  ``passed`` records the gate outcome; it never grants metric status
    on its own -- the aligning code refuses to emit a world frame when it is
    False.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: Literal["umeyama-sim3"]
    n_control_points: int = Field(ge=0)
    scale: float
    rms_residual_m: float = Field(ge=0)
    max_residual_m: float = Field(ge=0)
    per_point_residual_m: tuple[float, ...]
    source_singular_values: tuple[float, float, float]
    min_span_ratio: float
    max_rms_threshold_m: float
    geo_origin: dict[str, float]
    control_point_labels: tuple[str, ...]
    passed: bool

    def to_evidence(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return f"sim3.alignment.v1={payload}"

    @classmethod
    def parse(cls, evidence: str) -> Sim3AlignmentEvidence:
        prefix = "sim3.alignment.v1="
        if not evidence.startswith(prefix):
            raise ValueError("not a sim3.alignment.v1 evidence string")
        return cls.model_validate_json(evidence.split("=", 1)[1])


class CaptureSession(BaseModel):
    """One video or one coherent batch of photos."""

    session_id: str
    kind: Literal["video", "photo_batch"]
    source: str
    images: list[str]
    geo_anchor: GeoAnchor | None = None


class RegistrationResult(BaseModel):
    """Camera registrations and the evidence-backed frame containing them."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2, ge=1)
    engine: Literal["colmap", "mock"]
    pose_frame: CoordinateFrame
    world_frame: CoordinateFrame | None = None
    alignment_status: AlignmentStatus
    pose_to_world: FrameTransform | None = None
    geo_origin: GeoAnchor | None = None
    sessions: list[CaptureSession]
    poses: list[CameraPose]

    # Read-only compatibility fields from schema v1.  They are deliberately not
    # interpreted as proof that the legacy artifact is metric or aligned.
    world_convention: str | None = None
    session_to_world: dict[str, Sim3] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _migrate_v1_as_unknown(cls, raw: Any) -> Any:
        if not isinstance(raw, dict) or "pose_frame" in raw:
            return raw
        explicit_version = raw.get("schema_version")
        if explicit_version is not None and int(explicit_version) >= 2:
            raise ValueError("schema v2 RegistrationResult requires pose_frame")
        data = dict(raw)
        data["schema_version"] = 1
        data["pose_frame"] = {
            "frame_id": "legacy-unknown",
            "handedness": "unknown",
            "axes": "unknown",
            "units": "unknown",
            "metric_status": "unknown",
            "geo_aligned": "unknown",
            "provenance": "unknown",
            "evidence": ["schema-v1-missing-frame-contract"],
        }
        data.setdefault("world_frame", None)
        data.setdefault("alignment_status", "unknown")
        data.setdefault("pose_to_world", None)
        return data

    @model_validator(mode="after")
    def _validate_transform_chain(self) -> RegistrationResult:
        if self.pose_to_world is None:
            if (
                self.world_frame is not None
                and self.world_frame.frame_id != self.pose_frame.frame_id
            ):
                raise ValueError("world_frame requires pose_to_world when frame ids differ")
            return self
        if self.world_frame is None:
            raise ValueError("pose_to_world requires world_frame")
        if self.pose_to_world.source_frame != self.pose_frame.frame_id:
            raise ValueError("pose_to_world source_frame must match pose_frame")
        if self.pose_to_world.target_frame != self.world_frame.frame_id:
            raise ValueError("pose_to_world target_frame must match world_frame")
        return self

    @property
    def target_frame(self) -> CoordinateFrame:
        if self.pose_to_world is not None and self.world_frame is not None:
            return self.world_frame
        return self.pose_frame

    @property
    def transform_chain(self) -> list[FrameTransform]:
        return [self.pose_to_world] if self.pose_to_world is not None else []

    def poses_by_session(self, session_id: str) -> list[CameraPose]:
        return [pose for pose in self.poses if pose.session_id == session_id]


# ============ GPS -> ENU utility ============
_EARTH_R = 6378137.0


def gps_to_enu(anchor: GeoAnchor, origin: GeoAnchor) -> np.ndarray:
    """Small-area WGS84-to-local-ENU approximation in metres."""

    d_lat = np.radians(anchor.lat - origin.lat)
    d_lon = np.radians(anchor.lon - origin.lon)
    east = d_lon * _EARTH_R * np.cos(np.radians(origin.lat))
    north = d_lat * _EARTH_R
    up = anchor.alt - origin.alt
    return np.array([east, north, up])


if __name__ == "__main__":
    pose = CameraPose(
        image="a.jpg", session_id="s0", quat_wxyz=[1, 0, 0, 0],
        t_xyz=[1, 2, 3], intrinsics=CameraIntrinsics.from_fov(1920, 1080, 60),
    )
    assert np.allclose(pose.c2w_matrix()[:3, 3], [1, 2, 3])
    origin = GeoAnchor(lat=26.0, lon=119.0, alt=50)
    north_100m = GeoAnchor(lat=26.0 + 100 / 111319.49, lon=119.0, alt=50)
    assert abs(gps_to_enu(north_100m, origin)[1] - 100) < 0.1
    print("[OK] recon_schema self-check passed")
