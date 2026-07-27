from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline.real_dataset import DatasetEvidenceError, HfDatasetSource, canonical_model_bytes
from pipeline.real_scene_runner import (
    RealSceneBlockedError,
    RealSceneRunner,
    RealSceneRunOptions,
    RealSceneSourceIdentity,
    StageExecution,
    run_real_scene,
)


class _Operations:
    def __init__(self):
        self.calls: list[str] = []
        self.states: dict[str, tuple[str, str | None]] = {}
        self.alignment_rms_m: dict[str, float | None] = {}

    def execute(self, stage, stage_root, prerequisite_receipts):
        del prerequisite_receipts
        self.calls.append(stage)
        state, reason = self.states.get(stage, ("completed", None))
        if state != "completed":
            stage_root.mkdir(parents=True, exist_ok=True)
            evidence = stage_root / "failure-evidence.json"
            evidence.write_text(
                f'{{"stage":"{stage}","state":"{state}"}}\n',
                encoding="ascii",
            )
            return StageExecution(
                state=state,
                artifacts=(),
                reason=reason or f"{stage} gate rejected",
                evidence_artifacts=(evidence,),
            )
        stage_root.mkdir(parents=True, exist_ok=True)
        artifact = stage_root / "artifact.bin"
        artifact.write_bytes(f"{stage}-bytes".encode("ascii"))
        return StageExecution(
            state="completed",
            artifacts=(artifact,),
            alignment_rms_m=self.alignment_rms_m.get(stage),
        )


def _runner(
    tmp_path,
    *,
    role="internal-canary",
    control_points=None,
    now=None,
):
    operations = _Operations()
    runner = RealSceneRunner(
        source=RealSceneSourceIdentity(
            dataset_id="poster",
            role=role,
            source_sha256="a" * 64,
        ),
        workspace_base=tmp_path / "real-scene",
        operations=operations,
        control_points_path=control_points,
        geo_origin=(31.2, 121.5, 4.0) if control_points else None,
        now=now,
    )
    return runner, operations


