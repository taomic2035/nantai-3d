from __future__ import annotations

import hashlib

from pipeline.release_archive import canonical_json_bytes

MODELED_ACCEPTANCE_SHA = "a" * 64
MODELED_DECISION_SHA = "b" * 64
MODELED_SCENE_ID = "scene-" + "c" * 64
MODELED_SCENE_MANIFEST = canonical_json_bytes(
    {
        "fixture": "modeled-contract-not-real-release",
        "schema": "nantai.recon-manifest.v1",
    }
)
MODELED_SCENE_MANIFEST_SHA = hashlib.sha256(MODELED_SCENE_MANIFEST).hexdigest()
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


def modeled_public_evidence() -> dict[str, object]:
    return {
        "schema": "nantai.production-public-evidence.v1",
        "fixture_kind": "modeled-contract-not-real-release",
        "acceptance": {
            "report_sha256": MODELED_ACCEPTANCE_SHA,
            "decision_sha256": MODELED_DECISION_SHA,
            "source_role": "production-acceptance",
            "production_release_allowed": True,
            "gates": [
                {"id": gate, "state": "accepted"}
                for gate in PRODUCTION_GATE_IDS
            ],
        },
        "source": {
            "dataset_id_sha256": "d" * 64,
            "capture_manifest_sha256": "e" * 64,
            "rights": {
                "redistribution_allowed": True,
                "release_inclusion_allowed": True,
                "processing_purposes": ["3d-reconstruction"],
            },
        },
        "scene": {
            "scene_identity": MODELED_SCENE_ID,
            "import_receipt_sha256": "f" * 64,
            "manifest_sha256": MODELED_SCENE_MANIFEST_SHA,
            "quality_role": "production",
            "geometry_usability": "metric-aligned",
            "units": "meters",
            "alignment_rms_m": 0.1,
            "gaussian_count": 100_000,
        },
        "training": {
            "closure_sha256": "2" * 64,
            "runtime_decision_sha256": "3" * 64,
            "container_identity_sha256": "4" * 64,
        },
        "render": {
            "policy_sha256": "5" * 64,
            "report_sha256": "6" * 64,
            "accepted": True,
        },
        "viewer": {
            "schema": "nantai.viewer-performance-report.v2",
            "policy_sha256": "7" * 64,
            "report_sha256": "8" * 64,
            "accepted": True,
            "screenshot_count": 3,
        },
        "human_review": {
            "policy_sha256": "9" * 64,
            "review_sha256": "0" * 64,
            "accepted": True,
            "categories": list(VISUAL_CATEGORIES),
        },
        "private_evidence_omitted": list(PRIVATE_EVIDENCE_OMITTED),
    }


def modeled_artifact_records() -> list[dict[str, object]]:
    evidence_bytes = canonical_json_bytes(modeled_public_evidence())
    payloads = {
        "evidence/public-evidence.json": (
            "public-evidence",
            evidence_bytes,
        ),
        "pipeline/runtime.py": ("runtime-code", b"RUNTIME = True\n"),
        "scripts/verify_production_release.py": (
            "offline-verifier",
            b"raise SystemExit(0)\n",
        ),
        "web/data/recon/recon_manifest.json": (
            "scene-manifest",
            MODELED_SCENE_MANIFEST,
        ),
        "web/studio/index.html": ("studio-runtime", b"<h1>Studio</h1>\n"),
        "web/viewer/index.html": ("viewer-runtime", b"<h1>Viewer</h1>\n"),
    }
    return [
        {
            "path": path,
            "role": role,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for path, (role, payload) in reversed(tuple(payloads.items()))
    ]


def modeled_entrypoints() -> dict[str, str]:
    return {
        "scene_manifest": "/web/data/recon/recon_manifest.json",
        "studio": "/web/studio/",
        "viewer": "/web/viewer/",
    }
