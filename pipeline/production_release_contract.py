"""Standard-library-only contracts for a Production runtime release."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from pipeline.release_archive import (
    ReleaseArchiveError,
    canonical_json_bytes,
    safe_posix_member_path,
)

PRODUCTION_RELEASE_SCHEMA = "nantai.production-runtime-release.v1"
PRODUCTION_PUBLIC_EVIDENCE_SCHEMA = "nantai.production-public-evidence.v1"
PRODUCTION_RELEASE_NAME = "PRODUCTION-RELEASE.json"
CHECKSUMS_NAME = "SHA256SUMS.txt"
PRODUCTION_LAYOUT = "nantai.production-runtime.v1"
PRODUCTION_GATE_IDS = (
    "dataset",
    "capture",
    "sfm",
    "production-training",
    "import-integrity",
    "render-quality",
    "viewer-performance",
    "human-review",
    "release-rights",
    "metric-alignment",
)
VISUAL_CATEGORIES = (
    "scene-envelope",
    "floaters",
    "view-dependent-colour",
    "exposure-seams",
    "transparent-surfaces",
    "navigable-holes",
    "fidelity-label",
)
PRIVATE_EVIDENCE_OMITTED = (
    "capture-media",
    "control-point-coordinates",
    "private-operator-identity",
    "remote-host-configuration",
    "training-input-pixels",
)
PRODUCTION_EXCLUSIONS = (
    ".git/",
    ".nantai-studio/",
    "handoff/",
    "input/",
    "tests/",
    "web/data/recon/model-preview/",
)
PRODUCTION_ENTRYPOINTS = {
    "scene_manifest": "/web/data/recon/recon_manifest.json",
    "studio": "/web/studio/",
    "viewer": "/web/viewer/",
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_SCENE_ID = re.compile(r"^scene-[0-9a-f]{64}$")


class ProductionReleaseContractError(ValueError):
    """Raised when a Production receipt or public projection is invalid."""


def _mapping(value: object, *, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProductionReleaseContractError(f"{label} must be an object")
    return value


def _exact_keys(
    value: Mapping[str, object],
    expected: set[str],
    *,
    label: str,
) -> None:
    if set(value) != expected:
        raise ProductionReleaseContractError(f"{label} fields are not canonical")


def _sha(value: object, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ProductionReleaseContractError(f"{label} SHA-256 is invalid")
    return value


def _true(value: object, *, label: str) -> bool:
    if value is not True:
        raise ProductionReleaseContractError(f"{label} must be true")
    return True


def _validated_acceptance(value: object) -> dict[str, object]:
    acceptance = _mapping(value, label="acceptance evidence")
    _exact_keys(
        acceptance,
        {
            "report_sha256",
            "decision_sha256",
            "source_role",
            "production_release_allowed",
            "gates",
        },
        label="acceptance evidence",
    )
    if acceptance["source_role"] != "production-acceptance":
        raise ProductionReleaseContractError(
            "acceptance source role must be production-acceptance"
        )
    _true(
        acceptance["production_release_allowed"],
        label="production release decision",
    )
    gates = acceptance["gates"]
    if not isinstance(gates, list) or len(gates) != len(PRODUCTION_GATE_IDS):
        raise ProductionReleaseContractError(
            "acceptance gate list is incomplete"
        )
    normalized_gates: list[dict[str, str]] = []
    for index, raw in enumerate(gates):
        gate = _mapping(raw, label="acceptance gate")
        _exact_keys(gate, {"id", "state"}, label="acceptance gate")
        if gate["id"] != PRODUCTION_GATE_IDS[index] or gate["state"] != "accepted":
            raise ProductionReleaseContractError(
                "acceptance gates must be ordered and accepted"
            )
        normalized_gates.append(
            {"id": PRODUCTION_GATE_IDS[index], "state": "accepted"}
        )
    return {
        "report_sha256": _sha(
            acceptance["report_sha256"],
            label="acceptance report",
        ),
        "decision_sha256": _sha(
            acceptance["decision_sha256"],
            label="acceptance decision",
        ),
        "source_role": "production-acceptance",
        "production_release_allowed": True,
        "gates": normalized_gates,
    }


def _validated_source(value: object) -> dict[str, object]:
    source = _mapping(value, label="source evidence")
    _exact_keys(
        source,
        {"dataset_id_sha256", "capture_manifest_sha256", "rights"},
        label="source evidence",
    )
    rights = _mapping(source["rights"], label="release rights")
    _exact_keys(
        rights,
        {
            "redistribution_allowed",
            "release_inclusion_allowed",
            "processing_purposes",
        },
        label="release rights",
    )
    _true(rights["redistribution_allowed"], label="release rights redistribution")
    _true(
        rights["release_inclusion_allowed"],
        label="release rights inclusion",
    )
    purposes = rights["processing_purposes"]
    if (
        not isinstance(purposes, list)
        or not purposes
        or any(
            not isinstance(item, str)
            or not item
            or item != item.strip()
            or any(ord(character) < 32 for character in item)
            for item in purposes
        )
        or len(purposes) != len(set(purposes))
        or purposes != sorted(purposes)
        or "3d-reconstruction" not in purposes
    ):
        raise ProductionReleaseContractError(
            "release rights processing purposes are invalid"
        )
    return {
        "dataset_id_sha256": _sha(
            source["dataset_id_sha256"],
            label="dataset identity",
        ),
        "capture_manifest_sha256": _sha(
            source["capture_manifest_sha256"],
            label="capture manifest",
        ),
        "rights": {
            "redistribution_allowed": True,
            "release_inclusion_allowed": True,
            "processing_purposes": list(purposes),
        },
    }


def _validated_scene(value: object) -> dict[str, object]:
    scene = _mapping(value, label="scene evidence")
    _exact_keys(
        scene,
        {
            "scene_identity",
            "import_receipt_sha256",
            "manifest_sha256",
            "quality_role",
            "geometry_usability",
            "units",
            "alignment_rms_m",
            "gaussian_count",
        },
        label="scene evidence",
    )
    identity = scene["scene_identity"]
    if not isinstance(identity, str) or _SCENE_ID.fullmatch(identity) is None:
        raise ProductionReleaseContractError("scene identity is invalid")
    if scene["quality_role"] != "production":
        raise ProductionReleaseContractError(
            "scene quality role must be production"
        )
    if scene["geometry_usability"] != "metric-aligned":
        raise ProductionReleaseContractError(
            "scene geometry must be metric-aligned"
        )
    if scene["units"] != "meters":
        raise ProductionReleaseContractError("scene units must be meters")
    rms = scene["alignment_rms_m"]
    if (
        isinstance(rms, bool)
        or not isinstance(rms, (int, float))
        or not math.isfinite(float(rms))
        or float(rms) < 0.0
        or float(rms) > 0.25
    ):
        raise ProductionReleaseContractError(
            "scene alignment RMS is invalid"
        )
    count = scene["gaussian_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 100_000:
        raise ProductionReleaseContractError(
            "scene Gaussian count is below the Production minimum"
        )
    return {
        "scene_identity": identity,
        "import_receipt_sha256": _sha(
            scene["import_receipt_sha256"],
            label="import receipt",
        ),
        "manifest_sha256": _sha(
            scene["manifest_sha256"],
            label="scene manifest",
        ),
        "quality_role": "production",
        "geometry_usability": "metric-aligned",
        "units": "meters",
        "alignment_rms_m": float(rms),
        "gaussian_count": count,
    }


def _validated_training(value: object) -> dict[str, str]:
    training = _mapping(value, label="training evidence")
    _exact_keys(
        training,
        {
            "closure_sha256",
            "runtime_decision_sha256",
            "container_identity_sha256",
        },
        label="training evidence",
    )
    return {
        "closure_sha256": _sha(
            training["closure_sha256"],
            label="training closure",
        ),
        "runtime_decision_sha256": _sha(
            training["runtime_decision_sha256"],
            label="runtime decision",
        ),
        "container_identity_sha256": _sha(
            training["container_identity_sha256"],
            label="container identity",
        ),
    }


def _validated_render(value: object) -> dict[str, object]:
    render = _mapping(value, label="render evidence")
    _exact_keys(
        render,
        {"policy_sha256", "report_sha256", "accepted"},
        label="render evidence",
    )
    _true(render["accepted"], label="render acceptance")
    return {
        "policy_sha256": _sha(render["policy_sha256"], label="render policy"),
        "report_sha256": _sha(render["report_sha256"], label="render report"),
        "accepted": True,
    }


def _validated_viewer(value: object) -> dict[str, object]:
    viewer = _mapping(value, label="Viewer evidence")
    _exact_keys(
        viewer,
        {
            "schema",
            "policy_sha256",
            "report_sha256",
            "accepted",
            "screenshot_count",
        },
        label="Viewer evidence",
    )
    if viewer["schema"] != "nantai.viewer-performance-report.v2":
        raise ProductionReleaseContractError(
            "Viewer evidence must use the v2 report"
        )
    _true(viewer["accepted"], label="Viewer acceptance")
    screenshot_count = viewer["screenshot_count"]
    if (
        isinstance(screenshot_count, bool)
        or not isinstance(screenshot_count, int)
        or screenshot_count < 3
    ):
        raise ProductionReleaseContractError(
            "Viewer screenshot count is below three"
        )
    return {
        "schema": "nantai.viewer-performance-report.v2",
        "policy_sha256": _sha(
            viewer["policy_sha256"],
            label="Viewer policy",
        ),
        "report_sha256": _sha(
            viewer["report_sha256"],
            label="Viewer report",
        ),
        "accepted": True,
        "screenshot_count": screenshot_count,
    }


def _validated_human_review(value: object) -> dict[str, object]:
    review = _mapping(value, label="human review evidence")
    _exact_keys(
        review,
        {"policy_sha256", "review_sha256", "accepted", "categories"},
        label="human review evidence",
    )
    _true(review["accepted"], label="human review acceptance")
    if review["categories"] != list(VISUAL_CATEGORIES):
        raise ProductionReleaseContractError(
            "human review categories are incomplete or unordered"
        )
    return {
        "policy_sha256": _sha(
            review["policy_sha256"],
            label="human review policy",
        ),
        "review_sha256": _sha(
            review["review_sha256"],
            label="human review receipt",
        ),
        "accepted": True,
        "categories": list(VISUAL_CATEGORIES),
    }


def validate_public_evidence(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Validate and normalize one redacted Production evidence projection."""

    evidence = _mapping(value, label="public evidence")
    _exact_keys(
        evidence,
        {
            "schema",
            "fixture_kind",
            "acceptance",
            "source",
            "scene",
            "training",
            "render",
            "viewer",
            "human_review",
            "private_evidence_omitted",
        },
        label="public evidence",
    )
    if evidence["schema"] != PRODUCTION_PUBLIC_EVIDENCE_SCHEMA:
        raise ProductionReleaseContractError(
            "public evidence schema is unsupported"
        )
    fixture_kind = evidence["fixture_kind"]
    if fixture_kind not in {None, "modeled-contract-not-real-release"}:
        raise ProductionReleaseContractError(
            "public evidence fixture kind is invalid"
        )
    if evidence["private_evidence_omitted"] != list(PRIVATE_EVIDENCE_OMITTED):
        raise ProductionReleaseContractError(
            "private evidence omission list is incomplete or unordered"
        )
    return {
        "schema": PRODUCTION_PUBLIC_EVIDENCE_SCHEMA,
        "fixture_kind": fixture_kind,
        "acceptance": _validated_acceptance(evidence["acceptance"]),
        "source": _validated_source(evidence["source"]),
        "scene": _validated_scene(evidence["scene"]),
        "training": _validated_training(evidence["training"]),
        "render": _validated_render(evidence["render"]),
        "viewer": _validated_viewer(evidence["viewer"]),
        "human_review": _validated_human_review(evidence["human_review"]),
        "private_evidence_omitted": list(PRIVATE_EVIDENCE_OMITTED),
    }


