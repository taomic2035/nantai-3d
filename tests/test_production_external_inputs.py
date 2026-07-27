"""Tests for the canonical blocked external inputs report (NOW-7)."""

from __future__ import annotations

import json

import pytest

from pipeline.production_external_inputs import (
    BlockedExternalInputsReport,
    ControlPointBlock,
    CudaImageBlock,
    DatasetBlock,
    NerfstudioRequirementBlock,
    ProductionExternalInputsError,
    SshEndpointBlock,
    ViewerAcceptanceBlock,
    blocked_report_full_canonical_bytes,
    blocked_report_signing_bytes,
    build_blocked_report,
    compute_blocked_report_sha256,
    load_blocked_report,
)

_CONTAINER = "registry.example/nantai@sha256:" + "c" * 64
_HOST_KEY = "SHA256:abc123def456="
_DATASET_SHA = "d" * 64


def _make_blocks(
    *,
    host: str = "gpu-host",
    port: int = 22,
    host_key: str = _HOST_KEY,
    cuda_identity: str = _CONTAINER,
    dataset_name: str = "nantai-village",
    dataset_sha: str = _DATASET_SHA,
    nerfstudio_version: str = "1.1.5",
    nerfstudio_method: str = "splatfacto",
    cp_count: int = 4,
    cp_non_coplanar: bool = True,
    ssh_reason: str = "no ssh credentials available",
    cuda_reason: str = "no gpu endpoint configured",
    dataset_reason: str = "dataset not yet uploaded",
    nerfstudio_reason: str = "nerfstudio not installed in container",
    cp_reason: str = "control points not yet measured",
    viewer_reason: str = "human acceptance not yet performed",
) -> dict:
    return dict(
        ssh_endpoint=SshEndpointBlock(
            state="blocked",
            host=host,
            port=port,
            pinned_host_key_fingerprint=host_key,
            blocked_reason=ssh_reason,
        ),
        cuda_image=CudaImageBlock(
            state="blocked",
            identity=cuda_identity,
            blocked_reason=cuda_reason,
        ),
        dataset=DatasetBlock(
            state="blocked",
            name=dataset_name,
            content_sha256=dataset_sha,
            rights_clearance="rights-cleared",
            blocked_reason=dataset_reason,
        ),
        nerfstudio=NerfstudioRequirementBlock(
            state="blocked",
            required_version=nerfstudio_version,
            required_method=nerfstudio_method,
            blocked_reason=nerfstudio_reason,
        ),
        control_points=ControlPointBlock(
            state="blocked",
            minimum_count=cp_count,
            non_coplanar=cp_non_coplanar,
            blocked_reason=cp_reason,
        ),
        viewer_acceptance=ViewerAcceptanceBlock(
            state="blocked",
            blocked_reason=viewer_reason,
        ),
    )


def _make_report(**overrides) -> BlockedExternalInputsReport:
    blocks = _make_blocks(**overrides)
    return build_blocked_report(**blocks)


# ---------------------------------------------------------------------------
# Build & round-trip
# ---------------------------------------------------------------------------


def test_build_blocked_report_and_round_trip():
    report = _make_report()
    raw = blocked_report_full_canonical_bytes(report)
    loaded = load_blocked_report(raw)
    assert loaded == report
    assert loaded.report_sha256 == report.report_sha256


def test_report_is_canonical_json():
    report = _make_report()
    raw = blocked_report_full_canonical_bytes(report)
    text = raw.decode("ascii")
    assert text.endswith("\n")
    parsed = json.loads(text)
    assert sorted(parsed.keys()) == list(parsed.keys())
    assert "schema" in parsed
    assert parsed["schema"] == "nantai.blocked-external-inputs.v1"


def test_report_sha_is_content_addressed():
    report_a = _make_report(ssh_reason="reason A")
    report_b = _make_report(ssh_reason="reason B")
    assert report_a.report_sha256 != report_b.report_sha256
    assert (
        compute_blocked_report_sha256(report_a) == report_a.report_sha256
    )


