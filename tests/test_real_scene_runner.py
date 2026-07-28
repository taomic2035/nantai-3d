from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.real_scene_runner as runner_module
from pipeline.real_dataset import (
    DatasetEvidenceError,
    HfDatasetSource,
    LocalCaptureSource,
    canonical_model_bytes,
)
from pipeline.real_scene_runner import (
    RealSceneBlockedError,
    RealSceneRunner,
    RealSceneRunOptions,
    RealSceneSourceIdentity,
    RealSceneStatusError,
    StageArtifactBinding,
    StageExecution,
    StagePrerequisiteBinding,
    StageReceipt,
    canonical_snapshot_bytes,
    canonical_stage_receipt_bytes,
    resolve_latest_production_import,
    run_real_scene,
    snapshot_real_scene_stages,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
def test_hash_artifact_supports_windows_long_path(tmp_path):
    """Long-path evidence keeps its normal workspace-relative receipt path."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    desired_parent_length = 210
    padding = desired_parent_length - len(str(workspace.resolve())) - 1
    assert 1 <= padding <= 255
    parent = workspace / ("w" * padding)
    parent.mkdir()
    artifact = parent / ("source_manifest_" + "a" * 64 + ".json")
    assert len(str(artifact.resolve())) > 260
    extended_artifact = Path("\\\\?\\" + str(artifact.resolve()))
    extended_artifact.write_bytes(b"evidence")

    binding = runner_module._hash_artifact(artifact, workspace=workspace)

    assert binding.path == artifact.relative_to(workspace).as_posix()
    assert binding.byte_length == 8
    assert binding.sha256 == hashlib.sha256(b"evidence").hexdigest()


class _Operations:
    def __init__(self, *, role="internal-canary"):
        self.role = role
        self.calls: list[str] = []
        self.states: dict[str, tuple[str, str | None]] = {}
        self.alignment_rms_m: dict[str, float | None] = {}

    def execute(self, stage, stage_root, prerequisite_receipts):
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
        artifacts = (artifact,)
        if stage == "import":
            import_receipt = stage_root / "import-receipt.json"
            import_receipt.write_text(
                '{"schema":"fixture-import"}\n',
                encoding="ascii",
            )
            artifacts = (artifact, import_receipt)
        if stage == "accept":
            assert len(prerequisite_receipts) == 1
            import_bindings = tuple(
                output
                for output in prerequisite_receipts[0].outputs
                if Path(output.path).name == "import-receipt.json"
            )
            assert len(import_bindings) == 1
            payload = (
                json.dumps(
                    {
                        "import_receipt_sha256": (
                            import_bindings[0].sha256
                        ),
                        "role": self.role,
                        "schema": "fixture-acceptance",
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            ).encode("ascii")
            digest = hashlib.sha256(payload).hexdigest()
            acceptance = stage_root / f"real-scene-acceptance-{digest}.json"
            acceptance.write_bytes(payload)
            return StageExecution(
                state="completed",
                artifacts=(artifact, acceptance),
                alignment_rms_m=self.alignment_rms_m.get(stage),
            )
        return StageExecution(
            state="completed",
            artifacts=artifacts,
            alignment_rms_m=self.alignment_rms_m.get(stage),
        )


def _runner(
    tmp_path,
    *,
    role="internal-canary",
    control_points=None,
    now=None,
):
    operations = _Operations(role=role)
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


def _patch_fixture_acceptance(monkeypatch):
    def validate(
        path,
        *,
        expected_import_receipt_sha256=None,
    ):
        payload = json.loads(path.read_text(encoding="ascii"))
        role = payload["role"]
        assert (
            payload["import_receipt_sha256"]
            == expected_import_receipt_sha256
        )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return SimpleNamespace(
            source_role=role,
            report_sha256=digest,
            production_release_allowed=role == "production-acceptance",
            canary_accepted=role == "internal-canary",
        )

    monkeypatch.setattr(
        "pipeline.real_scene_acceptance.validate_real_scene_acceptance",
        validate,
    )


def test_resume_revalidates_transitive_prerequisite_bytes(
    tmp_path,
    monkeypatch,
):
    _patch_fixture_acceptance(monkeypatch)
    runner, _operations = _runner(tmp_path)
    runner.run("all")
    fetch_receipt = runner.run("fetch")
    artifact = runner.workspace / fetch_receipt.outputs[0].path
    artifact.write_bytes(b"x")

    with pytest.raises(RealSceneBlockedError, match="revalidation failed"):
        runner.run("accept", resume=True)

    receipts = tuple((runner.receipt_root / "accept").glob("*.json"))
    assert len(receipts) == 2
    assert any(
        json.loads(path.read_text(encoding="ascii"))["status"] == "blocked"
        for path in receipts
    )


def test_internal_canary_all_uses_preview_not_production(
    tmp_path,
    monkeypatch,
):
    _patch_fixture_acceptance(monkeypatch)
    runner, operations = _runner(tmp_path)

    receipt = runner.run("all")

    assert receipt.stage == "accept"
    assert "train-preview" in operations.calls
    assert "train-production" not in operations.calls
    assert "serve" not in operations.calls
    assert not (runner.receipt_root / "serve").exists()


def test_production_all_stops_at_authoritative_accept(
    tmp_path,
    monkeypatch,
):
    _patch_fixture_acceptance(monkeypatch)
    control_points = _control_points(tmp_path / "control-points.json")
    runner, operations = _runner(
        tmp_path,
        role="production-acceptance",
        control_points=control_points,
    )
    operations.alignment_rms_m["import"] = 0.1
    monkeypatch.setattr(
        runner,
        "_verify_production_import_output",
        lambda **_kwargs: 0.1,
    )

    receipt = runner.run("all")

    assert receipt.stage == "accept"
    assert operations.calls == [
        "fetch",
        "sfm",
        "train-production",
        "import",
        "accept",
    ]
    assert not (runner.receipt_root / "serve").exists()


def test_serve_is_not_a_durable_runner_stage(tmp_path):
    runner, operations = _runner(tmp_path)

    with pytest.raises(RealSceneBlockedError, match="unknown real-scene target"):
        runner.run("serve")

    assert operations.calls == []
    assert not (runner.receipt_root / "serve").exists()


def test_snapshot_ignores_legacy_serve_receipt_directory(
    tmp_path,
    monkeypatch,
):
    _patch_fixture_acceptance(monkeypatch)
    runner, _operations = _runner(tmp_path)
    runner.run("all")
    legacy = runner.receipt_root / "serve"
    legacy.mkdir()
    (legacy / "legacy.json").write_text(
        '{"legacy":true}\n',
        encoding="ascii",
    )

    snapshot = runner.snapshot_stages(run_id="canary-001")

    assert snapshot.state == "accepted-from-authoritative-decision"
    assert tuple(stage.stage for stage in snapshot.stages) == (
        "fetch",
        "sfm",
        "train-preview",
        "import",
        "accept",
    )


def test_snapshot_rejects_latest_completed_receipts_from_different_chains(
    tmp_path,
    monkeypatch,
):
    _patch_fixture_acceptance(monkeypatch)
    runner, _operations = _runner(tmp_path)
    runner.run("all")
    latest_import = runner._latest("import")
    assert latest_import is not None
    receipt, _digest = latest_import
    runner._write_receipt(
        receipt.model_copy(
            update={
                "attempt_id": "attempt-newer-unaccepted-import",
                "created_at_utc": (
                    receipt.created_at_utc + timedelta(seconds=1)
                ),
            }
        )
    )

    with pytest.raises(
        RealSceneStatusError,
        match="coherent chain",
    ):
        runner.snapshot_stages(run_id="canary-001")


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


def test_production_import_rejects_low_rms_without_metric_evidence(
    tmp_path,
):
    control_points = _control_points(tmp_path / "control-points.json")
    runner, operations = _runner(
        tmp_path,
        role="production-acceptance",
        control_points=control_points,
    )
    operations.alignment_rms_m["import"] = 0.1

    with pytest.raises(RealSceneBlockedError, match="import receipt"):
        runner.run("import")

    receipt_path = next(
        (runner.receipt_root / "import").glob("*.json")
    )
    payload = json.loads(receipt_path.read_text(encoding="ascii"))
    assert payload["status"] == "blocked"
    assert payload["alignment_rms_m"] == pytest.approx(0.1)
    assert payload["outputs"] == []
    assert "import receipt" in payload["reason"]
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


def _production_import_locator_fixture(tmp_path, monkeypatch):
    source = LocalCaptureSource(
        schema="nantai.real-dataset-source.v1",
        dataset_id="village-a",
        role="production-acceptance",
        source_kind="local-capture",
        rights_receipt_sha256="b" * 64,
        redistribution_allowed=False,
        release_inclusion_allowed=False,
    )
    source_path = tmp_path / "source.json"
    source_bytes = canonical_model_bytes(source)
    source_path.write_bytes(source_bytes)
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    workspace_base = tmp_path / "real-scene"
    run_id = "production-a"
    workspace = (
        workspace_base
        / run_id
        / source.dataset_id
        / source_sha[:16]
    )
    attempt_id = "attempt-import-one"
    import_root = workspace / "stages/import" / attempt_id
    import_root.mkdir(parents=True)
    import_receipt_path = import_root / "import-receipt.json"
    import_payload = b'{"schema":"fixture"}\n'
    import_receipt_path.write_bytes(import_payload)
    binding = StageArtifactBinding(
        path=import_receipt_path.relative_to(workspace).as_posix(),
        byte_length=len(import_payload),
        sha256=hashlib.sha256(import_payload).hexdigest(),
    )
    receipt = StageReceipt(
        dataset_id=source.dataset_id,
        source_sha256=source_sha,
        stage="import",
        attempt_id=attempt_id,
        created_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        status="completed",
        prerequisites=(),
        outputs=(binding,),
        alignment_rms_m=0.1,
    )
    payload = canonical_stage_receipt_bytes(receipt)
    receipt_dir = workspace / "receipts/import"
    receipt_dir.mkdir(parents=True)
    receipt_path = (
        receipt_dir / f"{hashlib.sha256(payload).hexdigest()}.json"
    )
    receipt_path.write_bytes(payload)
    monkeypatch.setattr(
        "pipeline.real_scene_import.validate_real_scene_import_receipt",
        lambda path, root: (
            SimpleNamespace(source_role="production-acceptance")
            if path == import_receipt_path and root == import_root
            else pytest.fail("resolver used a different import")
        ),
    )
    return {
        "source": source,
        "source_path": source_path,
        "source_sha": source_sha,
        "workspace_base": workspace_base,
        "run_id": run_id,
        "workspace": workspace,
        "import_root": import_root,
        "import_receipt_path": import_receipt_path,
        "stage_receipt_path": receipt_path,
    }


def test_latest_production_import_resolver_returns_content_bound_stage(
    tmp_path,
    monkeypatch,
):
    fixture = _production_import_locator_fixture(
        tmp_path,
        monkeypatch,
    )

    resolved = resolve_latest_production_import(
        fixture["source_path"],
        workspace_base=fixture["workspace_base"],
        run_id=fixture["run_id"],
    )

    assert resolved.workspace_root == fixture["workspace"]
    assert resolved.import_root == fixture["import_root"]
    assert resolved.import_receipt_path == fixture["import_receipt_path"]
    assert resolved.stage_receipt_path == fixture["stage_receipt_path"]
    assert (
        resolved.stage_receipt_sha256
        == fixture["stage_receipt_path"].stem
    )
    assert resolved.source_sha256 == fixture["source_sha"]


def test_latest_production_import_resolver_rejects_newer_blocked_receipt(
    tmp_path,
    monkeypatch,
):
    fixture = _production_import_locator_fixture(
        tmp_path,
        monkeypatch,
    )
    blocked = StageReceipt(
        dataset_id=fixture["source"].dataset_id,
        source_sha256=fixture["source_sha"],
        stage="import",
        attempt_id="attempt-import-two",
        created_at_utc=(
            datetime(2026, 7, 27, tzinfo=UTC)
            + timedelta(seconds=1)
        ),
        status="blocked",
        prerequisites=(),
        outputs=(),
        reason="fresh import evidence rejected",
    )
    payload = canonical_stage_receipt_bytes(blocked)
    path = (
        fixture["workspace"]
        / "receipts/import"
        / f"{hashlib.sha256(payload).hexdigest()}.json"
    )
    path.write_bytes(payload)

    with pytest.raises(
        RealSceneBlockedError,
        match="latest production import is blocked",
    ):
        resolve_latest_production_import(
            fixture["source_path"],
            workspace_base=fixture["workspace_base"],
            run_id=fixture["run_id"],
        )


def test_latest_production_import_resolver_rejects_artifact_tamper(
    tmp_path,
    monkeypatch,
):
    fixture = _production_import_locator_fixture(
        tmp_path,
        monkeypatch,
    )
    fixture["import_receipt_path"].write_bytes(b"tampered\n")

    with pytest.raises(DatasetEvidenceError, match="sha256"):
        resolve_latest_production_import(
            fixture["source_path"],
            workspace_base=fixture["workspace_base"],
            run_id=fixture["run_id"],
        )


@pytest.mark.parametrize("chunk_size", [True, "50", float("nan"), 0, -1])
def test_run_options_reject_invalid_chunk_size(chunk_size):
    with pytest.raises(ValueError, match="chunk_size"):
        RealSceneRunOptions(chunk_size=chunk_size)


# ---------------------------------------------------------------------------
# snapshot_stages / snapshot_real_scene_stages tests
# ---------------------------------------------------------------------------


class _SnapshotOperations:
    def execute(self, stage, stage_root, prerequisite_receipts):
        del stage_root, prerequisite_receipts
        raise AssertionError(f"snapshot executed {stage}")


def _snapshot_runner(
    tmp_path,
    *,
    role="internal-canary",
    run_id="default",
):
    runner = RealSceneRunner(
        source=RealSceneSourceIdentity(
            dataset_id="poster",
            role=role,
            source_sha256="a" * 64,
        ),
        workspace_base=tmp_path / "real-scene",
        operations=_SnapshotOperations(),
    )
    return runner


def _publish_completed_receipt(
    runner,
    stage,
    *,
    attempt_id,
    artifacts,
    prerequisites=(),
    created_at=None,
    alignment_rms_m=None,
):
    """Write a canonical completed StageReceipt with real artifact bindings."""
    if created_at is None:
        created_at = datetime(2026, 7, 27, tzinfo=UTC)
    bindings = tuple(
        StageArtifactBinding(
            path=artifact.relative_to(runner.workspace).as_posix(),
            byte_length=artifact.stat().st_size,
            sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        )
        for artifact in artifacts
    )
    receipt = StageReceipt(
        dataset_id=runner.source.dataset_id,
        source_sha256=runner.source.source_sha256,
        stage=stage,
        attempt_id=attempt_id,
        created_at_utc=created_at,
        status="completed",
        prerequisites=prerequisites,
        outputs=bindings,
        alignment_rms_m=alignment_rms_m,
    )
    payload = canonical_stage_receipt_bytes(receipt)
    directory = runner.receipt_root / stage
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hashlib.sha256(payload).hexdigest()}.json"
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _publish_blocked_receipt(
    runner,
    stage,
    *,
    attempt_id,
    reason,
    evidence_artifacts=(),
    created_at=None,
):
    if created_at is None:
        created_at = datetime(2026, 7, 27, tzinfo=UTC)
    evidence = tuple(
        StageArtifactBinding(
            path=artifact.relative_to(runner.workspace).as_posix(),
            byte_length=artifact.stat().st_size,
            sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        )
        for artifact in evidence_artifacts
    )
    receipt = StageReceipt(
        dataset_id=runner.source.dataset_id,
        source_sha256=runner.source.source_sha256,
        stage=stage,
        attempt_id=attempt_id,
        created_at_utc=created_at,
        status="blocked",
        prerequisites=(),
        evidence=evidence,
        outputs=(),
        reason=reason,
    )
    payload = canonical_stage_receipt_bytes(receipt)
    directory = runner.receipt_root / stage
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hashlib.sha256(payload).hexdigest()}.json"
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _publish_unknown_receipt(
    runner,
    stage,
    *,
    attempt_id,
    reason,
    evidence_artifacts=(),
    created_at=None,
):
    if created_at is None:
        created_at = datetime(2026, 7, 27, tzinfo=UTC)
    evidence = tuple(
        StageArtifactBinding(
            path=artifact.relative_to(runner.workspace).as_posix(),
            byte_length=artifact.stat().st_size,
            sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        )
        for artifact in evidence_artifacts
    )
    receipt = StageReceipt(
        dataset_id=runner.source.dataset_id,
        source_sha256=runner.source.source_sha256,
        stage=stage,
        attempt_id=attempt_id,
        created_at_utc=created_at,
        status="unknown",
        prerequisites=(),
        evidence=evidence,
        outputs=(),
        reason=reason,
    )
    payload = canonical_stage_receipt_bytes(receipt)
    directory = runner.receipt_root / stage
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{hashlib.sha256(payload).hexdigest()}.json"
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _make_artifact(runner, stage, attempt_id, name="artifact.bin"):
    stage_root = runner.workspace / "stages" / stage / attempt_id
    stage_root.mkdir(parents=True, exist_ok=True)
    artifact = stage_root / name
    artifact.write_bytes(f"{stage}-{attempt_id}".encode("ascii"))
    return artifact


def _build_completed_chain(
    runner,
    *,
    up_to="accept",
    monkeypatch=None,
    acceptance_allowed=True,
):
    """Build a completed receipt chain up to a given stage."""
    stages_canary = ("fetch", "sfm", "train-preview", "import", "accept")
    stages_prod = ("fetch", "sfm", "train-production", "import", "accept")
    chain = (
        stages_prod
        if runner.source.role == "production-acceptance"
        else stages_canary
    )
    limit = chain.index(up_to) + 1 if up_to in chain else len(chain)
    prev = None
    for stage in chain[:limit]:
        artifacts = [
            _make_artifact(
                runner,
                stage,
                f"attempt-{stage}",
                name=(
                    "import-receipt.json"
                    if stage == "import"
                    else "artifact.bin"
                ),
            )
        ]
        prereqs = ()
        if prev is not None:
            prereqs = (
                StagePrerequisiteBinding(
                    stage=prev[0],
                    receipt_sha256=prev[1],
                ),
            )
        if stage == "accept":
            acceptance_path, acceptance_sha = _make_acceptance_report(
                runner,
                import_receipt_sha256=hashlib.sha256(
                    (
                        runner.workspace
                        / "stages/import/attempt-import/import-receipt.json"
                    ).read_bytes()
                ).hexdigest(),
                monkeypatch=monkeypatch,
                allowed=acceptance_allowed,
            )
            artifacts.append(acceptance_path)
        alignment_rms_m = None
        if (
            stage == "import"
            and runner.source.role == "production-acceptance"
        ):
            alignment_rms_m = 0.1
            if monkeypatch is not None:
                monkeypatch.setattr(
                    runner,
                    "_verify_production_import_output",
                    lambda **_kwargs: 0.1,
                )
        _, digest = _publish_completed_receipt(
            runner,
            stage,
            attempt_id=f"attempt-{stage}",
            artifacts=artifacts,
            prerequisites=prereqs,
            alignment_rms_m=alignment_rms_m,
        )
        prev = (stage, digest)
    return prev


def _make_acceptance_report(
    runner,
    *,
    import_receipt_sha256,
    monkeypatch=None,
    allowed=True,
):
    """Create a fake acceptance report file and patch the validator."""
    stage_root = runner.workspace / "stages/accept/attempt-accept"
    stage_root.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            {
                "import_receipt_sha256": import_receipt_sha256,
                "schema": "fixture-acceptance",
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    digest = hashlib.sha256(payload).hexdigest()
    report_path = stage_root / f"real-scene-acceptance-{digest}.json"
    report_path.write_bytes(payload)
    if monkeypatch is not None:
        decision = SimpleNamespace(
            source_role=runner.source.role,
            report_sha256=digest,
            production_release_allowed=allowed,
            canary_accepted=allowed,
        )
        monkeypatch.setattr(
            "pipeline.real_scene_acceptance.validate_real_scene_acceptance",
            lambda path, *, expected_import_receipt_sha256=None: (
                decision
                if (
                    json.loads(path.read_text(encoding="ascii"))[
                        "import_receipt_sha256"
                    ]
                    == expected_import_receipt_sha256
                )
                else pytest.fail(
                    "acceptance/import receipt binding differs"
                )
            ),
        )
    return report_path, digest


def test_snapshot_missing_all_stages(tmp_path):
    runner = _snapshot_runner(tmp_path)
    runner.receipt_root.mkdir(parents=True, exist_ok=True)

    snapshot = runner.snapshot_stages(run_id="default")

    assert snapshot.state == "blocked"
    assert len(snapshot.stages) == 5
    assert snapshot.stages[0].status == "missing"
    assert snapshot.earliest_blocker.stage == "fetch"
    assert snapshot.earliest_blocker.reason_code == "receipt-missing"
    assert snapshot.acceptance.decision == "not-reached"
    assert snapshot.run_id == "default"


def test_snapshot_partial_completed(tmp_path):
    runner = _snapshot_runner(tmp_path)
    _build_completed_chain(runner, up_to="sfm")

    snapshot = runner.snapshot_stages(run_id="default")

    assert snapshot.state == "blocked"
    assert snapshot.stages[0].status == "completed"
    assert snapshot.stages[1].status == "completed"
    assert snapshot.stages[2].status == "missing"
    assert snapshot.earliest_blocker.stage == "train-preview"


def test_snapshot_blocked_stage_reports_earliest(tmp_path):
    runner = _snapshot_runner(tmp_path)
    _build_completed_chain(runner, up_to="fetch")
    evidence = _make_artifact(runner, "sfm", "attempt-sfm", "failure.json")
    _publish_blocked_receipt(
        runner,
        "sfm",
        attempt_id="attempt-sfm",
        reason="registration quality rejected",
        evidence_artifacts=(evidence,),
    )

    snapshot = runner.snapshot_stages(run_id="default")

    assert snapshot.state == "blocked"
    assert snapshot.stages[1].status == "blocked"
    assert snapshot.stages[1].reason_code == "stage-blocked"
    assert snapshot.earliest_blocker.stage == "sfm"
    payload = canonical_snapshot_bytes(snapshot).decode("ascii")
    assert "registration quality rejected" not in payload


def test_snapshot_unknown_stage(tmp_path):
    runner = _snapshot_runner(tmp_path)
    _build_completed_chain(runner, up_to="fetch")
    evidence = _make_artifact(runner, "sfm", "attempt-sfm", "probe.json")
    _publish_unknown_receipt(
        runner,
        "sfm",
        attempt_id="attempt-sfm",
        reason="remote host unreachable",
        evidence_artifacts=(evidence,),
    )

    snapshot = runner.snapshot_stages(run_id="default")

    assert snapshot.stages[1].status == "unknown"
    assert snapshot.stages[1].reason_code == "stage-unknown"
    assert snapshot.earliest_blocker.stage == "sfm"


def test_snapshot_completed_revalidate_prerequisite_chain(tmp_path):
    runner = _snapshot_runner(tmp_path)
    _build_completed_chain(runner, up_to="sfm")
    sfm_dir = runner.receipt_root / "sfm"
    receipt_path = next(sfm_dir.glob("*.json"))
    receipt = StageReceipt.model_validate_json(receipt_path.read_bytes())
    drifted = receipt.model_copy(
        update={
            "prerequisites": (
                StagePrerequisiteBinding(
                    stage="fetch",
                    receipt_sha256="b" * 64,
                ),
            )
        }
    )
    payload = canonical_stage_receipt_bytes(drifted)
    receipt_path.unlink()
    sfm_dir.joinpath(
        f"{hashlib.sha256(payload).hexdigest()}.json"
    ).write_bytes(payload)

    with pytest.raises(RealSceneStatusError):
        runner.snapshot_stages(run_id="default")


def test_snapshot_completed_revalidate_artifact_bindings(tmp_path):
    runner = _snapshot_runner(tmp_path)
    _build_completed_chain(runner, up_to="sfm")
    fetch_dir = runner.receipt_root / "fetch"
    receipt_path = next(fetch_dir.glob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="ascii"))
    artifact = runner.workspace / receipt["outputs"][0]["path"]
    artifact.write_bytes(b"tampered")

    with pytest.raises(RealSceneStatusError):
        runner.snapshot_stages(run_id="default")


def test_snapshot_import_completed_validates_import_receipt(tmp_path, monkeypatch):
    runner = _snapshot_runner(tmp_path, role="production-acceptance")
    _build_completed_chain(
        runner,
        up_to="import",
        monkeypatch=monkeypatch,
    )
    calls = []
    monkeypatch.setattr(
        runner,
        "_verify_production_import_output",
        lambda **kwargs: calls.append(kwargs) or 0.1,
    )

    snapshot = runner.snapshot_stages(run_id="default")

    assert snapshot.stages[3].status == "completed"
    assert snapshot.acceptance.decision == "not-reached"
    assert len(calls) == 1


@pytest.mark.parametrize("role", ["production-acceptance", "internal-canary"])
def test_snapshot_accept_completed_allowed(tmp_path, monkeypatch, role):
    runner = _snapshot_runner(tmp_path, role=role)
    _build_completed_chain(
        runner,
        up_to="accept",
        monkeypatch=monkeypatch,
        acceptance_allowed=True,
    )

    snapshot = runner.snapshot_stages(run_id="default")

    assert snapshot.state == "accepted-from-authoritative-decision"
    assert snapshot.acceptance.decision == "allowed-from-authoritative-decision"
    assert snapshot.acceptance.acceptance_source == "real-scene-acceptance"
    assert snapshot.acceptance.acceptance_report_sha256 is not None
    assert snapshot.earliest_blocker is None
    assert all(s.status == "completed" for s in snapshot.stages)


def test_snapshot_accept_completed_but_validator_contradicts(tmp_path, monkeypatch):
    runner = _snapshot_runner(tmp_path)
    _build_completed_chain(
        runner,
        up_to="accept",
        monkeypatch=monkeypatch,
        acceptance_allowed=False,
    )

    with pytest.raises(RealSceneStatusError):
        runner.snapshot_stages(run_id="default")


def test_snapshot_foreign_receipt_or_toctou(tmp_path):
    runner = _snapshot_runner(tmp_path)
    runner.receipt_root.mkdir(parents=True, exist_ok=True)
    foreign_dir = runner.receipt_root / "fetch"
    foreign_dir.mkdir(parents=True, exist_ok=True)
    artifact = _make_artifact(
        runner,
        "fetch",
        "attempt-foreign",
    )
    foreign = StageReceipt(
        dataset_id="other-dataset",
        source_sha256="b" * 64,
        stage="fetch",
        attempt_id="attempt-foreign",
        created_at_utc=datetime(2026, 7, 27, tzinfo=UTC),
        status="completed",
        prerequisites=(),
        outputs=(
            StageArtifactBinding(
                path=artifact.relative_to(
                    runner.workspace
                ).as_posix(),
                byte_length=artifact.stat().st_size,
                sha256=hashlib.sha256(
                    artifact.read_bytes()
                ).hexdigest(),
            ),
        ),
    )
    payload = canonical_stage_receipt_bytes(foreign)
    foreign_dir.joinpath(
        f"{hashlib.sha256(payload).hexdigest()}.json"
    ).write_bytes(payload)

    with pytest.raises(RealSceneStatusError):
        runner.snapshot_stages(run_id="default")


def test_snapshot_deterministic_bytes(tmp_path, monkeypatch):
    runner = _snapshot_runner(tmp_path)
    _build_completed_chain(runner, up_to="sfm")

    first = runner.snapshot_stages(run_id="default")
    second = runner.snapshot_stages(run_id="default")

    assert canonical_snapshot_bytes(first) == canonical_snapshot_bytes(second)
    assert first.report_sha256 == second.report_sha256


def test_snapshot_public_helper_rejects_linklike_source(tmp_path):
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
    real_source = tmp_path / "source.json"
    real_source.write_bytes(canonical_model_bytes(source))
    link = tmp_path / "source-link.json"
    try:
        link.symlink_to(real_source)
    except OSError:
        pytest.skip("source symlink creation is unavailable")

    with pytest.raises(RealSceneStatusError):
        snapshot_real_scene_stages(
            link,
            workspace_base=tmp_path / "workspace",
            run_id="canary-001",
        )


def test_snapshot_public_helper_rejects_linklike_workspace(tmp_path):
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
    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    link = tmp_path / "workspace-link"
    try:
        link.symlink_to(real_workspace, target_is_directory=True)
    except OSError:
        pytest.skip("workspace symlink creation is unavailable")

    with pytest.raises(RealSceneStatusError):
        snapshot_real_scene_stages(
            source_path,
            workspace_base=link,
            run_id="canary-001",
        )


def test_snapshot_rejects_workspace_below_linklike_ancestor(tmp_path):
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
    real_parent = tmp_path / "real-parent"
    workspace = real_parent / "workspace"
    workspace.mkdir(parents=True)
    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(
            real_parent,
            target_is_directory=True,
        )
    except OSError:
        pytest.skip("ancestor symlink creation is unavailable")

    with pytest.raises(RealSceneStatusError, match="link-like"):
        snapshot_real_scene_stages(
            source_path,
            workspace_base=linked_parent / "workspace",
            run_id="canary-001",
        )
