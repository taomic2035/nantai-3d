from __future__ import annotations

import hashlib
import json
import struct
import zipfile
import zlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import pipeline.real_scene_acceptance as acceptance_module
from pipeline.real_scene_acceptance import (
    REQUIRED_VISUAL_CATEGORIES,
    AcceptanceDirectoryReference,
    AcceptanceEvidenceReference,
    HumanReviewPolicy,
    HumanVisualReview,
    RealSceneAcceptance,
    RealSceneAcceptanceError,
    RealSceneAcceptancePointer,
    canonical_human_review_bytes,
    canonical_human_review_policy_bytes,
    canonical_real_scene_acceptance_bytes,
    canonical_real_scene_acceptance_pointer_bytes,
    load_latest_real_scene_acceptance,
    publish_real_scene_acceptance,
    publish_real_scene_acceptance_pointer,
    record_human_visual_review,
    validate_human_visual_review,
    validate_real_scene_acceptance,
)
from pipeline.real_scene_training import (
    HeldOutSplit,
    TrainingImageIdentity,
    held_out_split_canonical_bytes,
)
from pipeline.render_evaluation import (
    RenderCameraRecord,
    RenderEvaluationPolicy,
    RenderEvaluationProtocol,
    RenderEvaluationReport,
    RenderFrameMetric,
    canonical_render_evaluation_bytes,
    render_artifact_stem,
    render_evaluation_sha256,
)
from pipeline.viewer_acceptance import ViewerPerformancePolicy
from scripts.record_real_scene_review import main as record_review_main

POSES = (
    "pose-" + "a" * 64,
    "pose-" + "b" * 64,
    "pose-" + "c" * 64,
)


def _png(width: int = 4, height: int = 3) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    scanline = b"\x00" + b"\x20\x40\x60" * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(
            b"IHDR",
            struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
        + chunk(b"IDAT", zlib.compress(scanline * height))
        + chunk(b"IEND", b"")
    )


def _policy() -> HumanReviewPolicy:
    return HumanReviewPolicy(
        source_role="production-acceptance",
        required_categories=REQUIRED_VISUAL_CATEGORIES,
        required_pose_ids=POSES,
        maximum_screenshot_bytes=10_000,
    )


def _fixture(tmp_path):
    root = tmp_path / "run"
    shots = root / "review-shots"
    shots.mkdir(parents=True)
    screenshot_paths = {}
    for index, pose_id in enumerate(POSES):
        relative = f"review-shots/shot-{index}.png"
        (root / relative).write_bytes(_png())
        screenshot_paths[pose_id] = relative
    review = record_human_visual_review(
        policy=_policy(),
        root=root,
        reviewer="Reviewer One",
        dispositions={category: "accepted" for category in REQUIRED_VISUAL_CATEGORIES},
        screenshots=screenshot_paths,
        reviewed_at=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
    )
    return root, review


def test_all_explicit_visual_categories_and_bound_screenshots_pass(tmp_path):
    root, review = _fixture(tmp_path)

    decision = validate_human_visual_review(_policy(), review, root)

    assert decision.accepted is True
    assert decision.unknown_categories == ()
    assert decision.rejected_categories == ()
    assert decision.screenshot_count == 3
    assert review.review_id.startswith("human-review-")
    assert canonical_human_review_bytes(review).endswith(b"\n")


