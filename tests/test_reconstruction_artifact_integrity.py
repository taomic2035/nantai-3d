"""TDD for the reconstruction artifact integrity verifier.

Closes the gap noted in ``scripts/inspect_recon.py`` header:
"不碰 PLY 字节, 不校验 artifacts.*.sha256, 不重算残差".

This verifier recomputes every declared artifact SHA-256 and size, walks
``chunks.json`` entries, rejects symlinks/path-escapes/duplicates, and
reports ``verified`` / ``mismatch`` / ``unknown`` separately.  It never
promotes ``preview-only`` / ``metric-aligned`` / real-photo / training
trust (HANDOFF-GLM-007 section 4).

These tests are the contract: write them first, then implement the module
until every test passes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from pipeline.reconstruction_artifact_integrity import (
    ArtifactMismatch,
    ArtifactUnknown,
    ArtifactVerification,
    ChunksReport,
    IntegrityReport,
    PathSafetyViolation,
    verify_recon_artifacts,
)

# ---------------------------------------------------------------------------
# Fixtures: build a small but realistic recon directory on disk
# ---------------------------------------------------------------------------


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ply_header(point_count: int, attributes: list[str]) -> bytes:
    """Return a minimal ASCII PLY header for ``point_count`` points."""
    lines = [
        b"ply",
        b"format ascii 1.0",
        b"element vertex " + str(point_count).encode("ascii"),
    ]
    for attr in attributes:
        if attr in ("x", "y", "z", "nx", "ny", "nz", "scale"):
            t = b"float"
        elif attr in ("r", "g", "b"):
            t = b"uchar"
        elif attr.startswith("f_dc_") or attr.startswith("f_rest_"):
            t = b"float"
        elif attr in ("opacity",):
            t = b"float"
        elif attr.startswith("scale_") or attr.startswith("rot_"):
            t = b"float"
        else:
            t = b"float"
        lines.append(b"property " + t + b" " + attr.encode("ascii"))
    lines.append(b"end_header")
    return b"\n".join(lines) + b"\n"


def _ply_body(point_count: int, attributes: list[str]) -> bytes:
    """Return a deterministic ASCII PLY body."""
    rows = []
    for i in range(point_count):
        # Deterministic but varied values per point per attribute.
        rows.append(
            b" ".join(
                b"%g" % (float(i + k) / 10.0) for k in range(len(attributes))
            )
        )
    return b"\n".join(rows) + b"\n"


def _write_ply(path: Path, point_count: int, attributes: list[str]) -> bytes:
    """Write a minimal PLY file and return its raw bytes."""
    body = _ply_body(point_count, attributes)
    payload = _ply_header(point_count, attributes) + body
    path.write_bytes(payload)
    return payload


def _make_clean_manifest(
    recon_dir: Path,
    *,
    with_chunks: bool = False,
) -> tuple[Path, dict]:
    """Build a clean, self-consistent recon directory.

    Returns ``(manifest_path, manifest_dict)``.
    """
    recon_dir.mkdir(parents=True, exist_ok=True)

    full_attrs = ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity", "scale_0"]
    full_payload = _write_ply(recon_dir / "recon_full.ply", 10, full_attrs)

    lod0_attrs = ["x", "y", "z", "r", "g", "b", "scale"]
    lod0_payload = _write_ply(recon_dir / "recon_lod0.ply", 1, lod0_attrs)
    lod1_payload = _write_ply(recon_dir / "recon_lod1.ply", 3, lod0_attrs)
    lod2_payload = _write_ply(recon_dir / "recon_lod2.ply", 6, lod0_attrs)

    manifest = {
        "schema_version": 2,
        "engine": "import",
        "registration_engine": "external",
        "gaussian_count": 10,
        "bounds": {
            "min": [-1.0, -2.0, -3.0],
            "max": [1.0, 2.0, 3.0],
        },
        "spatial_parameters": {
            "frame_id": "synthetic-local",
            "units": "arbitrary",
            "dedup_voxel": 0.0,
            "replace_margin": None,
        },
        "lod": {"0": "recon_lod0.ply", "1": "recon_lod1.ply", "2": "recon_lod2.ply"},
        "full_3dgs": "recon_full.ply",
        "artifacts": {
            "full_3dgs": {
                "path": "recon_full.ply",
                "kind": "3dgs-ply",
                "fidelity": "full-3dgs",
                "sha256": _sha256_bytes(full_payload),
                "bytes": len(full_payload),
                "attributes": full_attrs,
                "sh_degree": 0,
                "immutable": False,
            },
            "lod": {
                "0": {
                    "path": "recon_lod0.ply",
                    "kind": "simple-ply",
                    "fidelity": "dc-point-preview",
                    "sha256": _sha256_bytes(lod0_payload),
                    "bytes": len(lod0_payload),
                    "attributes": lod0_attrs,
                    "sh_degree": None,
                    "immutable": False,
                },
                "1": {
                    "path": "recon_lod1.ply",
                    "kind": "simple-ply",
                    "fidelity": "dc-point-preview",
                    "sha256": _sha256_bytes(lod1_payload),
                    "bytes": len(lod1_payload),
                    "attributes": lod0_attrs,
                    "sh_degree": None,
                    "immutable": False,
                },
                "2": {
                    "path": "recon_lod2.ply",
                    "kind": "simple-ply",
                    "fidelity": "dc-point-preview",
                    "sha256": _sha256_bytes(lod2_payload),
                    "bytes": len(lod2_payload),
                    "attributes": lod0_attrs,
                    "sh_degree": None,
                    "immutable": False,
                },
            },
        },
        "sessions": [
            {"session_id": "external_3dgs", "kind": "photo_batch", "n_images": 0}
        ],
        "coordinate_contract": {
            "pose_frame": {
                "frame_id": "synthetic-local",
                "handedness": "right",
                "axes": "sfm-arbitrary",
                "units": "arbitrary",
                "metric_status": "arbitrary",
                "geo_aligned": "unaligned",
                "provenance": "synthetic",
                "evidence": ["external-3dgs-import", "synthetic-source-declared"],
            },
            "target_frame": {
                "frame_id": "synthetic-local",
                "handedness": "right",
                "axes": "sfm-arbitrary",
                "units": "arbitrary",
                "metric_status": "arbitrary",
                "geo_aligned": "unaligned",
                "provenance": "synthetic",
                "evidence": ["external-3dgs-import", "synthetic-source-declared"],
            },
            "alignment_status": "unaligned",
            "metric_evidence": [
                "external-3dgs-import",
                "synthetic-source-declared",
            ],
            "transform_chain": [],
        },
        "provenance": {
            "requested_reconstruction_engine": "import",
            "actual_reconstruction_engine": "import",
            "requested_registration_engine": "external",
            "actual_registration_engine": "external",
            "synthetic": True,
            "geometry_usability": "preview-proxy",
            "artifact_fidelity": {
                "full_3dgs": "full-3dgs",
                "lod_preview": "dc-point-preview",
            },
            "render_fidelity": "simplified-pbr-not-render-parity",
        },
    }

    if with_chunks:
        chunks_dir = recon_dir / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        # Two small chunks, each with lod0/lod1/lod2 PLYs.
        chunks_list = []
        total_points = 0
        for cx, cy in [(0, 0), (1, 0)]:
            per_chunk_points = 4
            total_points += per_chunk_points
            chunk_files = {}
            for lod in ("0", "1", "2"):
                fname = f"chunk_{cx}_{cy}_lod{lod}.ply"
                _write_ply(
                    chunks_dir / fname, per_chunk_points, lod0_attrs
                )
                chunk_files[lod] = fname
            chunks_list.append(
                {
                    "id": f"{cx}_{cy}",
                    "x": cx,
                    "y": cy,
                    "ply_file": chunk_files["2"],
                    "lod": chunk_files,
                    "point_count": per_chunk_points,
                    "aabb": {
                        "min": [float(cx), float(cy), -1.0],
                        "max": [float(cx) + 1.0, float(cy) + 1.0, 1.0],
                    },
                }
            )
        chunks_manifest = {
            "schema_version": 1,
            "kind": "spatial-chunks",
            "chunk_size_m": 50.0,
            "chunks": chunks_list,
            "lod_fractions": {"0": 0.08, "1": 0.30, "2": 1.0},
            "total_chunks": len(chunks_list),
            "total_points": total_points,
            "bounds": {"min": [0.0, 0.0, -1.0], "max": [2.0, 1.0, 1.0]},
            "extent": {"x_min": 0, "x_max": 1, "y_min": 0, "y_max": 0},
            "source": {
                "frame_id": "synthetic-local",
                "units": "arbitrary",
                "applied_transform_ids": [],
                "geometry_usability": "preview-proxy",
                "recon_manifest_sha256": "0" * 64,
            },
        }
        chunks_path = chunks_dir / "chunks.json"
        chunks_path.write_text(
            json.dumps(chunks_manifest, indent=2, sort_keys=True), "utf-8"
        )
        manifest["artifacts"]["chunks"] = {
            "manifest": "chunks/chunks.json",
            "chunk_size_m": 50.0,
            "total_chunks": len(chunks_list),
            "total_points": total_points,
        }

    manifest_path = recon_dir / "recon_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), "utf-8"
    )
    return manifest_path, manifest


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_manifest_verifies_all_artifacts(tmp_path: Path) -> None:
    """A clean manifest verifies all declared artifacts (SHA+size match)."""
    manifest_path, _ = _make_clean_manifest(tmp_path / "recon")
    report = verify_recon_artifacts(manifest_path)
    assert isinstance(report, IntegrityReport)
    assert len(report.mismatch) == 0
    assert len(report.path_safety_violations) == 0
    assert len(report.duplicate_paths) == 0
    assert len(report.duplicate_json_keys) == 0
    assert len(report.contradictions) == 0
    assert report.trust_preserved is True
    # full_3dgs + 3 LODs = 4 verified entries
    assert len(report.verified) == 4
    for v in report.verified:
        assert isinstance(v, ArtifactVerification)
        assert v.sha256_match is True
        assert v.size_match is True
    # geometry_usability is preserved as-declared, never promoted.
    assert report.geometry_usability == "preview-proxy"
    assert report.chunks_report is None


def test_clean_manifest_with_chunks_verifies_chunk_paths(tmp_path: Path) -> None:
    """A clean manifest with chunks.json verifies every chunk PLY exists."""
    manifest_path, _ = _make_clean_manifest(tmp_path / "recon", with_chunks=True)
    report = verify_recon_artifacts(manifest_path)
    assert len(report.mismatch) == 0
    assert report.chunks_report is not None
    assert isinstance(report.chunks_report, ChunksReport)
    assert report.chunks_report.total_chunks_matches_len is True
    assert report.chunks_report.total_points_matches_sum is True
    assert report.chunks_report.bounds_consistent_with_aabbs is True
    assert report.chunks_report.missing_chunk_files == []
    assert report.chunks_report.duplicate_chunk_paths == []
    assert report.chunks_report.extra_unbound_chunk_files == []
    # 4 verified (full+3 LOD) + 6 chunk PLYs verified to exist
    assert len(report.verified) == 4
    assert report.chunks_report.verified_chunk_files == 6


# ---------------------------------------------------------------------------
# Tampered PLY bytes
# ---------------------------------------------------------------------------


def test_tampered_ply_bytes_detected(tmp_path: Path) -> None:
    """Modifying PLY bytes after manifest creation must be flagged as mismatch."""
    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    # Tamper with the full PLY.
    full_path = tmp_path / "recon" / "recon_full.ply"
    original = full_path.read_bytes()
    tampered = original + b"0 0 0 0 0 0 0 0\n"
    full_path.write_bytes(tampered)
    report = verify_recon_artifacts(manifest_path)
    assert len(report.mismatch) == 1
    m = report.mismatch[0]
    assert isinstance(m, ArtifactMismatch)
    assert m.artifact_key == "full_3dgs"
    assert m.declared_sha256 == manifest["artifacts"]["full_3dgs"]["sha256"]
    assert m.actual_sha256 == _sha256_bytes(tampered)
    assert m.declared_bytes == len(original)
    assert m.actual_bytes == len(tampered)
    assert m.sha256_match is False
    assert m.size_match is False
    # Other artifacts still verify.
    assert len(report.verified) == 3


# ---------------------------------------------------------------------------
# Stale manifest SHA
# ---------------------------------------------------------------------------


def test_stale_manifest_sha_detected(tmp_path: Path) -> None:
    """A manifest SHA that doesn't match the actual file SHA is flagged."""
    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    # Corrupt the SHA in the manifest (keep file intact).
    stale = copy.deepcopy(manifest)
    stale["artifacts"]["lod"]["1"]["sha256"] = "a" * 64
    manifest_path.write_text(json.dumps(stale, indent=2, sort_keys=True), "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert len(report.mismatch) == 1
    m = report.mismatch[0]
    assert m.artifact_key == "lod.1"
    assert m.declared_sha256 == "a" * 64
    assert m.sha256_match is False
    # Size still matches (we only corrupted the SHA).
    assert m.size_match is True


# ---------------------------------------------------------------------------
# Missing chunk
# ---------------------------------------------------------------------------


def test_missing_chunk_ply_detected(tmp_path: Path) -> None:
    """A missing chunk PLY file is reported in chunks_report.missing_chunk_files."""
    manifest_path, _ = _make_clean_manifest(tmp_path / "recon", with_chunks=True)
    # Delete one chunk PLY.
    (tmp_path / "recon" / "chunks" / "chunk_0_0_lod0.ply").unlink()
    report = verify_recon_artifacts(manifest_path)
    assert report.chunks_report is not None
    assert len(report.chunks_report.missing_chunk_files) == 1
    assert "chunk_0_0_lod0.ply" in report.chunks_report.missing_chunk_files[0]


# ---------------------------------------------------------------------------
# Extra unbound chunk
# ---------------------------------------------------------------------------


def test_extra_unbound_chunk_detected(tmp_path: Path) -> None:
    """An extra PLY in the chunks dir not referenced by chunks.json is flagged."""
    manifest_path, _ = _make_clean_manifest(tmp_path / "recon", with_chunks=True)
    # Drop an extra PLY not referenced by chunks.json.
    (tmp_path / "recon" / "chunks" / "chunk_9_9_lod0.ply").write_bytes(b"ply\n")
    report = verify_recon_artifacts(manifest_path)
    assert report.chunks_report is not None
    assert len(report.chunks_report.extra_unbound_chunk_files) == 1
    assert any(
        "chunk_9_9_lod0.ply" in p
        for p in report.chunks_report.extra_unbound_chunk_files
    )


# ---------------------------------------------------------------------------
# Path escape
# ---------------------------------------------------------------------------


def test_path_escape_rejected(tmp_path: Path) -> None:
    """An artifact path that escapes the manifest dir is rejected."""
    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    evil = copy.deepcopy(manifest)
    evil["artifacts"]["full_3dgs"]["path"] = "../evil.ply"
    manifest_path.write_text(json.dumps(evil, indent=2, sort_keys=True), "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert len(report.path_safety_violations) >= 1
    v = report.path_safety_violations[0]
    assert isinstance(v, PathSafetyViolation)
    assert "evil.ply" in v.path or "escape" in v.reason.lower() or ".." in v.path


# ---------------------------------------------------------------------------
# Symlink rejected (skip on platforms without symlink permission)
# ---------------------------------------------------------------------------


def test_symlink_artifact_rejected(tmp_path: Path) -> None:
    """A symlinked artifact file is rejected (fail-closed)."""
    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    real = tmp_path / "recon" / "recon_full.ply"
    link = tmp_path / "recon" / "recon_full_symlink.ply"
    try:
        os.symlink(real, link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    evil = copy.deepcopy(manifest)
    evil["artifacts"]["full_3dgs"]["path"] = "recon_full_symlink.ply"
    manifest_path.write_text(json.dumps(evil, indent=2, sort_keys=True), "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert any(
        "symlink" in v.reason.lower() or "symbolic" in v.reason.lower()
        for v in report.path_safety_violations
    )


# ---------------------------------------------------------------------------
# Duplicate paths
# ---------------------------------------------------------------------------


def test_duplicate_artifact_paths_detected(tmp_path: Path) -> None:
    """Two artifacts pointing to the same file are flagged as duplicate_paths."""
    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    dup = copy.deepcopy(manifest)
    # Point lod.1 at the same file as lod.0.
    dup["artifacts"]["lod"]["1"]["path"] = dup["artifacts"]["lod"]["0"]["path"]
    dup["artifacts"]["lod"]["1"]["sha256"] = dup["artifacts"]["lod"]["0"]["sha256"]
    dup["artifacts"]["lod"]["1"]["bytes"] = dup["artifacts"]["lod"]["0"]["bytes"]
    manifest_path.write_text(json.dumps(dup, indent=2, sort_keys=True), "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert len(report.duplicate_paths) >= 1
    assert "recon_lod0.ply" in report.duplicate_paths[0]


# ---------------------------------------------------------------------------
# Duplicate JSON keys
# ---------------------------------------------------------------------------


def test_duplicate_json_keys_detected(tmp_path: Path) -> None:
    """Duplicate keys in the manifest JSON are flagged."""
    manifest_path, _ = _make_clean_manifest(tmp_path / "recon")
    # Inject a duplicate key by writing raw JSON text.
    raw = manifest_path.read_text("utf-8")
    # Duplicate the "engine" key.
    raw = raw.replace(
        '"engine": "import",',
        '"engine": "import",\n  "engine": "sneaky-duplicate",',
        1,
    )
    manifest_path.write_text(raw, "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert len(report.duplicate_json_keys) >= 1
    assert any("engine" in k for k in report.duplicate_json_keys)


# ---------------------------------------------------------------------------
# Contradictory metric evidence
# ---------------------------------------------------------------------------


def test_contradictory_metric_evidence_flagged(tmp_path: Path) -> None:
    """A manifest claiming metric-aligned but with passed=false evidence is flagged."""
    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    bad = copy.deepcopy(manifest)
    # Claim metric-aligned...
    bad["provenance"]["geometry_usability"] = "metric-aligned"
    bad["coordinate_contract"]["target_frame"]["units"] = "meters"
    bad["coordinate_contract"]["target_frame"]["metric_status"] = "metric"
    bad["coordinate_contract"]["target_frame"]["geo_aligned"] = "aligned"
    bad["coordinate_contract"]["alignment_status"] = "aligned"
    # ...but the sim3 evidence says passed=false (contradiction).
    bad["coordinate_contract"]["metric_evidence"] = [
        "sim3.alignment.v1=" + json.dumps({"passed": False, "rms_m": 999.0}),
    ]
    manifest_path.write_text(json.dumps(bad, indent=2, sort_keys=True), "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert len(report.contradictions) >= 1
    assert any(
        "metric" in c.lower() or "alignment" in c.lower() or "passed" in c.lower()
        for c in report.contradictions
    )
    # Trust is preserved: the verifier did NOT promote to metric-aligned.
    assert report.trust_preserved is True


# ---------------------------------------------------------------------------
# Trust never promoted
# ---------------------------------------------------------------------------


def test_preview_only_not_promoted(tmp_path: Path) -> None:
    """A preview-only manifest stays preview-only after verification."""
    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    report = verify_recon_artifacts(manifest_path)
    assert report.geometry_usability == "preview-proxy"
    assert report.trust_preserved is True
    # No metric-aligned or metric-unaligned promotion.
    assert "metric" not in (report.geometry_usability or "")


def test_metric_aligned_stays_metric_aligned_when_consistent(tmp_path: Path) -> None:
    """A consistent metric-aligned manifest keeps its trust level (no promotion)."""
    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    good = copy.deepcopy(manifest)
    good["provenance"]["geometry_usability"] = "metric-aligned"
    good["coordinate_contract"]["target_frame"]["units"] = "meters"
    good["coordinate_contract"]["target_frame"]["metric_status"] = "metric"
    good["coordinate_contract"]["target_frame"]["geo_aligned"] = "aligned"
    good["coordinate_contract"]["alignment_status"] = "aligned"
    good["coordinate_contract"]["target_frame"]["provenance"] = "measured"
    good["coordinate_contract"]["target_frame"]["evidence"] = [
        "sim3.alignment.v1=" + json.dumps({"passed": True, "rms_m": 0.05}),
    ]
    good["coordinate_contract"]["metric_evidence"] = (
        good["coordinate_contract"]["target_frame"]["evidence"]
    )
    good["provenance"]["synthetic"] = False
    manifest_path.write_text(json.dumps(good, indent=2, sort_keys=True), "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert report.geometry_usability == "metric-aligned"
    assert report.trust_preserved is True
    assert len(report.contradictions) == 0


# ---------------------------------------------------------------------------
# chunks.json structural checks
# ---------------------------------------------------------------------------


def test_chunks_total_chunks_mismatch_detected(tmp_path: Path) -> None:
    """total_chunks != len(chunks) is flagged."""
    manifest_path, _ = _make_clean_manifest(tmp_path / "recon", with_chunks=True)
    chunks_path = tmp_path / "recon" / "chunks" / "chunks.json"
    chunks = json.loads(chunks_path.read_text("utf-8"))
    chunks["total_chunks"] = 99  # wrong
    chunks_path.write_text(json.dumps(chunks, indent=2, sort_keys=True), "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert report.chunks_report is not None
    assert report.chunks_report.total_chunks_matches_len is False


def test_chunks_total_points_mismatch_detected(tmp_path: Path) -> None:
    """total_points != sum(point_count) is flagged."""
    manifest_path, _ = _make_clean_manifest(tmp_path / "recon", with_chunks=True)
    chunks_path = tmp_path / "recon" / "chunks" / "chunks.json"
    chunks = json.loads(chunks_path.read_text("utf-8"))
    chunks["total_points"] = 99999  # wrong
    chunks_path.write_text(json.dumps(chunks, indent=2, sort_keys=True), "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert report.chunks_report is not None
    assert report.chunks_report.total_points_matches_sum is False


# ---------------------------------------------------------------------------
# Missing manifest file
# ---------------------------------------------------------------------------


def test_missing_manifest_file_raises(tmp_path: Path) -> None:
    """A missing manifest path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        verify_recon_artifacts(tmp_path / "does_not_exist.json")


def test_manifest_symlink_rejected(tmp_path: Path) -> None:
    """A symlinked manifest file is rejected (fail-closed)."""
    real_manifest_path, _ = _make_clean_manifest(tmp_path / "recon")
    link_path = tmp_path / "recon_link.json"
    try:
        os.symlink(real_manifest_path, link_path)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises((ValueError, OSError), match="symlink|symbolic"):
        verify_recon_artifacts(link_path)


# ---------------------------------------------------------------------------
# Unknown (no SHA declared)
# ---------------------------------------------------------------------------


def test_artifact_without_sha256_reported_as_unknown(tmp_path: Path) -> None:
    """An artifact entry without a sha256 field is reported as unknown."""
    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    no_sha = copy.deepcopy(manifest)
    del no_sha["artifacts"]["lod"]["2"]["sha256"]
    manifest_path.write_text(json.dumps(no_sha, indent=2, sort_keys=True), "utf-8")
    report = verify_recon_artifacts(manifest_path)
    assert len(report.unknown) == 1
    u = report.unknown[0]
    assert isinstance(u, ArtifactUnknown)
    assert u.artifact_key == "lod.2"
    assert u.reason is not None


# ---------------------------------------------------------------------------
# CLI entry point (scripts/verify_recon_artifacts.py::main)
#
# These tests lock in the exit-code contract that mirrors inspect_recon:
#   0 = no problems
#   2 = any mismatch / path safety / chunks anomaly / contradiction
#   SystemExit = manifest missing / symlink / not a dict (fatal)
# so CI can use this script as a gate identical to inspect_recon.
# ---------------------------------------------------------------------------


def test_cli_clean_manifest_exits_zero(tmp_path: Path, capsys) -> None:
    """A clean manifest exits 0 and prints a human report."""
    from scripts.verify_recon_artifacts import main

    manifest_path, _ = _make_clean_manifest(tmp_path / "recon")
    code = main([str(manifest_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "已验证产物" in out
    assert "full_3dgs" in out
    # Trust preservation line is always present (the whole point of the verifier).
    assert "trust_preserved=True" in out


def test_cli_json_flag_emits_parseable_json(tmp_path: Path, capsys) -> None:
    """--json outputs a parseable IntegrityReport dict."""
    from scripts.verify_recon_artifacts import main

    manifest_path, _ = _make_clean_manifest(tmp_path / "recon")
    code = main([str(manifest_path), "--json"])
    assert code == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["manifest_path"] == str(manifest_path)
    assert len(parsed["verified"]) == 4
    assert parsed["mismatch"] == []
    assert parsed["trust_preserved"] is True


def test_cli_tampered_ply_exits_two(tmp_path: Path, capsys) -> None:
    """A tampered PLY causes exit code 2 (CI gate)."""
    from scripts.verify_recon_artifacts import main

    manifest_path, _ = _make_clean_manifest(tmp_path / "recon")
    full_path = tmp_path / "recon" / "recon_full.ply"
    tampered = full_path.read_bytes() + b"0 0 0 0 0 0 0 0\n"
    full_path.write_bytes(tampered)

    code = main([str(manifest_path)])
    assert code == 2
    out = capsys.readouterr().out
    assert "不匹配" in out
    assert "SHA-256 漂移" in out


def test_cli_stale_sha_exits_two(tmp_path: Path, capsys) -> None:
    """A stale SHA in the manifest causes exit code 2."""
    from scripts.verify_recon_artifacts import main

    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    stale = copy.deepcopy(manifest)
    stale["artifacts"]["lod"]["1"]["sha256"] = "a" * 64
    manifest_path.write_text(json.dumps(stale, indent=2, sort_keys=True), "utf-8")

    code = main([str(manifest_path)])
    assert code == 2
    out = capsys.readouterr().out
    assert "lod.1" in out


def test_cli_missing_chunk_exits_two(tmp_path: Path, capsys) -> None:
    """A missing chunk PLY causes exit code 2."""
    from scripts.verify_recon_artifacts import main

    manifest_path, _ = _make_clean_manifest(tmp_path / "recon", with_chunks=True)
    (tmp_path / "recon" / "chunks" / "chunk_0_0_lod0.ply").unlink()

    code = main([str(manifest_path)])
    assert code == 2
    out = capsys.readouterr().out
    assert "缺失的分块文件" in out


def test_cli_chunks_total_mismatch_exits_two(tmp_path: Path, capsys) -> None:
    """total_chunks != len(chunks) causes exit code 2."""
    from scripts.verify_recon_artifacts import main

    manifest_path, _ = _make_clean_manifest(tmp_path / "recon", with_chunks=True)
    chunks_path = tmp_path / "recon" / "chunks" / "chunks.json"
    chunks = json.loads(chunks_path.read_text("utf-8"))
    chunks["total_chunks"] = 99
    chunks_path.write_text(json.dumps(chunks, indent=2, sort_keys=True), "utf-8")

    code = main([str(manifest_path)])
    assert code == 2


def test_cli_path_escape_exits_two(tmp_path: Path, capsys) -> None:
    """A path escape causes exit code 2."""
    from scripts.verify_recon_artifacts import main

    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    evil = copy.deepcopy(manifest)
    evil["artifacts"]["full_3dgs"]["path"] = "../evil.ply"
    manifest_path.write_text(json.dumps(evil, indent=2, sort_keys=True), "utf-8")

    code = main([str(manifest_path)])
    assert code == 2
    out = capsys.readouterr().out
    assert "路径安全违规" in out


def test_cli_contradiction_exits_two(tmp_path: Path, capsys) -> None:
    """A contradictory metric claim causes exit code 2."""
    from scripts.verify_recon_artifacts import main

    manifest_path, manifest = _make_clean_manifest(tmp_path / "recon")
    bad = copy.deepcopy(manifest)
    bad["provenance"]["geometry_usability"] = "metric-aligned"
    bad["coordinate_contract"]["target_frame"]["units"] = "meters"
    bad["coordinate_contract"]["target_frame"]["metric_status"] = "metric"
    bad["coordinate_contract"]["target_frame"]["geo_aligned"] = "aligned"
    bad["coordinate_contract"]["alignment_status"] = "aligned"
    bad["coordinate_contract"]["metric_evidence"] = [
        "sim3.alignment.v1=" + json.dumps({"passed": False, "rms_m": 999.0}),
    ]
    manifest_path.write_text(json.dumps(bad, indent=2, sort_keys=True), "utf-8")

    code = main([str(manifest_path)])
    assert code == 2
    out = capsys.readouterr().out
    assert "矛盾" in out


def test_cli_missing_manifest_file_raises_systemexit(tmp_path: Path) -> None:
    """A missing manifest path raises SystemExit (shell sees exit 1)."""
    from scripts.verify_recon_artifacts import main

    with pytest.raises(SystemExit) as excinfo:
        main([str(tmp_path / "does_not_exist.json")])
    assert "不存在" in str(excinfo.value) or "not found" in str(excinfo.value).lower()


def test_cli_symlink_manifest_raises_systemexit(tmp_path: Path) -> None:
    """A symlinked manifest raises SystemExit (fail-closed)."""
    from scripts.verify_recon_artifacts import main

    real_manifest_path, _ = _make_clean_manifest(tmp_path / "recon")
    link_path = tmp_path / "recon_link.json"
    try:
        os.symlink(real_manifest_path, link_path)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(SystemExit) as excinfo:
        main([str(link_path)])
    assert "symlink" in str(excinfo.value).lower() or "不可校验" in str(excinfo.value)


def test_cli_json_on_problems_still_exits_two(tmp_path: Path, capsys) -> None:
    """--json with problems still reports exit code 2 (CI gate)."""
    from scripts.verify_recon_artifacts import main

    manifest_path, _ = _make_clean_manifest(tmp_path / "recon")
    full_path = tmp_path / "recon" / "recon_full.ply"
    full_path.write_bytes(full_path.read_bytes() + b"tampered\n")

    code = main([str(manifest_path), "--json"])
    assert code == 2
    parsed = json.loads(capsys.readouterr().out)
    assert len(parsed["mismatch"]) == 1
    assert parsed["mismatch"][0]["artifact_key"] == "full_3dgs"
    # Trust never promoted, even when bytes mismatch.
    assert parsed["trust_preserved"] is True