def _unique_json(payload: bytes, *, label: str) -> object:
    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ProductionReleaseContractError(
                    f"{label} contains duplicate key {key}"
                )
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=unique_pairs,
        )
    except ProductionReleaseContractError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionReleaseContractError(
            f"{label} is not valid UTF-8 JSON"
        ) from exc


def load_public_evidence_bytes(payload: bytes) -> dict[str, object]:
    parsed = _unique_json(payload, label="public evidence")
    evidence = validate_public_evidence(
        _mapping(parsed, label="public evidence")
    )
    if payload != canonical_json_bytes(evidence):
        raise ProductionReleaseContractError(
            "public evidence bytes are not canonical"
        )
    return evidence


def _validated_artifacts(
    values: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    exact: set[str] = set()
    folded: set[str] = set()
    for raw in values:
        artifact = _mapping(raw, label="release artifact")
        _exact_keys(
            artifact,
            {"path", "role", "bytes", "sha256"},
            label="release artifact",
        )
        try:
            path = safe_posix_member_path(artifact["path"]).as_posix()
        except (ReleaseArchiveError, TypeError) as exc:
            raise ProductionReleaseContractError(
                "release artifact path is invalid"
            ) from exc
        if path in exact:
            raise ProductionReleaseContractError(
                f"duplicate release artifact path: {path}"
            )
        if path.casefold() in folded:
            raise ProductionReleaseContractError(
                f"release artifact case-fold collision: {path}"
            )
        exact.add(path)
        folded.add(path.casefold())
        role = artifact["role"]
        if (
            not isinstance(role, str)
            or not role
            or role != role.strip()
            or any(ord(character) < 32 for character in role)
        ):
            raise ProductionReleaseContractError(
                "release artifact role is invalid"
            )
        byte_length = artifact["bytes"]
        if (
            isinstance(byte_length, bool)
            or not isinstance(byte_length, int)
            or byte_length < 0
        ):
            raise ProductionReleaseContractError(
                "release artifact byte length is invalid"
            )
        artifacts.append(
            {
                "path": path,
                "role": role,
                "bytes": byte_length,
                "sha256": _sha(
                    artifact["sha256"],
                    label="release artifact",
                ),
            }
        )
    if not artifacts:
        raise ProductionReleaseContractError(
            "Production release requires artifacts"
        )
    return sorted(artifacts, key=lambda row: str(row["path"]))


def _validated_protected_roots(values: Iterable[str]) -> list[str]:
    roots: list[str] = []
    folded: set[str] = set()
    for raw in values:
        try:
            root = safe_posix_member_path(raw).as_posix()
        except (ReleaseArchiveError, TypeError) as exc:
            raise ProductionReleaseContractError(
                "protected root path is invalid"
            ) from exc
        if root.casefold() in folded:
            raise ProductionReleaseContractError(
                "protected root case-fold collision"
            )
        folded.add(root.casefold())
        roots.append(root)
    roots.sort()
    required = {"evidence", "pipeline", "scripts", "web"}
    if not required <= set(roots):
        raise ProductionReleaseContractError(
            "Production protected roots are incomplete"
        )
    for index, root in enumerate(roots):
        for other in roots[index + 1 :]:
            if other.startswith(f"{root}/"):
                raise ProductionReleaseContractError(
                    "Production protected roots overlap"
                )
    return roots


def _validated_entrypoints(value: Mapping[str, str]) -> dict[str, str]:
    entrypoints = _mapping(value, label="release entrypoints")
    if dict(entrypoints) != PRODUCTION_ENTRYPOINTS:
        raise ProductionReleaseContractError(
            "Production release entrypoints disagree"
        )
    return dict(PRODUCTION_ENTRYPOINTS)


def _validated_scene_summary(value: object) -> dict[str, object]:
    scene = _mapping(value, label="receipt scene")
    expected_keys = {
        "scene_identity",
        "source_role",
        "quality_role",
        "geometry_usability",
        "units",
        "alignment_status",
        "trust_effect",
    }
    _exact_keys(scene, expected_keys, label="receipt scene")
    if (
        not isinstance(scene["scene_identity"], str)
        or _SCENE_ID.fullmatch(scene["scene_identity"]) is None
        or scene["source_role"] != "production-acceptance"
        or scene["quality_role"] != "production"
        or scene["geometry_usability"] != "metric-aligned"
        or scene["units"] != "meters"
        or scene["alignment_status"] != "aligned"
        or scene["trust_effect"] != "none"
    ):
        raise ProductionReleaseContractError(
            "receipt scene trust summary disagrees"
        )
    return {
        "scene_identity": scene["scene_identity"],
        "source_role": "production-acceptance",
        "quality_role": "production",
        "geometry_usability": "metric-aligned",
        "units": "meters",
        "alignment_status": "aligned",
        "trust_effect": "none",
    }


def _validated_acceptance_summary(value: object) -> dict[str, object]:
    acceptance = _mapping(value, label="receipt acceptance")
    _exact_keys(
        acceptance,
        {
            "report_sha256",
            "decision_sha256",
            "production_release_allowed",
            "public_evidence_path",
            "public_evidence_sha256",
        },
        label="receipt acceptance",
    )
    if acceptance["public_evidence_path"] != "evidence/public-evidence.json":
        raise ProductionReleaseContractError(
            "receipt public evidence path disagrees"
        )
    _true(
        acceptance["production_release_allowed"],
        label="receipt production release decision",
    )
    return {
        "report_sha256": _sha(
            acceptance["report_sha256"],
            label="receipt acceptance report",
        ),
        "decision_sha256": _sha(
            acceptance["decision_sha256"],
            label="receipt acceptance decision",
        ),
        "production_release_allowed": True,
        "public_evidence_path": "evidence/public-evidence.json",
        "public_evidence_sha256": _sha(
            acceptance["public_evidence_sha256"],
            label="receipt public evidence",
        ),
    }


def _validated_receipt(value: Mapping[str, object]) -> dict[str, object]:
    receipt = _mapping(value, label="Production release receipt")
    _exact_keys(
        receipt,
        {
            "schema",
            "version",
            "source",
            "package",
            "scene",
            "acceptance",
            "artifacts",
            "protected_roots",
            "entrypoints",
            "exclusions",
        },
        label="Production release receipt",
    )
    if receipt["schema"] != PRODUCTION_RELEASE_SCHEMA:
        raise ProductionReleaseContractError(
            "Production release schema is unsupported"
        )
    version = receipt["version"]
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise ProductionReleaseContractError(
            "Production release version is invalid"
        )
    source = _mapping(receipt["source"], label="release source")
    _exact_keys(source, {"git_commit", "tag"}, label="release source")
    commit = source["git_commit"]
    if not isinstance(commit, str) or _COMMIT.fullmatch(commit) is None:
        raise ProductionReleaseContractError(
            "Production source commit is invalid"
        )
    if source["tag"] != version:
        raise ProductionReleaseContractError(
            "Production source tag differs from version"
        )
    package = _mapping(receipt["package"], label="release package")
    _exact_keys(
        package,
        {"layout", "immutable", "content_id"},
        label="release package",
    )
    if package["layout"] != PRODUCTION_LAYOUT or package["immutable"] is not True:
        raise ProductionReleaseContractError(
            "Production package layout or immutability disagrees"
        )
    content_id = _sha(package["content_id"], label="package content")
    artifacts_raw = receipt["artifacts"]
    if not isinstance(artifacts_raw, list):
        raise ProductionReleaseContractError(
            "release artifacts must be a list"
        )
    artifacts = _validated_artifacts(artifacts_raw)
    roots_raw = receipt["protected_roots"]
    if not isinstance(roots_raw, list):
        raise ProductionReleaseContractError(
            "protected roots must be a list"
        )
    roots = _validated_protected_roots(roots_raw)
    entrypoints = _validated_entrypoints(
        _mapping(receipt["entrypoints"], label="release entrypoints")
    )
    if receipt["exclusions"] != list(PRODUCTION_EXCLUSIONS):
        raise ProductionReleaseContractError(
            "Production release exclusions disagree"
        )
    scene = _validated_scene_summary(receipt["scene"])
    acceptance = _validated_acceptance_summary(receipt["acceptance"])
    artifact_by_path = {str(row["path"]): row for row in artifacts}
    evidence_artifact = artifact_by_path.get(
        str(acceptance["public_evidence_path"])
    )
    if (
        evidence_artifact is None
        or evidence_artifact["role"] != "public-evidence"
        or evidence_artifact["sha256"]
        != acceptance["public_evidence_sha256"]
    ):
        raise ProductionReleaseContractError(
            "public evidence artifact binding disagrees"
        )
    manifest_artifact = artifact_by_path.get(
        "web/data/recon/recon_manifest.json"
    )
    if manifest_artifact is None or manifest_artifact["role"] != "scene-manifest":
        raise ProductionReleaseContractError(
            "scene manifest artifact binding is missing"
        )

    normalized = {
        "schema": PRODUCTION_RELEASE_SCHEMA,
        "version": version,
        "source": {"git_commit": commit, "tag": version},
        "package": {
            "layout": PRODUCTION_LAYOUT,
            "immutable": True,
            "content_id": content_id,
        },
        "scene": scene,
        "acceptance": acceptance,
        "artifacts": artifacts,
        "protected_roots": roots,
        "entrypoints": entrypoints,
        "exclusions": list(PRODUCTION_EXCLUSIONS),
    }
    unsigned = copy.deepcopy(normalized)
    unsigned["package"]["content_id"] = None
    expected = hashlib.sha256(canonical_json_bytes(unsigned)).hexdigest()
    if content_id != expected:
        raise ProductionReleaseContractError(
            "Production package content ID disagrees"
        )
    return normalized


def build_production_receipt(
    *,
    version: str,
    source_commit: str,
    artifacts: Iterable[Mapping[str, object]],
    protected_roots: Iterable[str],
    entrypoints: Mapping[str, str],
    public_evidence: Mapping[str, object],
) -> dict[str, object]:
    """Build one canonical content-addressed Production receipt."""

    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise ProductionReleaseContractError(
            "Production release version is invalid"
        )
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise ProductionReleaseContractError(
            "Production source commit is invalid"
        )
    evidence = validate_public_evidence(public_evidence)
    evidence_bytes = canonical_json_bytes(evidence)
    normalized_artifacts = _validated_artifacts(artifacts)
    artifact_by_path = {
        str(row["path"]): row
        for row in normalized_artifacts
    }
    evidence_artifact = artifact_by_path.get("evidence/public-evidence.json")
    if (
        evidence_artifact is None
        or evidence_artifact["role"] != "public-evidence"
        or evidence_artifact["bytes"] != len(evidence_bytes)
        or evidence_artifact["sha256"]
        != hashlib.sha256(evidence_bytes).hexdigest()
    ):
        raise ProductionReleaseContractError(
            "public evidence artifact bytes disagree"
        )
    scene = evidence["scene"]
    manifest_artifact = artifact_by_path.get(
        "web/data/recon/recon_manifest.json"
    )
    if (
        manifest_artifact is None
        or manifest_artifact["role"] != "scene-manifest"
        or manifest_artifact["sha256"] != scene["manifest_sha256"]
    ):
        raise ProductionReleaseContractError(
            "scene manifest artifact SHA disagrees"
        )
    acceptance = evidence["acceptance"]
    draft: dict[str, object] = {
        "schema": PRODUCTION_RELEASE_SCHEMA,
        "version": version,
        "source": {
            "git_commit": source_commit,
            "tag": version,
        },
        "package": {
            "layout": PRODUCTION_LAYOUT,
            "immutable": True,
            "content_id": None,
        },
        "scene": {
            "scene_identity": scene["scene_identity"],
            "source_role": "production-acceptance",
            "quality_role": "production",
            "geometry_usability": "metric-aligned",
            "units": "meters",
            "alignment_status": "aligned",
            "trust_effect": "none",
        },
        "acceptance": {
            "report_sha256": acceptance["report_sha256"],
            "decision_sha256": acceptance["decision_sha256"],
            "production_release_allowed": True,
            "public_evidence_path": "evidence/public-evidence.json",
            "public_evidence_sha256": hashlib.sha256(
                evidence_bytes
            ).hexdigest(),
        },
        "artifacts": normalized_artifacts,
        "protected_roots": _validated_protected_roots(
            protected_roots
        ),
        "entrypoints": _validated_entrypoints(entrypoints),
        "exclusions": list(PRODUCTION_EXCLUSIONS),
    }
    draft["package"]["content_id"] = hashlib.sha256(
        canonical_json_bytes(draft)
    ).hexdigest()
    return _validated_receipt(draft)


def load_production_receipt_bytes(payload: bytes) -> dict[str, object]:
    parsed = _unique_json(payload, label="Production release receipt")
    receipt = _validated_receipt(
        _mapping(parsed, label="Production release receipt")
    )
    if payload != canonical_json_bytes(receipt):
        raise ProductionReleaseContractError(
            "Production release receipt bytes are not canonical"
        )
    return receipt
