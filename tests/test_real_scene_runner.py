from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.real_dataset import DatasetEvidenceError
from pipeline.real_scene_runner import (
    RealSceneBlockedError,
    RealSceneRunner,
    RealSceneSourceIdentity,
    StageExecution,
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
            return StageExecution(
                state=state,
                artifacts=(),
                reason=reason or f"{stage} gate rejected",
            )
        stage_root.mkdir(parents=True, exist_ok=True)
        artifact = stage_root / "artifact.bin"
        artifact.write_bytes(f"{stage}-bytes".encode("ascii"))
        return StageExecution(
            state="completed",
            artifacts=(artifact,),
            alignment_rms_m=self.alignment_rms_m.get(stage),
        )


def _runner(tmp_path, *, role="internal-canary", control_points=None):
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


def test_resume_revalidates_bytes_not_file_existence(tmp_path):
    runner, _operations = _runner(tmp_path)
    receipt = runner.run("fetch")
    artifact = runner.workspace / receipt.outputs[0].path
    artifact.write_bytes(b"x")

    with pytest.raises(DatasetEvidenceError, match="sha256"):
        runner.run("fetch", resume=True)


def test_resume_revalidates_transitive_prerequisite_bytes(tmp_path):
    runner, _operations = _runner(tmp_path)
    runner.run("all")
    fetch_receipt = runner.run("fetch")
    artifact = runner.workspace / fetch_receipt.outputs[0].path
    artifact.write_bytes(b"x")

    with pytest.raises(DatasetEvidenceError, match="sha256"):
        runner.run("serve", resume=True)


def test_internal_canary_all_uses_preview_not_production(tmp_path):
    runner, operations = _runner(tmp_path)

    receipt = runner.run("all")

    assert receipt.stage == "serve"
    assert "train-preview" in operations.calls
    assert "train-production" not in operations.calls


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


def test_unknown_remote_state_requires_explicit_retry_and_is_preserved(
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
    first_paths = tuple(
        (runner.receipt_root / "train-production").glob("*.json")
    )
    with pytest.raises(RealSceneBlockedError, match="explicit retry"):
        runner.run("train-production")

    operations.states["train-production"] = ("completed", None)
    receipt = runner.run("train-production", retry=True)

    assert receipt.status == "completed"
    all_paths = tuple(
        (runner.receipt_root / "train-production").glob("*.json")
    )
    assert len(first_paths) == 1
    assert len(all_paths) == 2


def test_workspace_is_source_parameterized(tmp_path):
    runner, _operations = _runner(tmp_path)

    assert runner.workspace == (
        tmp_path / "real-scene" / "poster" / ("a" * 16)
    )