def _control_points(path: Path, *, coplanar: bool = False) -> Path:
    points = [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0 if coplanar else 1.0],
    ]
    import json

    path.write_text(
        json.dumps(
            [
                {
                    "label": f"gcp-{index}",
                    "source_xyz": point,
                    "enu_xyz": point,
                }
                for index, point in enumerate(points)
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_all_stops_before_training_when_sfm_rejected(tmp_path):
    runner, operations = _runner(tmp_path)
    operations.states["sfm"] = (
        "blocked",
        "registration quality rejected",
    )

    with pytest.raises(RealSceneBlockedError, match="registration"):
        runner.run("all")

    assert operations.calls == ["fetch", "sfm"]
    receipt_path = next(
        (runner.receipt_root / "sfm").glob("*.json")
    )
    assert '"evidence":[' in receipt_path.read_text(encoding="ascii")


def test_blocked_stage_evidence_is_revalidated_before_retry(tmp_path):
    runner, operations = _runner(tmp_path)
    operations.states["sfm"] = ("blocked", "quality rejected")
    with pytest.raises(RealSceneBlockedError):
        runner.run("sfm")
    receipt_path = next(
        (runner.receipt_root / "sfm").glob("*.json")
    )
    receipt = __import__("json").loads(
        receipt_path.read_text(encoding="ascii")
    )
    evidence = runner.workspace / receipt["evidence"][0]["path"]
    evidence.write_text("tampered\n", encoding="ascii")

    with pytest.raises(DatasetEvidenceError, match="sha256"):
        runner.run("sfm", retry=True)


def test_resume_byte_tamper_records_blocked_revalidation_receipt(tmp_path):
    runner, operations = _runner(tmp_path)
    receipt = runner.run("fetch")
    completed_path = next((runner.receipt_root / "fetch").glob("*.json"))
    completed_bytes = completed_path.read_bytes()
    artifact = runner.workspace / receipt.outputs[0].path
    artifact.write_bytes(b"x")

    with pytest.raises(RealSceneBlockedError, match="revalidation failed"):
        runner.run("fetch", resume=True)

    receipt_paths = tuple(sorted((runner.receipt_root / "fetch").glob("*.json")))
    assert len(receipt_paths) == 2
    assert completed_path.read_bytes() == completed_bytes
    documents = tuple(json.loads(path.read_text(encoding="ascii")) for path in receipt_paths)
    blocked = next(document for document in documents if document["status"] == "blocked")
    assert blocked["outputs"] == []
    assert len(blocked["evidence"]) == 1
    assert "sha256/size mismatch" in blocked["reason"]
    failure_path = runner.workspace / blocked["evidence"][0]["path"]
    failure = json.loads(failure_path.read_text(encoding="ascii"))
    assert failure == {
        "dataset_id": "poster",
        "detected_at_utc": failure["detected_at_utc"],
        "failure_kind": "artifact-integrity",
        "previous_receipt_sha256": completed_path.stem,
        "reason": blocked["reason"],
        "schema": "nantai.stage-revalidation-failure.v1",
        "source_sha256": "a" * 64,
        "stage": "fetch",
    }

    with pytest.raises(RealSceneBlockedError, match="explicit retry"):
        runner.run("fetch")
    recovered = runner.run("fetch", retry=True)

    assert recovered.status == "completed"
    assert len(tuple((runner.receipt_root / "fetch").glob("*.json"))) == 3
    assert operations.calls.count("fetch") == 2


def test_resume_revalidates_transitive_prerequisite_bytes(tmp_path):
    runner, _operations = _runner(tmp_path)
    runner.run("all")
    fetch_receipt = runner.run("fetch")
    artifact = runner.workspace / fetch_receipt.outputs[0].path
    artifact.write_bytes(b"x")

    with pytest.raises(RealSceneBlockedError, match="revalidation failed"):
        runner.run("serve", resume=True)

    receipts = tuple((runner.receipt_root / "serve").glob("*.json"))
    assert len(receipts) == 2
    assert any(
        json.loads(path.read_text(encoding="ascii"))["status"] == "blocked"
        for path in receipts
    )


def test_internal_canary_all_uses_preview_not_production(tmp_path):
    runner, operations = _runner(tmp_path)

    receipt = runner.run("all")

    assert receipt.stage == "serve"
    assert "train-preview" in operations.calls
    assert "train-production" not in operations.calls


def test_internal_canary_import_prefers_existing_production_training(tmp_path):
    runner, operations = _runner(tmp_path)

    runner.run("train-production")
    runner.run("import")

    assert operations.calls.count("train-production") == 1
    assert "train-preview" not in operations.calls


def test_production_import_requires_measured_control_points(tmp_path):
    runner, operations = _runner(
        tmp_path,
        role="production-acceptance",
    )

    with pytest.raises(RealSceneBlockedError, match="control points"):
        runner.run("import")

    assert "import" not in operations.calls


def test_production_import_rejects_fewer_or_coplanar_points(tmp_path):
    fewer = _control_points(tmp_path / "fewer.json")
    import json

    payload = json.loads(fewer.read_text(encoding="utf-8"))
    fewer.write_text(json.dumps(payload[:3]), encoding="utf-8")
    runner, _operations = _runner(
        tmp_path / "fewer-run",
        role="production-acceptance",
        control_points=fewer,
    )
    with pytest.raises(RealSceneBlockedError, match="four"):
        runner.run("import")

    coplanar = _control_points(
        tmp_path / "coplanar.json",
        coplanar=True,
    )
    runner, _operations = _runner(
        tmp_path / "coplanar-run",
        role="production-acceptance",
        control_points=coplanar,
    )
    with pytest.raises(RealSceneBlockedError, match="non-coplanar"):
        runner.run("import")


@pytest.mark.parametrize("rms", [None, 0.2500001])
def test_production_import_persists_bad_rms_as_blocked(tmp_path, rms):
    control_points = _control_points(tmp_path / "control-points.json")
    runner, operations = _runner(
        tmp_path,
        role="production-acceptance",
        control_points=control_points,
    )
    operations.alignment_rms_m["import"] = rms

    with pytest.raises(RealSceneBlockedError, match="0.25"):
        runner.run("import")

    receipt_paths = tuple(
        (runner.receipt_root / "import").glob("*.json")
    )
    assert len(receipt_paths) == 1
    assert '"status":"blocked"' in receipt_paths[0].read_text(
        encoding="ascii"
    )
    with pytest.raises(RealSceneBlockedError, match="explicit retry"):
        runner.run("import")


def test_unknown_remote_state_resumes_the_same_attempt(
    tmp_path,
):
    control_points = _control_points(tmp_path / "control-points.json")
    runner, operations = _runner(
        tmp_path,
        role="production-acceptance",
        control_points=control_points,
        now=lambda: datetime(2026, 7, 27, tzinfo=UTC),
    )
    operations.states["train-production"] = (
        "unknown",
        "remote host unreachable",
    )

    with pytest.raises(RealSceneBlockedError, match="unreachable"):
        runner.run("train-production")
    first_paths = tuple(
        (runner.receipt_root / "train-production").glob("*.json")
    )
    with pytest.raises(RealSceneBlockedError, match="explicit retry"):
        runner.run("train-production")
    first_payload = json.loads(
        first_paths[0].read_text(encoding="ascii")
    )

    operations.states["train-production"] = ("completed", None)
    receipt = runner.run("train-production", resume=True)

    assert receipt.status == "completed"
    assert receipt.attempt_id == first_payload["attempt_id"]
    assert receipt.created_at_utc > datetime.fromisoformat(
        first_payload["created_at_utc"],
    )
    all_paths = tuple(
        (runner.receipt_root / "train-production").glob("*.json")
    )
    assert len(first_paths) == 1
    assert len(all_paths) == 2
    assert runner.run("train-production").status == "completed"


def test_unknown_remote_state_explicit_retry_uses_new_attempt(
    tmp_path,
):
    control_points = _control_points(tmp_path / "control-points.json")
    runner, operations = _runner(
        tmp_path,
        role="production-acceptance",
        control_points=control_points,
    )
    operations.states["train-production"] = (
        "unknown",
        "remote host unreachable",
    )
    with pytest.raises(RealSceneBlockedError, match="unreachable"):
        runner.run("train-production")
    first_path = next(
        (runner.receipt_root / "train-production").glob("*.json")
    )
    first = json.loads(first_path.read_text(encoding="ascii"))

    operations.states["train-production"] = ("completed", None)
    retried = runner.run("train-production", retry=True)

    assert retried.status == "completed"
    assert retried.attempt_id != first["attempt_id"]


def test_workspace_is_source_parameterized(tmp_path):
    runner, _operations = _runner(tmp_path)

    assert runner.workspace == (
        tmp_path / "real-scene" / "poster" / ("a" * 16)
    )


def test_run_real_scene_binds_canonical_source_and_run_id(tmp_path):
    source = HfDatasetSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="poster",
        role="internal-canary",
        source_kind="hf-dataset",
        repository="owner/repo",
        repository_revision="4" * 40,
        subtree="poster",
        capture_subtree="poster/images",
        declared_file_count=1,
        declared_total_bytes=5,
        license_status="not-declared",
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )
    source_path = tmp_path / "source.json"
    source_path.write_bytes(canonical_model_bytes(source))
    operations = _Operations()

    receipt = run_real_scene(
        source_path,
        "fetch",
        RealSceneRunOptions(
            workspace_base=tmp_path / "real-scene",
            run_id="canary-001",
        ),
        operations=operations,
    )

    source_sha = __import__("hashlib").sha256(
        canonical_model_bytes(source)
    ).hexdigest()
    assert receipt.source_sha256 == source_sha
    assert (
        tmp_path
        / "real-scene"
        / "canary-001"
        / "poster"
        / source_sha[:16]
    ).is_dir()


@pytest.mark.parametrize("chunk_size", [True, "50", float("nan"), 0, -1])
def test_run_options_reject_invalid_chunk_size(chunk_size):
    with pytest.raises(ValueError, match="chunk_size"):
        RealSceneRunOptions(chunk_size=chunk_size)