def test_signing_bytes_exclude_sha():
    report = _make_report()
    signing = blocked_report_signing_bytes(report)
    assert b"report_sha256" not in signing
    full = blocked_report_full_canonical_bytes(report)
    assert b"report_sha256" in full


# ---------------------------------------------------------------------------
# Duplicate keys rejected
# ---------------------------------------------------------------------------


def test_duplicate_keys_rejected():
    report = _make_report()
    raw = blocked_report_full_canonical_bytes(report)
    text = raw.decode("ascii")
    duplicated = text.replace(
        '"host"',
        '"host":"gpu-host","host"',
        1,
    )
    with pytest.raises(
        ProductionExternalInputsError, match="duplicate keys"
    ):
        load_blocked_report(duplicated.encode("ascii"))


# ---------------------------------------------------------------------------
# Non-canonical JSON rejected
# ---------------------------------------------------------------------------


def test_non_canonical_json_rejected():
    report = _make_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    non_canonical = json.dumps(payload, indent=2) + "\n"
    with pytest.raises(
        ProductionExternalInputsError, match="not canonical JSON"
    ):
        load_blocked_report(non_canonical.encode("ascii"))


def test_wrong_sha_rejected():
    report = _make_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["report_sha256"] = "e" * 64
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises(
        ProductionExternalInputsError, match="sha256 does not match"
    ):
        load_blocked_report(raw)


# ---------------------------------------------------------------------------
# Forbidden terms rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    ["ready", "verified-production", "metric-aligned", "release-allowed"],
)
def test_forbidden_terms_in_reason_rejected(forbidden: str):
    with pytest.raises(ValueError, match="must not declare"):
        _make_report(ssh_reason=f"system is {forbidden}")


def test_secret_like_content_rejected():
    with pytest.raises(ValueError, match="secret-like"):
        _make_report(cuda_reason="password=secret123")


def test_private_key_pattern_rejected():
    with pytest.raises(ValueError, match="secret-like"):
        _make_report(ssh_reason="private_key=abc")


def test_absolute_path_rejected():
    with pytest.raises(ValueError, match="absolute paths"):
        _make_report(dataset_reason="/home/user/secret/dataset")


# ---------------------------------------------------------------------------
# All states are Literal["blocked"]
# ---------------------------------------------------------------------------


def test_all_states_are_blocked():
    report = _make_report()
    assert report.ssh_endpoint.state == "blocked"
    assert report.cuda_image.state == "blocked"
    assert report.dataset.state == "blocked"
    assert report.nerfstudio.state == "blocked"
    assert report.control_points.state == "blocked"
    assert report.viewer_acceptance.state == "blocked"


def test_state_cannot_be_non_blocked():
    with pytest.raises(ValueError):
        SshEndpointBlock(
            state="ready",
            host="gpu-host",
            port=22,
            pinned_host_key_fingerprint=_HOST_KEY,
            blocked_reason="test",
        )


# ---------------------------------------------------------------------------
# Nerfstudio version and method are Literal-locked
# ---------------------------------------------------------------------------


def test_nerfstudio_version_is_literal():
    report = _make_report()
    assert report.nerfstudio.required_version == "1.1.5"
    assert report.nerfstudio.required_method == "splatfacto"


def test_nerfstudio_version_rejects_other():
    with pytest.raises(ValueError):
        NerfstudioRequirementBlock(
            state="blocked",
            required_version="2.0.0",
            required_method="splatfacto",
            blocked_reason="test",
        )


def test_nerfstudio_method_rejects_other():
    with pytest.raises(ValueError):
        NerfstudioRequirementBlock(
            state="blocked",
            required_version="1.1.5",
            required_method="nerfacto",
            blocked_reason="test",
        )


# ---------------------------------------------------------------------------
# Control points: at least 4, non-coplanar
# ---------------------------------------------------------------------------


def test_control_points_minimum_four():
    report = _make_report(cp_count=4)
    assert report.control_points.minimum_count == 4


