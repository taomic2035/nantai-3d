"""Pinned KTX 4.4.2 receipt, command, and binary-structure contracts."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from pipeline.synthetic_village import ktx2_toolchain
from pipeline.synthetic_village.h3_material_sources import H3_HERO_SLOTS
from pipeline.synthetic_village.ktx2_toolchain import (
    H3_KTX2_PACK_SCHEMA,
    KTX2_MAGIC,
    KTX_DARWIN_ARM64_ASSET,
    KTX_DARWIN_ARM64_SHA256,
    KTX_DARWIN_ARM64_URL,
    KTX_LEVEL_DIMENSIONS,
    KTX_TOOL_VERSION,
    KtxDecodedQuality,
    KtxTextureDescriptor,
    KtxToolBinary,
    KtxToolchainError,
    KtxToolFile,
    KtxToolReceipt,
    audit_ktx2_bytes,
    compile_h3_ktx2_pack,
    compile_verified_ktx2_texture,
    extract_command,
    load_h3_ktx2_pack,
    measure_decoded_quality,
    toktx_command,
    validation_command,
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
        1 if colour_model == 163 else 2,
        dfd_offset,
        dfd_length,
        0,
        0,
        0,
        0,
    )
    return header + b"".join(levels) + bytes(dfd) + b"".join(level_payloads)


def _png_bytes(pixels: np.ndarray) -> bytes:
    output = io.BytesIO()
    Image.fromarray(pixels, mode="RGB").save(
        output,
        format="PNG",
        optimize=False,
        compress_level=1,
    )
    return output.getvalue()


def _fake_receipt() -> KtxToolReceipt:
    return KtxToolReceipt(
        package_file=KtxToolFile(
            relative_path=f"downloads/{KTX_DARWIN_ARM64_ASSET}",
            sha256=KTX_DARWIN_ARM64_SHA256,
            bytes=100,
        ),
        toktx=KtxToolBinary(
            relative_path="runtime/bin/toktx",
            sha256="1" * 64,
            bytes=100,
            version_output="toktx v4.4.2",
            codesign_valid=True,
        ),
        ktx=KtxToolBinary(
            relative_path="runtime/bin/ktx",
            sha256="2" * 64,
            bytes=100,
            version_output="ktx version: v4.4.2",
            codesign_valid=True,
        ),
        library=KtxToolFile(
            relative_path="runtime/lib/libktx.4.4.2.dylib",
            sha256="3" * 64,
            bytes=100,
        ),
        license=KtxToolFile(
            relative_path="runtime/licenses/License.rtf",
            sha256="4" * 64,
            bytes=100,
        ),
    )


class _FakeCompilerRunner:
    def __init__(
        self,
        reference: bytes,
        *,
        drift: bool = False,
        validator_messages: bool = False,
        fail_etc1s_quality: bool = False,
    ) -> None:
        self.reference = reference
        self.drift = drift
        self.validator_messages = validator_messages
        self.fail_etc1s_quality = fail_etc1s_quality
        self.codecs: dict[Path, str] = {}

    def __call__(
        self,
        command: tuple[str, ...],
        *,
        environment: dict[str, str],
        label: str,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        del environment, label
        assert timeout >= 120
        if "--t2" in command:
            assert timeout > 120
            codec = command[command.index("--encode") + 1]
            transfer = command[command.index("--assign_oetf") + 1]
            output = Path(command[-2])
            payload = _fake_ktx2(
                transfer=2 if transfer == "srgb" else 1,
                colour_model=166 if codec == "uastc" else 163,
            )
            if self.drift and output.parent.name == "repeat-2":
                payload += b"repeat drift"
            output.write_bytes(payload)
            self.codecs[output] = codec
            return subprocess.CompletedProcess(command, 0, "", "")
        if len(command) > 1 and command[1] == "validate":
            messages = [{"id": "warning"}] if self.validator_messages else []
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"valid": True, "messages": messages}),
                "",
            )
        if len(command) > 1 and command[1] == "extract":
            source = Path(command[-2])
            output = Path(command[-1])
            decoded = self.reference
            if self.fail_etc1s_quality and self.codecs[source] == "etc1s":
                with Image.open(io.BytesIO(self.reference)) as image:
                    pixels = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
                pixels[..., 1] = np.clip(
                    pixels[..., 1].astype(np.int16) + 13,
                    0,
                    255,
                ).astype(np.uint8)
                decoded = _png_bytes(pixels)
            output.write_bytes(decoded)
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(f"unexpected fake command: {command}")


@pytest.fixture(scope="module")
def compiler_reference_png() -> bytes:
    pixels = np.empty((4096, 4096, 3), dtype=np.uint8)
    pixels[..., 0] = 255
    pixels[..., 1] = 190
    pixels[..., 2] = 0
    return _png_bytes(pixels)


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
        str(Path("/opt/ktx/bin/toktx")),
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
        str(Path("/opt/ktx/bin/toktx")),
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


def test_official_validation_and_decode_commands_are_exact() -> None:
    assert validation_command(
        Path("/opt/ktx/bin/ktx"),
        Path("texture.ktx2"),
    ) == (
        str(Path("/opt/ktx/bin/ktx")),
        "validate",
        "--format",
        "mini-json",
        "--warnings-as-errors",
        "--gltf-basisu",
        "texture.ktx2",
    )
    assert extract_command(
        Path("/opt/ktx/bin/ktx"),
        source=Path("texture.ktx2"),
        output=Path("decoded.png"),
    ) == (
        str(Path("/opt/ktx/bin/ktx")),
        "extract",
        "--transcode",
        "rgba8",
        "--level",
        "0",
        "texture.ktx2",
        "decoded.png",
    )


def test_verified_texture_compiles_twice_then_publishes_content_addressed(
    tmp_path: Path,
    compiler_reference_png: bytes,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(compiler_reference_png)
    output_root = tmp_path / "published"
    descriptor = compile_verified_ktx2_texture(
        source,
        role="base_color",
        tool_root=tmp_path / "tools",
        receipt=_fake_receipt(),
        output_root=output_root,
        runner=_FakeCompilerRunner(compiler_reference_png),
    )

    assert descriptor.role == "base_color"
    assert descriptor.codec == "uastc"
    assert descriptor.transfer == "srgb"
    assert descriptor.repeat_build_byte_equal is True
    assert descriptor.official_validation is True
    assert descriptor.quality.base_colour_ssim == pytest.approx(1.0)
    assert descriptor.object_path == f"objects/{descriptor.sha256}.ktx2"
    assert (output_root / descriptor.object_path).read_bytes() == (
        _fake_ktx2()
    )


def test_verified_texture_rejects_repeat_drift_and_validator_messages(
    tmp_path: Path,
    compiler_reference_png: bytes,
) -> None:
    source = tmp_path / "source.png"
    source.write_bytes(compiler_reference_png)
    for runner, message in (
        (_FakeCompilerRunner(compiler_reference_png, drift=True), "repeat"),
        (
            _FakeCompilerRunner(
                compiler_reference_png,
                validator_messages=True,
            ),
            "validator",
        ),
    ):
        with pytest.raises(KtxToolchainError, match=message):
            compile_verified_ktx2_texture(
                source,
                role="base_color",
                tool_root=tmp_path / "tools",
                receipt=_fake_receipt(),
                output_root=tmp_path / f"published-{message}",
                runner=runner,
            )


def test_orm_quality_failure_rebuilds_both_repeats_as_uastc(
    tmp_path: Path,
    compiler_reference_png: bytes,
) -> None:
    source = tmp_path / "orm.png"
    source.write_bytes(compiler_reference_png)
    descriptor = compile_verified_ktx2_texture(
        source,
        role="orm",
        tool_root=tmp_path / "tools",
        receipt=_fake_receipt(),
        output_root=tmp_path / "published",
        runner=_FakeCompilerRunner(
            compiler_reference_png,
            fail_etc1s_quality=True,
        ),
    )

    assert descriptor.codec == "uastc"
    assert descriptor.orm_etc1s_fallback is True
    assert descriptor.quality.orm_max_channel_error == 0.0


def test_h3_ktx2_pack_is_complete_content_addressed_and_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    authored_pack_id = "a" * 64
    source_pack_id = "b" * 64
    authored = SimpleNamespace(
        pack_id=authored_pack_id,
        source_pack_id=source_pack_id,
        synthetic=True,
        ai_generated=True,
        real_photo_textures=False,
        geometry_usability="preview-only",
        metric_alignment=False,
        verification_level="L0",
        records=tuple(
            SimpleNamespace(slot_id=slot_id)
            for slot_id in H3_HERO_SLOTS
        ),
    )
    monkeypatch.setattr(
        ktx2_toolchain,
        "load_h3_authored_material_pack",
        lambda _root: authored,
    )
    monkeypatch.setattr(
        ktx2_toolchain,
        "read_verified_h3_authored_map",
        lambda _root, *, pack, slot_id, role: (
            f"{pack.pack_id}:{slot_id}:{role}".encode()
        ),
    )
    monkeypatch.setattr(
        ktx2_toolchain,
        "load_ktx_tool_receipt",
        lambda _path: _fake_receipt(),
    )

    def fake_compile(
        source,
        *,
        role,
        tool_root,
        receipt,
        output_root,
        runner,
    ):
        del tool_root, runner
        source_payload = Path(source).read_bytes()
        transfer = "srgb" if role == "base_color" else "linear"
        payload = _fake_ktx2(transfer=2 if transfer == "srgb" else 1)
        digest = hashlib.sha256(payload).hexdigest()
        object_path = f"objects/{digest}.ktx2"
        destination = Path(output_root) / object_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(payload)
        quality = {
            "base_color": KtxDecodedQuality(
                role="base_color",
                base_colour_ssim=1.0,
            ),
            "normal": KtxDecodedQuality(
                role="normal",
                normal_mean_cosine=1.0,
                normal_p01_cosine=1.0,
            ),
            "orm": KtxDecodedQuality(
                role="orm",
                orm_max_channel_error=0.0,
            ),
        }[role]
        return KtxTextureDescriptor(
            role=role,
            source_sha256=hashlib.sha256(source_payload).hexdigest(),
            object_path=object_path,
            sha256=digest,
            bytes=len(payload),
            transfer=transfer,
            codec="uastc",
            toktx_sha256=receipt.toktx.sha256,
            command_options=("--encode", "uastc"),
            official_validation=True,
            repeat_build_byte_equal=True,
            orm_etc1s_fallback=role == "orm",
            quality=quality,
        )

    first = compile_h3_ktx2_pack(
        tmp_path / "authored",
        tmp_path / "published",
        receipt_path=tmp_path / "receipt.json",
        texture_compiler=fake_compile,
    )
    pack = first.manifest

    assert pack.schema_version == H3_KTX2_PACK_SCHEMA
    assert pack.authored_pack_id == authored_pack_id
    assert pack.source_pack_id == source_pack_id
    assert pack.synthetic is True
    assert pack.real_photo_textures is False
    assert tuple(record.slot_id for record in pack.records) == H3_HERO_SLOTS
    assert all(
        (record.base_color.role, record.normal.role, record.orm.role)
        == ("base_color", "normal", "orm")
        for record in pack.records
    )
    assert load_h3_ktx2_pack(first.root) == pack
    second = compile_h3_ktx2_pack(
        tmp_path / "authored",
        tmp_path / "published",
        receipt_path=tmp_path / "receipt.json",
        texture_compiler=fake_compile,
    )
    assert second == first


def test_h3_ktx2_pack_rejects_unmanifested_bytes(tmp_path: Path) -> None:
    root = tmp_path / "invalid-pack"
    root.mkdir()
    (root / "unexpected").write_bytes(b"not in manifest")
    with pytest.raises(KtxToolchainError):
        load_h3_ktx2_pack(root)


def test_decoded_quality_gates_are_role_exact() -> None:
    size = 4096
    horizontal = np.linspace(32, 224, size, dtype=np.uint8)
    base = np.empty((size, size, 3), dtype=np.uint8)
    base[..., 0] = horizontal
    base[..., 1] = horizontal[:, None]
    base[..., 2] = 127
    base_quality = measure_decoded_quality(
        _png_bytes(base),
        _png_bytes(base.copy()),
        role="base_color",
    )
    assert base_quality.base_colour_ssim == pytest.approx(1.0)
    assert base_quality.passed is True

    normal = np.empty_like(base)
    normal[..., 0] = 128
    normal[..., 1] = 128
    normal[..., 2] = 255
    normal_quality = measure_decoded_quality(
        _png_bytes(normal),
        _png_bytes(normal.copy()),
        role="normal",
    )
    assert normal_quality.normal_mean_cosine == pytest.approx(1.0)
    assert normal_quality.normal_p01_cosine == pytest.approx(1.0)
    assert normal_quality.passed is True

    orm = np.empty_like(base)
    orm[..., 0] = 255
    orm[..., 1] = 190
    orm[..., 2] = 0
    decoded_orm = np.clip(orm.astype(np.int16) + 12, 0, 255).astype(np.uint8)
    orm_quality = measure_decoded_quality(
        _png_bytes(orm),
        _png_bytes(decoded_orm),
        role="orm",
    )
    assert orm_quality.orm_max_channel_error == pytest.approx(12 / 255)
    assert orm_quality.passed is True


@pytest.mark.parametrize(
    "role",
    ["base_color", "normal", "orm"],
)
def test_decoded_quality_rejects_role_specific_failure(role: str) -> None:
    reference = np.zeros((4096, 4096, 3), dtype=np.uint8)
    decoded = reference.copy()
    if role == "base_color":
        reference[..., 0] = 255
    elif role == "normal":
        reference[..., 2] = 255
        decoded[..., 2] = 0
    else:
        decoded[..., 1] = 13

    with pytest.raises(KtxToolchainError, match="decoded quality"):
        measure_decoded_quality(
            _png_bytes(reference),
            _png_bytes(decoded),
            role=role,
        )


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
