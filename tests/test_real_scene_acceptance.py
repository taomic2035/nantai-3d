from __future__ import annotations

import struct
import zlib
from datetime import UTC, datetime

import pytest

from pipeline.real_scene_acceptance import (
    REQUIRED_VISUAL_CATEGORIES,
    HumanReviewPolicy,
    HumanVisualReview,
    RealSceneAcceptanceError,
    canonical_human_review_bytes,
    canonical_human_review_policy_bytes,
    record_human_visual_review,
    validate_human_visual_review,
)
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
        dispositions={
            category: "accepted"
            for category in REQUIRED_VISUAL_CATEGORIES
        },
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
    dispositions = {
        category: "accepted"
        for category in REQUIRED_VISUAL_CATEGORIES[:-1]
    }

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
    assert decision.unknown_categories == (
        REQUIRED_VISUAL_CATEGORIES[-1],
    )


def test_rejected_human_disposition_cannot_pass(tmp_path):
    root, review = _fixture(tmp_path)
    dispositions = {
        category: "accepted"
        for category in REQUIRED_VISUAL_CATEGORIES
    }
    dispositions[REQUIRED_VISUAL_CATEGORIES[0]] = "rejected"
    rejected = record_human_visual_review(
        policy=_policy(),
        root=root,
        reviewer=review.reviewer,
        dispositions=dispositions,
        screenshots={
            screenshot.pose_id: screenshot.path
            for screenshot in review.screenshots
        },
        reviewed_at=review.reviewed_at,
    )

    decision = validate_human_visual_review(
        _policy(),
        rejected,
        root,
    )

    assert decision.accepted is False
    assert decision.rejected_categories == (
        REQUIRED_VISUAL_CATEGORIES[0],
    )


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
    length = struct.unpack(">I", corrupt[idat - 4:idat])[0]
    payload_start = idat + 4
    corrupt[payload_start:payload_start + length] = b"x" * length
    crc = zlib.crc32(
        b"IDAT" + corrupt[payload_start:payload_start + length]
    ) & 0xFFFFFFFF
    corrupt[payload_start + length:payload_start + length + 4] = (
        struct.pack(">I", crc)
    )
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


def test_screenshot_symlink_and_escape_are_rejected(tmp_path):
    root, review = _fixture(tmp_path)
    original = root / review.screenshots[0].path
    target = root / "target.png"
    target.write_bytes(original.read_bytes())
    original.unlink()
    original.symlink_to(target)

    with pytest.raises(RealSceneAcceptanceError, match="symlink"):
        validate_human_visual_review(_policy(), review, root)

    escaped_binding = review.screenshots[0].model_copy(
        update={"path": "../escape.png"}
    )
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
    policy_path.write_bytes(
        canonical_human_review_policy_bytes(_policy())
    )
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
        args.extend(
            ["--disposition", f"{category}=accepted"]
        )
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
    assert (
        validate_human_visual_review(_policy(), review, root).accepted
        is True
    )
    assert "ACCEPTED" in capsys.readouterr().out


def test_human_review_cli_records_missing_category_as_unknown(
    tmp_path,
    capsys,
):
    root, args = _cli_fixture(tmp_path)
    omitted = f"{REQUIRED_VISUAL_CATEGORIES[-1]}=accepted"
    index = args.index(omitted)
    del args[index - 1:index + 1]

    exit_code = record_review_main(args)

    assert exit_code == 2
    output = root / "evidence/human-visual-review.json"
    review = HumanVisualReview.model_validate_json(
        output.read_bytes()
    )
    decision = validate_human_visual_review(_policy(), review, root)
    assert decision.unknown_categories == (
        REQUIRED_VISUAL_CATEGORIES[-1],
    )
    assert "PENDING" in capsys.readouterr().out