def test_control_points_below_four_rejected():
    with pytest.raises(ValueError):
        ControlPointBlock(
            state="blocked",
            minimum_count=3,
            non_coplanar=True,
            blocked_reason="test",
        )


def test_control_points_non_coplanar_is_true():
    report = _make_report()
    assert report.control_points.non_coplanar is True


def test_control_points_coplanar_rejected():
    with pytest.raises(ValueError):
        ControlPointBlock(
            state="blocked",
            minimum_count=4,
            non_coplanar=False,
            blocked_reason="test",
        )


# ---------------------------------------------------------------------------
# SSH endpoint validation
# ---------------------------------------------------------------------------


def test_ssh_host_must_be_hostname():
    with pytest.raises(ValueError):
        SshEndpointBlock(
            state="blocked",
            host="host with spaces",
            port=22,
            pinned_host_key_fingerprint=_HOST_KEY,
            blocked_reason="test",
        )


def test_ssh_fingerprint_must_be_sha256():
    with pytest.raises(ValueError):
        SshEndpointBlock(
            state="blocked",
            host="gpu-host",
            port=22,
            pinned_host_key_fingerprint="md5:abc",
            blocked_reason="test",
        )


def test_ssh_port_range():
    with pytest.raises(ValueError):
        SshEndpointBlock(
            state="blocked",
            host="gpu-host",
            port=0,
            pinned_host_key_fingerprint=_HOST_KEY,
            blocked_reason="test",
        )
    with pytest.raises(ValueError):
        SshEndpointBlock(
            state="blocked",
            host="gpu-host",
            port=70000,
            pinned_host_key_fingerprint=_HOST_KEY,
            blocked_reason="test",
        )


# ---------------------------------------------------------------------------
# CUDA image must be immutable digest
# ---------------------------------------------------------------------------


def test_cuda_image_must_be_digest():
    with pytest.raises(ValueError):
        CudaImageBlock(
            state="blocked",
            identity="registry.example/nantai:latest",
            blocked_reason="test",
        )


def test_cuda_image_accepts_digest():
    report = _make_report(
        cuda_identity="registry.example/nantai@sha256:" + "f" * 64
    )
    assert report.cuda_image.identity.startswith(
        "registry.example/nantai@sha256:"
    )


# ---------------------------------------------------------------------------
# Dataset rights clearance is Literal
# ---------------------------------------------------------------------------


def test_dataset_rights_cleared_is_literal():
    report = _make_report()
    assert report.dataset.rights_clearance == "rights-cleared"


def test_dataset_rights_pending_rejected():
    with pytest.raises(ValueError):
        DatasetBlock(
            state="blocked",
            name="test",
            content_sha256="d" * 64,
            rights_clearance="pending",
            blocked_reason="test",
        )


def test_dataset_sha_must_be_hex():
    with pytest.raises(ValueError):
        DatasetBlock(
            state="blocked",
            name="test",
            content_sha256="not-hex",
            rights_clearance="rights-cleared",
            blocked_reason="test",
        )


# ---------------------------------------------------------------------------
# Extra fields rejected (frozen model)
# ---------------------------------------------------------------------------


def test_extra_fields_rejected():
    with pytest.raises(ValueError):
        SshEndpointBlock(
            state="blocked",
            host="gpu-host",
            port=22,
            pinned_host_key_fingerprint=_HOST_KEY,
            blocked_reason="test",
            extra_field="evil",
        )


def test_report_extra_fields_rejected():
    report = _make_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["extra"] = "evil"
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises(ProductionExternalInputsError):
        load_blocked_report(raw)


# ---------------------------------------------------------------------------
# Non-ASCII rejected
# ---------------------------------------------------------------------------


def test_non_ascii_rejected():
    report = _make_report()
    payload = json.loads(
        blocked_report_full_canonical_bytes(report).decode("ascii")
    )
    payload["ssh_endpoint"]["blocked_reason"] = "bloqué"
    raw = (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    with pytest.raises((ProductionExternalInputsError, ValueError)):
        load_blocked_report(raw)