def test_missing_disposition_is_unknown_never_accepted(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    screenshots = {}
    for index, pose_id in enumerate(POSES):
        path = root / f"{index}.png"
        path.write_bytes(_png())
        screenshots[pose_id] = path.name
    dispositions = {category: "accepted" for category in REQUIRED_VISUAL_CATEGORIES[:-1]}

    review = record_human_visual_review(
        policy=_policy(),
        root=root,
        reviewer="Reviewer One",
        dispositions=dispositions,
        screenshots=screenshots,
        reviewed_at=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
    )
    decision = validate_human_visual_review(_policy(), review, root)

    assert decision.accepted is False
    assert decision.unknown_categories == (REQUIRED_VISUAL_CATEGORIES[-1],)


def test_rejected_human_disposition_cannot_pass(tmp_path):
    root, review = _fixture(tmp_path)
    dispositions = {category: "accepted" for category in REQUIRED_VISUAL_CATEGORIES}
    dispositions[REQUIRED_VISUAL_CATEGORIES[0]] = "rejected"
    rejected = record_human_visual_review(
        policy=_policy(),
        root=root,
        reviewer=review.reviewer,
        dispositions=dispositions,
        screenshots={screenshot.pose_id: screenshot.path for screenshot in review.screenshots},
        reviewed_at=review.reviewed_at,
    )

    decision = validate_human_visual_review(
        _policy(),
        rejected,
        root,
    )

    assert decision.accepted is False
    assert decision.rejected_categories == (REQUIRED_VISUAL_CATEGORIES[0],)


def test_screenshot_byte_tamper_is_rejected(tmp_path):
    root, review = _fixture(tmp_path)
    (root / review.screenshots[0].path).write_bytes(_png(5, 3))

    with pytest.raises(
        RealSceneAcceptanceError,
        match="screenshot.*(SHA|byte|dimensions)",
    ):
        validate_human_visual_review(_policy(), review, root)


def test_crc_valid_but_undecodable_png_is_rejected(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    screenshots = {}
    for index, pose_id in enumerate(POSES):
        path = root / f"{index}.png"
        path.write_bytes(_png())
        screenshots[pose_id] = path.name

    corrupt = bytearray((root / "0.png").read_bytes())
    idat = corrupt.index(b"IDAT")
    length = struct.unpack(">I", corrupt[idat - 4 : idat])[0]
    payload_start = idat + 4
    corrupt[payload_start : payload_start + length] = b"x" * length
    crc = zlib.crc32(b"IDAT" + corrupt[payload_start : payload_start + length]) & 0xFFFFFFFF
    corrupt[payload_start + length : payload_start + length + 4] = struct.pack(">I", crc)
    (root / "0.png").write_bytes(corrupt)

    with pytest.raises(
        RealSceneAcceptanceError,
        match="decode",
    ):
        record_human_visual_review(
            policy=_policy(),
            root=root,
            reviewer="Reviewer One",
            dispositions={},
            screenshots=screenshots,
            reviewed_at=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
        )


def test_screenshot_symlink_is_rejected(tmp_path):
    root, review = _fixture(tmp_path)
    original = root / review.screenshots[0].path
    target = root / "target.png"
    target.write_bytes(original.read_bytes())
    original.unlink()
    try:
        original.symlink_to(target)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip(
                "Windows SeCreateSymbolicLinkPrivilege not held"
            )
        raise

    with pytest.raises(RealSceneAcceptanceError, match="symlink"):
        validate_human_visual_review(_policy(), review, root)


def test_screenshot_escape_is_rejected(tmp_path):
    root, review = _fixture(tmp_path)
    escaped_binding = review.screenshots[0].model_copy(update={"path": "../escape.png"})
    escaped = review.model_copy(
        update={
            "screenshots": (
                escaped_binding,
                *review.screenshots[1:],
            )
        }
    )
    with pytest.raises(RealSceneAcceptanceError, match="relative"):
        validate_human_visual_review(_policy(), escaped, root)


def test_review_authored_aggregate_boolean_is_forbidden(tmp_path):
    root, review = _fixture(tmp_path)
    forged = review.model_copy(update={"accepted": True})

    with pytest.raises(RealSceneAcceptanceError, match="authored"):
        validate_human_visual_review(_policy(), forged, root)


def _cli_fixture(tmp_path):
    root = tmp_path / "run"
    root.mkdir()
    policy_path = root / "human-review-policy.json"
    policy_path.write_bytes(canonical_human_review_policy_bytes(_policy()))
    args = [
        "--run-root",
        str(root),
        "--reviewer",
        "Reviewer One",
        "--policy",
        str(policy_path),
        "--reviewed-at",
        "2026-07-26T12:00:00Z",
    ]
    for category in REQUIRED_VISUAL_CATEGORIES:
        args.extend(["--disposition", f"{category}=accepted"])
    for index, pose_id in enumerate(POSES):
        relative = f"shot-{index}.png"
        (root / relative).write_bytes(_png())
        args.extend(["--screenshot", f"{pose_id}={relative}"])
    return root, args


def test_human_review_cli_writes_canonical_accepted_receipt(
    tmp_path,
    capsys,
):
    root, args = _cli_fixture(tmp_path)

    exit_code = record_review_main(args)

    assert exit_code == 0
    output = root / "evidence/human-visual-review.json"
    payload = output.read_bytes()
    review = HumanVisualReview.model_validate_json(payload)
    assert payload == canonical_human_review_bytes(review)
    assert validate_human_visual_review(_policy(), review, root).accepted is True
    assert "ACCEPTED" in capsys.readouterr().out


def test_human_review_cli_flush_failure_leaves_no_final_receipt(
    tmp_path,
    monkeypatch,
):
    root, args = _cli_fixture(tmp_path)

    def fail_flush(_path):
        raise OSError("simulated flush failure")

    monkeypatch.setattr("pipeline.durable_io.flush_file", fail_flush)

    exit_code = record_review_main(args)

    assert exit_code == 2
    assert not (root / "evidence/human-visual-review.json").exists()
    assert not tuple((root / "evidence").glob(".*.staging"))


def test_human_review_cli_records_missing_category_as_unknown(
    tmp_path,
    capsys,
):
    root, args = _cli_fixture(tmp_path)
    omitted = f"{REQUIRED_VISUAL_CATEGORIES[-1]}=accepted"
    index = args.index(omitted)
    del args[index - 1 : index + 1]

    exit_code = record_review_main(args)

    assert exit_code == 2
    output = root / "evidence/human-visual-review.json"
    review = HumanVisualReview.model_validate_json(output.read_bytes())
    decision = validate_human_visual_review(_policy(), review, root)
    assert decision.unknown_categories == (REQUIRED_VISUAL_CATEGORIES[-1],)
    assert "PENDING" in capsys.readouterr().out


def _acceptance_reference(root, relative: str) -> AcceptanceEvidenceReference:
    payload = f"fixture:{relative}\n".encode("ascii")
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return AcceptanceEvidenceReference(
        path=relative,
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_length=len(payload),
    )


def _acceptance_report(tmp_path, *, role: str) -> tuple:
    root = tmp_path / f"acceptance-{role}"
    root.mkdir()
    directories = {
        name: AcceptanceDirectoryReference(path=name)
        for name in (
            "fetch",
            "capture",
            "sfm",
            "training",
            "imported",
        )
    }
    directories["render"] = AcceptanceDirectoryReference(path="training/remote-result")
    for directory in directories.values():
        (root / directory.path).mkdir(parents=True, exist_ok=True)
    refs = {
        "source": _acceptance_reference(root, "fetch/source.json"),
        "capture_manifest": _acceptance_reference(
            root,
            "capture/manifest.json",
        ),
        "prepared_capture_evidence": _acceptance_reference(
            root,
            "sfm/prepared-capture-evidence.json",
        ),
        "registration": _acceptance_reference(
            root,
            "sfm/registration.json",
        ),
        "registration_policy": _acceptance_reference(
            root,
            "sfm/registration-policy.json",
        ),
        "registration_report": _acceptance_reference(
            root,
            "sfm/registration-report.json",
        ),
        "training_bundle": _acceptance_reference(
            root,
            "training/training-bundle/training-job.zip",
        ),
        "import_receipt": _acceptance_reference(
            root,
            "imported/import-receipt.json",
        ),
        "render_policy": _acceptance_reference(
            root,
            "training/remote-result/render-evaluation/policy.json",
        ),
        "render_report": _acceptance_reference(
            root,
            "training/remote-result/render-evaluation/report.json",
        ),
        "viewer_policy": _acceptance_reference(
            root,
            "viewer-policy.json",
        ),
        "viewer_report": _acceptance_reference(
            root,
            "viewer-report.json",
        ),
        "human_review_policy": _acceptance_reference(
            root,
            "human-policy.json",
        ),
        "human_visual_review": _acceptance_reference(
            root,
            "human-review.json",
        ),
    }
    if role == "internal-canary":
        refs["dataset_lock"] = _acceptance_reference(
            root,
            "fetch/dataset-lock.json",
        )
        refs["dataset_receipt"] = _acceptance_reference(
            root,
            "fetch/dataset-receipt.json",
        )
        rights = None
        dataset_lock = refs["dataset_lock"]
        dataset_receipt = refs["dataset_receipt"]
    else:
        rights = _acceptance_reference(
            root,
            "fetch/capture-rights-receipt.json",
        )
        dataset_lock = None
        dataset_receipt = None
    report = RealSceneAcceptance(
        source_role=role,
        source=refs["source"],
        rights_receipt=rights,
        fetch_root=directories["fetch"],
        dataset_lock=dataset_lock,
        dataset_receipt=dataset_receipt,
        capture_bundle=directories["capture"],
        capture_manifest=refs["capture_manifest"],
        prepared_capture_evidence=refs["prepared_capture_evidence"],
        sfm_root=directories["sfm"],
        registration=refs["registration"],
        registration_policy=refs["registration_policy"],
        registration_report=refs["registration_report"],
        training_root=directories["training"],
        training_bundle=refs["training_bundle"],
        import_root=directories["imported"],
        import_receipt=refs["import_receipt"],
        render_root=directories["render"],
        render_policy=refs["render_policy"],
        render_report=refs["render_report"],
        viewer_policy=refs["viewer_policy"],
        viewer_report=refs["viewer_report"],
        human_review_policy=refs["human_review_policy"],
        human_visual_review=refs["human_visual_review"],
    )
    path = root / "real-scene-acceptance.json"
    path.write_bytes(canonical_real_scene_acceptance_bytes(report))
    return root, path, report


def _accepted_evidence(*, role: str):
    return acceptance_module._ValidatedAcceptanceEvidence(
        release_rights_allowed=role == "production-acceptance",
        sfm_accepted=True,
        training_quality_role="production",
        geometry_usability=(
            "metric-aligned" if role == "production-acceptance" else "preview-only"
        ),
        target_units=("meters" if role == "production-acceptance" else "arbitrary"),
        alignment_rms_m=(0.1 if role == "production-acceptance" else None),
        render_accepted=True,
        render_failures=(),
        viewer_accepted=True,
        viewer_failures=(),
        human_accepted=True,
        human_failures=(),
    )


def test_internal_canary_acceptance_never_unblocks_release(
    tmp_path,
    monkeypatch,
):
    _root, path, _report = _acceptance_report(
        tmp_path,
        role="internal-canary",
    )
    monkeypatch.setattr(
        acceptance_module,
        "_validate_acceptance_evidence",
        lambda *_args, **_kwargs: _accepted_evidence(role="internal-canary"),
    )

    decision = validate_real_scene_acceptance(path)

    assert decision.canary_accepted is True
    assert decision.production_release_allowed is False
    assert decision.technical_accepted is True


def test_acceptance_publication_is_content_addressed_and_idempotent(
    tmp_path,
    monkeypatch,
):
    root, _path, report = _acceptance_report(
        tmp_path,
        role="internal-canary",
    )
    monkeypatch.setattr(
        acceptance_module,
        "_validate_acceptance_evidence",
        lambda *_args, **_kwargs: _accepted_evidence(role="internal-canary"),
    )

    first_path, first_decision = publish_real_scene_acceptance(
        report,
        root,
    )
    second_path, second_decision = publish_real_scene_acceptance(
        report,
        root,
    )

    assert first_path == second_path
    assert first_decision == second_decision
    assert first_path.name == (f"real-scene-acceptance-{first_decision.report_sha256}.json")
    assert first_path.read_bytes() == canonical_real_scene_acceptance_bytes(report)


def test_acceptance_publication_flush_failure_leaves_no_final_report(
    tmp_path,
    monkeypatch,
):
    root, _path, report = _acceptance_report(
        tmp_path,
        role="internal-canary",
    )

    def fail_flush(_path):
        raise OSError("simulated flush failure")

    monkeypatch.setattr(
        acceptance_module,
        "_validate_acceptance_evidence",
        lambda *_args, **_kwargs: _accepted_evidence(role="internal-canary"),
    )
    monkeypatch.setattr("pipeline.durable_io.flush_file", fail_flush)

    with pytest.raises(
        RealSceneAcceptanceError,
        match="cannot be published",
    ):
        publish_real_scene_acceptance(report, root)

    assert not tuple(root.glob("real-scene-acceptance-*.json"))
    assert not tuple(root.glob(".*.staging"))


def test_latest_acceptance_pointer_is_relative_content_bound_and_idempotent(
    tmp_path,
):
    root = tmp_path / "real-scene"
    payload = b'{"schema":"fixture"}\n'
    report = root / "run-a" / (
        f"real-scene-acceptance-{hashlib.sha256(payload).hexdigest()}.json"
    )
    report.parent.mkdir(parents=True)
    report.write_bytes(payload)

    first = publish_real_scene_acceptance_pointer(report, root)
    second = publish_real_scene_acceptance_pointer(report, root)

    assert first == second == root / "latest-acceptance.json"
    pointer_model = RealSceneAcceptancePointer.model_validate_json(first.read_bytes())
    assert first.read_bytes() == canonical_real_scene_acceptance_pointer_bytes(
        pointer_model
    )
    assert load_latest_real_scene_acceptance(root) == report
    pointer = json.loads(first.read_bytes())
    assert pointer["report_path"] == f"run-a/{report.name}"
    assert set(pointer) == {
        "schema",
        "report_path",
        "report_sha256",
        "report_byte_length",
    }


def test_latest_acceptance_pointer_flush_failure_preserves_previous_pointer(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "real-scene"
    first_payload = b'{"schema":"first"}\n'
    first = root / "run-a" / (
        f"real-scene-acceptance-{hashlib.sha256(first_payload).hexdigest()}.json"
    )
    first.parent.mkdir(parents=True)
    first.write_bytes(first_payload)
    pointer = publish_real_scene_acceptance_pointer(first, root)
    previous = pointer.read_bytes()

    second_payload = b'{"schema":"second"}\n'
    second = root / "run-b" / (
        f"real-scene-acceptance-{hashlib.sha256(second_payload).hexdigest()}.json"
    )
    second.parent.mkdir()
    second.write_bytes(second_payload)

    def fail_flush(_path):
        raise OSError("simulated flush failure")

    monkeypatch.setattr("pipeline.durable_io.flush_file", fail_flush)

    with pytest.raises(
        RealSceneAcceptanceError,
        match="cannot be published",
    ):
        publish_real_scene_acceptance_pointer(second, root)

    assert pointer.read_bytes() == previous
    assert not tuple(root.glob(".latest-acceptance-*.tmp"))


def test_latest_acceptance_pointer_rejects_report_tamper_and_unsafe_paths(
    tmp_path,
):
    root = tmp_path / "real-scene"
    root.mkdir()
    payload = b'{"schema":"fixture"}\n'
    report = root / (
        f"real-scene-acceptance-{hashlib.sha256(payload).hexdigest()}.json"
    )
    report.write_bytes(payload)
    pointer = publish_real_scene_acceptance_pointer(report, root)

    report.write_bytes(b'{"schema":"tampered"}\n')
    with pytest.raises(RealSceneAcceptanceError, match="SHA|byte"):
        load_latest_real_scene_acceptance(root)

    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside\n")
    pointer.write_text(
        json.dumps(
            {
                "schema": "nantai.real-scene-acceptance-pointer.v1",
                "report_path": "../outside.json",
                "report_sha256": hashlib.sha256(b"outside\n").hexdigest(),
                "report_byte_length": len(b"outside\n"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RealSceneAcceptanceError, match="pointer"):
        load_latest_real_scene_acceptance(root)


def test_production_acceptance_requires_rights_metric_and_every_gate(
    tmp_path,
    monkeypatch,
):
    _root, path, _report = _acceptance_report(
        tmp_path,
        role="production-acceptance",
    )
    accepted = _accepted_evidence(role="production-acceptance")
    monkeypatch.setattr(
        acceptance_module,
        "_validate_acceptance_evidence",
        lambda *_args, **_kwargs: accepted,
    )
    assert validate_real_scene_acceptance(path).production_release_allowed is True

    for update, gate in (
        ({"release_rights_allowed": False}, "release-rights"),
        ({"geometry_usability": "preview-only"}, "metric-alignment"),
        ({"training_quality_role": "preview-only"}, "production-training"),
        (
            {
                "viewer_accepted": False,
                "viewer_failures": ("p95 frame time exceeded",),
            },
            "viewer-performance",
        ),
    ):
        rejected = accepted.__class__(
            **{
                **accepted.__dict__,
                **update,
            }
        )
        monkeypatch.setattr(
            acceptance_module,
            "_validate_acceptance_evidence",
            lambda *_args, _result=rejected, **_kwargs: _result,
        )
        decision = validate_real_scene_acceptance(path)
        assert decision.production_release_allowed is False
        assert gate in decision.failed_gates


def test_aggregate_rejects_reference_tamper_before_derivation(
    tmp_path,
    monkeypatch,
):
    root, path, report = _acceptance_report(
        tmp_path,
        role="internal-canary",
    )
    monkeypatch.setattr(
        acceptance_module,
        "_validate_acceptance_evidence",
        lambda *_args, **_kwargs: _accepted_evidence(role="internal-canary"),
    )
    (root / report.viewer_report.path).write_bytes(b"tampered\n")

    with pytest.raises(RealSceneAcceptanceError, match="SHA|length"):
        validate_real_scene_acceptance(path)


def test_aggregate_rejects_self_authored_decision_boolean(
    tmp_path,
    monkeypatch,
):
    _root, path, _report = _acceptance_report(
        tmp_path,
        role="internal-canary",
    )
    payload = json.loads(path.read_text(encoding="ascii"))
    payload["production_release_allowed"] = True
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="ascii",
    )
    monkeypatch.setattr(
        acceptance_module,
        "_validate_acceptance_evidence",
        lambda *_args, **_kwargs: _accepted_evidence(role="internal-canary"),
    )

    with pytest.raises(RealSceneAcceptanceError, match="authored"):
        validate_real_scene_acceptance(path)


def test_production_report_cannot_omit_rights_reference(tmp_path):
    _root, _path, report = _acceptance_report(
        tmp_path,
        role="production-acceptance",
    )
    with pytest.raises(ValueError, match="rights"):
        RealSceneAcceptance.model_validate(
            {
                **report.model_dump(by_alias=True),
                "rights_receipt": None,
            }
        )


def test_aggregate_paths_must_stay_below_report_root(tmp_path):
    with pytest.raises(ValueError, match="relative"):
        AcceptanceEvidenceReference(
            path="../outside.json",
            sha256="a" * 64,
            byte_length=1,
        )


def test_aggregate_rejects_policies_weaker_than_production_baseline():
    render_policy = RenderEvaluationPolicy(
        held_out_split_sha256="a" * 64,
        transforms_sha256="b" * 64,
        evaluator_container_digest=("nantai/nerfstudio@sha256:" + "c" * 64),
        protocol=RenderEvaluationProtocol(
            width=800,
            height=600,
            crop_mode="center-crop",
            colour_space="srgb",
            alpha_handling="reject",
            mask_handling="none",
            ssim_window_size=11,
            ssim_sigma=1.5,
            ssim_data_range=1.0,
            lpips_backbone="alex",
        ),
        minimum_mean_psnr=24.0,
        minimum_mean_ssim=0.80,
        maximum_mean_lpips=0.25,
        minimum_worst_psnr=18.0,
    )
    assert acceptance_module._render_policy_failures(render_policy) == ()
    assert acceptance_module._render_policy_failures(
        render_policy.model_copy(update={"minimum_mean_psnr": 23.99})
    )

    viewer_policy = ViewerPerformancePolicy(
        required_pose_ids=POSES,
        viewport_width=1280,
        viewport_height=720,
        warmup_frame_count=120,
        measured_frame_count=600,
        maximum_interactive_ms=10_000.0,
        maximum_p50_frame_ms=33.34,
        maximum_p95_frame_ms=50.0,
        maximum_worst_frame_ms=250.0,
    )
    assert acceptance_module._viewer_policy_failures(viewer_policy) == ()
    assert acceptance_module._viewer_policy_failures(
        viewer_policy.model_copy(update={"maximum_interactive_ms": 10_000.01})
    )


def test_packaged_render_evaluation_reopens_held_out_bundle_bytes(
    tmp_path,
    monkeypatch,
):
    source_bytes = b"held-out-source"
    identity = TrainingImageIdentity(
        logical_path="frame.jpg",
        sha256=hashlib.sha256(source_bytes).hexdigest(),
    )
    split = HeldOutSplit(
        ratio=0.5,
        total_count=2,
        held_out=(identity,),
        train=(
            TrainingImageIdentity(
                logical_path="train.jpg",
                sha256="d" * 64,
            ),
        ),
    )
    split_bytes = held_out_split_canonical_bytes(split)
    transforms = b'{"test_filenames":["frame.jpg"]}\n'
    config = b"method_name: splatfacto\n"
    protocol = RenderEvaluationProtocol(
        width=800,
        height=600,
        crop_mode="center-crop",
        colour_space="srgb",
        alpha_handling="reject",
        mask_handling="none",
        ssim_window_size=11,
        ssim_sigma=1.5,
        ssim_data_range=1.0,
        lpips_backbone="alex",
    )
    policy = RenderEvaluationPolicy(
        held_out_split_sha256=hashlib.sha256(split_bytes).hexdigest(),
        transforms_sha256=hashlib.sha256(transforms).hexdigest(),
        evaluator_container_digest=("nantai/nerfstudio@sha256:" + "e" * 64),
        protocol=protocol,
        minimum_mean_psnr=24.0,
        minimum_mean_ssim=0.80,
        maximum_mean_lpips=0.25,
        minimum_worst_psnr=18.0,
    )
    stem = render_artifact_stem(identity.logical_path)
    render_bytes = _png(800, 600)
    camera = RenderCameraRecord(
        frame_id=identity.logical_path,
        source_path="prepared/images/frame.jpg",
        source_sha256=identity.sha256,
        transforms_sha256=policy.transforms_sha256,
        camera_model="perspective",
        source_width=800,
        source_height=600,
        fx=400.0,
        fy=400.0,
        cx=400.0,
        cy=300.0,
        camera_to_world=(
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
        ),
    )
    camera_bytes = canonical_render_evaluation_bytes(camera)
    frame = RenderFrameMetric(
        frame_id=identity.logical_path,
        source_path="prepared/images/frame.jpg",
        source_byte_length=len(source_bytes),
        source_sha256=identity.sha256,
        render_path=f"result/render-evaluation/renders/{stem}.png",
        render_byte_length=len(render_bytes),
        render_sha256=hashlib.sha256(render_bytes).hexdigest(),
        camera_path=f"result/render-evaluation/cameras/{stem}.json",
        camera_byte_length=len(camera_bytes),
        camera_sha256=hashlib.sha256(camera_bytes).hexdigest(),
        psnr=24.0,
        ssim=0.80,
        lpips=0.25,
    )
    report = RenderEvaluationReport(
        evaluation_id="eval-aggregate",
        policy_sha256=render_evaluation_sha256(policy),
        held_out_split_sha256=policy.held_out_split_sha256,
        evaluator_container_digest=policy.evaluator_container_digest,
        protocol=protocol,
        frames=(frame,),
        trainer_config_sha256=hashlib.sha256(config).hexdigest(),
        mean_psnr=24.0,
        mean_ssim=0.80,
        mean_lpips=0.25,
        worst_psnr=24.0,
    )
    bundle = tmp_path / "training-job.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            "evaluation/payload/frame.jpg",
            source_bytes,
        )
    remote = tmp_path / "remote-result"
    files = {
        "render-evaluation/transforms.json": transforms,
        "render-evaluation/trainer-config.yml": config,
        f"render-evaluation/renders/{stem}.png": render_bytes,
        f"render-evaluation/cameras/{stem}.json": camera_bytes,
    }
    for relative, payload in files.items():
        path = remote / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    monkeypatch.setattr(
        acceptance_module,
        "verify_production_training_job_bundle",
        lambda _path: SimpleNamespace(path=bundle, split=split),
    )

    decision = acceptance_module._validate_packaged_render_evaluation(
        policy=policy,
        report=report,
        training_bundle_path=bundle,
        remote_result_root=remote,
    )

    assert decision.accepted is True
