"""Pinned KTX 4.4.2 receipt, command, and binary-structure contracts."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest

from pipeline.synthetic_village.ktx2_toolchain import (
    KTX2_MAGIC,
    KTX_DARWIN_ARM64_ASSET,
    KTX_DARWIN_ARM64_SHA256,
    KTX_DARWIN_ARM64_URL,
    KTX_LEVEL_DIMENSIONS,
    KTX_TOOL_VERSION,
    KtxToolchainError,
    audit_ktx2_bytes,
    toktx_command,
)
from scripts import setup_synthetic_tools


def _fake_ktx2(
    *,
    width: int = 4096,
    height: int = 4096,
    level_count: int = 13,
    transfer: int = 2,
    colour_model: int = 166,
) -> bytes:
    level_index_offset = 80
    dfd_offset = level_index_offset + level_count * 24
    dfd_length = 28
    payload_offset = dfd_offset + dfd_length
    levels = []
    level_payloads = []
    cursor = payload_offset
    for level in range(level_count):
        payload = bytes([level % 251]) * 16
        levels.append(struct.pack("<QQQ", cursor, len(payload), len(payload)))
        level_payloads.append(payload)
        cursor += len(payload)
    dfd = bytearray(dfd_length)
    struct.pack_into("<I", dfd, 0, dfd_length)
    struct.pack_into("<HHHH", dfd, 4, 0, 0, 2, 24)
    dfd[12] = colour_model
    dfd[13] = 1
    dfd[14] = transfer
    dfd[15] = 0
    header = KTX2_MAGIC + struct.pack(
        "<13I2Q",
        0,
        1,
        width,
        height,
        0,
        0,
        1,
        level_count,
        2,
        dfd_offset,
        dfd_length,
        0,
        0,
        0,
        0,
    )
    return header + b"".join(levels) + bytes(dfd) + b"".join(level_payloads)


def test_exact_khronos_darwin_arm64_pin() -> None:
    assert KTX_TOOL_VERSION == "4.4.2"
    assert KTX_DARWIN_ARM64_ASSET == "KTX-Software-4.4.2-Darwin-arm64.pkg"
    assert KTX_DARWIN_ARM64_URL == (
        "https://github.com/KhronosGroup/KTX-Software/releases/download/"
        "v4.4.2/KTX-Software-4.4.2-Darwin-arm64.pkg"
    )
    assert KTX_DARWIN_ARM64_SHA256 == (
        "500bd8f9d63358c3f3a0d83b724c8574436a72c37dc0e4bad90ec1ca38032c3c"
    )


@pytest.mark.parametrize(
    ("role", "transfer"),
    [
        ("base_color", "srgb"),
        ("normal", "linear"),
    ],
)
def test_uastc_commands_are_role_and_colour_space_exact(
    role: str,
    transfer: str,
) -> None:
    assert toktx_command(
        Path("/opt/ktx/bin/toktx"),
        role=role,
        source=Path("source.png"),
        output=Path("output.ktx2"),
    ) == (
        "/opt/ktx/bin/toktx",
        "--t2",
        "--encode",
        "uastc",
        "--uastc_quality",
        "4",
        "--zcmp",
        "18",
        "--genmipmap",
        "--assign_oetf",
        transfer,
        "output.ktx2",
        "source.png",
    )


def test_orm_starts_etc1s_and_can_fall_back_to_uastc() -> None:
    assert toktx_command(
        Path("/opt/ktx/bin/toktx"),
        role="orm",
        source=Path("orm.png"),
        output=Path("orm.ktx2"),
    ) == (
        "/opt/ktx/bin/toktx",
        "--t2",
        "--encode",
        "etc1s",
        "--clevel",
        "5",
        "--qlevel",
        "255",
        "--genmipmap",
        "--assign_oetf",
        "linear",
        "orm.ktx2",
        "orm.png",
    )
    fallback = toktx_command(
        Path("/opt/ktx/bin/toktx"),
        role="orm",
        source=Path("orm.png"),
        output=Path("orm.ktx2"),
        force_uastc=True,
    )
    assert fallback[fallback.index("--encode") + 1] == "uastc"


def test_independent_ktx2_audit_reads_header_dfd_and_full_mip_chain() -> None:
    payload = _fake_ktx2()
    audit = audit_ktx2_bytes(
        payload,
        expected_transfer="srgb",
        expected_codec="uastc",
    )

    assert audit.sha256 == hashlib.sha256(payload).hexdigest()
    assert audit.width == audit.height == 4096
    assert audit.level_count == 13
    assert audit.level_dimensions == KTX_LEVEL_DIMENSIONS
    assert audit.transfer == "srgb"
    assert audit.codec == "uastc"
    assert audit.media_type == "image/ktx2"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: b"wrong magic!" + payload[12:], "identifier"),
        (lambda _payload: _fake_ktx2(width=2048), "4096"),
        (lambda _payload: _fake_ktx2(level_count=12), "13"),
        (lambda _payload: _fake_ktx2(transfer=1), "transfer"),
        (lambda _payload: _fake_ktx2(colour_model=163), "codec"),
    ],
)
def test_independent_ktx2_audit_fails_closed(mutation, message: str) -> None:
    with pytest.raises(KtxToolchainError, match=message):
        audit_ktx2_bytes(
            mutation(_fake_ktx2()),
            expected_transfer="srgb",
            expected_codec="uastc",
        )


def test_setup_parser_exposes_exact_private_ktx_install() -> None:
    args = setup_synthetic_tools._parser().parse_args(
        ["--install-ktx-4.4.2"],
    )
    assert args.install_ktx_4_4_2 is True

