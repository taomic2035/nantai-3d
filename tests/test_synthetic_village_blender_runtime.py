from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import uuid
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BLENDER = ROOT / "third" / "blender" / "blender.exe"
BUILDER = ROOT / "scripts" / "blender" / "build_synthetic_village.py"
RENDERER = ROOT / "scripts" / "blender" / "render_synthetic_village.py"
PREFLIGHT = ROOT / "scripts" / "blender" / "preflight_production_cameras.py"
TEXTURED_RUNTIME_BLEND = (
    ROOT
    / ".nantai-studio/synthetic-village/hybrid-v3/work/canary"
    / "4f38ecf49ff8182e02c426df314dab90b91502673164330d3b704f234d02f1dc"
    / "village-canary.blend"
)
MATERIAL_BUNDLE_ROOT = (
    ROOT
    / ".nantai-studio/synthetic-village/hybrid-v3/material-bundles"
    / "88e35afe5ed57b7d0187956d601b1470662aaf964f593a2fc08c543c7da2e2a3"
)


pytestmark = pytest.mark.skipif(
    not BLENDER.is_file(),
    reason="locked private Blender runtime is not installed",
)

RUN_END_TO_END = os.environ.get("NANTAI_RUN_BLENDER_RUNTIME_TESTS") == "1"


def _run_builder(
    *runtime_args: str,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(BUILDER),
            "--",
            *runtime_args,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _run_renderer(
    blend_path: Path,
    *runtime_args: str,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            str(blend_path),
            "--python",
            str(RENDERER),
            "--",
            *runtime_args,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _run_preflight(
    blend_path: Path,
    *runtime_args: str,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            str(blend_path),
            "--python",
            str(PREFLIGHT),
            "--",
            *runtime_args,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _run_renderer_probe(
    tmp_path: Path,
    source: str,
    *,
    blend_path: Path | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    probe_path = tmp_path / "renderer-probe.py"
    probe_path.write_text(source, encoding="utf-8")
    command = [
        str(BLENDER),
        "--background",
        "--factory-startup",
        "--disable-autoexec",
        "--python-exit-code",
        "17",
    ]
    if blend_path is not None:
        command.append(str(blend_path))
    command.extend(("--python", str(probe_path)))
    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _probe_prelude() -> str:
    return (
        f"import runpy\nns = runpy.run_path({str(RENDERER)!r}, run_name='nantai_renderer_probe')\n"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decode_canonical_png(path: Path) -> tuple[int, int, int, int, bytes]:
    raw = path.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    offset = 8
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(raw):
        length = struct.unpack_from(">I", raw, offset)[0]
        kind = raw[offset + 4 : offset + 8]
        payload = raw[offset + 8 : offset + 8 + length]
        stored_crc = struct.unpack_from(">I", raw, offset + 8 + length)[0]
        assert stored_crc == zlib.crc32(kind + payload) & 0xFFFFFFFF
        chunks.append((kind, payload))
        offset += 12 + length
    assert offset == len(raw)
    assert [kind for kind, _payload in chunks] == [b"IHDR", b"IDAT", b"IEND"]
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB",
        chunks[0][1],
    )
    assert (compression, filtering, interlace) == (0, 0, 0)
    channels = {0: 1, 2: 3}[color_type]
    row_bytes = width * channels * (bit_depth // 8)
    rows = zlib.decompress(chunks[1][1])
    assert len(rows) == height * (row_bytes + 1)
    pixels = bytearray()
    for row in range(height):
        start = row * (row_bytes + 1)
        assert rows[start] == 0
        pixels.extend(rows[start + 1 : start + 1 + row_bytes])
    return width, height, bit_depth, color_type, bytes(pixels)


def test_production_layer_counts_are_raw_region_aware_and_deterministic(
    tmp_path: Path,
) -> None:
    source = (
        _probe_prelude()
        + "import json\n"
        + "fn = ns['_production_layer_counts']\n"
        + "fn.__globals__.update(WIDTH=4, HEIGHT=4, PIXELS=16)\n"
        + "depth = [0,1,1,3, 1.5,.5,2,4, 5,5,5,5, 5,5,5,5]\n"
        + "normals = [0,0,0] + [0,0,1] * 15\n"
        + "instances = [0,0,1,1, 0,1,1,2, 1,1,2,2, 1,2,2,2]\n"
        + "semantics = [0,1,3,3, 1,3,3,4, 3,3,4,4, 3,4,4,4]\n"
        + "policy = {'near_depth_m': 2.0, "
        + "'upper_region_end_row_exclusive': 2, "
        + "'ground_semantic_ids': [1], 'sky_semantic_id': 0}\n"
        + "objects = [{'instance_id': 1, 'semantic_id': 3}, "
        + "{'instance_id': 2, 'semantic_id': 4}]\n"
        + "semantics_registry = [{'semantic_id': i} for i in [0,1,2,3,4]]\n"
        + "result = ns['_production_layer_counts']("
        + "depth, normals, instances, semantics, policy=policy, "
        + "object_registry=objects, semantic_registry=semantics_registry)\n"
        + "print('NANTAI_COUNTS ' + json.dumps(result, sort_keys=True))\n"
    )

    completed = _run_renderer_probe(tmp_path, source)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    line = next(
        row
        for row in completed.stdout.splitlines()
        if row.startswith("NANTAI_COUNTS ")
    )
    counts = json.loads(line.removeprefix("NANTAI_COUNTS "))
    assert counts == {
        "dominant_near_instance_id": 1,
        "dominant_near_instance_pixel_count": 2,
        "dominant_upper_instance_id": 1,
        "dominant_upper_instance_pixel_count": 4,
        "near_depth_pixel_count": 4,
        "registered_instance_pixel_count": 13,
        "sky_pixel_count": 1,
        "total_pixel_count": 16,
        "upper_ground_pixel_count": 2,
        "upper_pixel_count": 8,
        "valid_depth_pixel_count": 15,
        "valid_normal_pixel_count": 15,
        "valid_semantic_pixel_count": 15,
    }


def test_production_layer_counts_reject_unknown_mask_ids(
    tmp_path: Path,
) -> None:
    source = (
        _probe_prelude()
        + "fn = ns['_production_layer_counts']\n"
        + "fn.__globals__.update(WIDTH=1, HEIGHT=1, PIXELS=1)\n"
        + "fn("
        + "[1.0], [0.0,0.0,1.0], [99], [3], "
        + "policy={'near_depth_m': 2.0, "
        + "'upper_region_end_row_exclusive': 1, "
        + "'ground_semantic_ids': [1], 'sky_semantic_id': 0}, "
        + "object_registry=[{'instance_id': 1, 'semantic_id': 3}], "
        + "semantic_registry=[{'semantic_id': i} for i in [0,1,2,3]])\n"
    )

    completed = _run_renderer_probe(tmp_path, source)

    assert completed.returncode == 17
    assert "unregistered instance ID" in (completed.stdout + completed.stderr)


def _textured_render_request():
    import pipeline.synthetic_village.canary as canary
    from pipeline.synthetic_village.windows_production_build import (
        verify_windows_production_build,
    )

    build_directory = TEXTURED_RUNTIME_BLEND.parent
    verified = verify_windows_production_build(
        directory=build_directory,
        material_bundle_root=MATERIAL_BUNDLE_ROOT,
        repo_root=ROOT,
        surface_realism_profile_id="source-consistent-multiscale-surface-v1",
    )
    report = verified.report
    build_request = verified.request
    camera = build_request.camera_plan.cameras[0]
    measured = next(
        row.measured_c2w_blender
        for row in report.camera_registry
        if row.camera_id == camera.camera_id
    )
    executable_sha256 = verified.blender_executable_sha256
    renderer_sha256 = _sha256(RENDERER)
    blend_sha256 = verified.blend_sha256
    report_sha256 = verified.build_report_sha256
    registry_sha256 = hashlib.sha256(
        canary._canonical_json_bytes(  # noqa: SLF001 - contract-level integration test
            [row.model_dump(mode="json") for row in report.object_registry],
        ),
    ).hexdigest()
    settings = canary.RenderSettings()
    render_id = hashlib.sha256(
        canary._canonical_json_bytes(  # noqa: SLF001 - mirrors the production host
            canary._render_id_payload(  # noqa: SLF001
                report=report,
                blender_executable_sha256=executable_sha256,
                renderer_script_sha256=renderer_sha256,
                blend_sha256=blend_sha256,
                build_report_sha256=report_sha256,
                object_registry_sha256=registry_sha256,
                settings=settings,
            ),
        ),
    ).hexdigest()
    return canary.RenderFrameRequest(
        render_id=render_id,
        build_id=report.build_id,
        blender_executable_sha256=executable_sha256,
        renderer_script_sha256=renderer_sha256,
        blend_sha256=blend_sha256,
        build_report_sha256=report_sha256,
        object_registry_sha256=registry_sha256,
        settings=settings,
        camera=camera,
        measured_c2w_blender=measured,
        object_registry=report.object_registry,
        auxiliary_registry=report.auxiliary_registry,
        semantic_registry=report.semantic_registry,
    )


def _production_clearance_request():
    from pipeline.synthetic_village import canary
    from pipeline.synthetic_village.elevated_topology import (
        build_elevated_topology_plan,
    )
    from pipeline.synthetic_village.production_preflight import (
        ProductionClearancePolicy,
        build_production_clearance_request,
    )
    from pipeline.synthetic_village.production_profile import (
        build_production_camera_plan,
    )
    from pipeline.synthetic_village.scene_plan import build_scene_plan

    report_path = TEXTURED_RUNTIME_BLEND.parent / "build-report.json"
    report = canary.load_textured_build_report(report_path)
    scene = build_scene_plan()
    plan = build_production_camera_plan(
        scene,
        build_elevated_topology_plan(scene),
    )
    return build_production_clearance_request(
        plan=plan,
        selected_camera_ids=(
            "camera-ground-route-010",
            "camera-ground-route-034",
            "camera-ground-route-039",
        ),
        build_id=report.build_id,
        blender_executable_sha256=_sha256(BLENDER),
        preflight_script_sha256=_sha256(PREFLIGHT),
        blend_sha256=_sha256(TEXTURED_RUNTIME_BLEND),
        build_report_sha256=_sha256(report_path),
        object_registry=report.object_registry,
        auxiliary_registry=report.auxiliary_registry,
        semantic_registry=report.semantic_registry,
        policy=ProductionClearancePolicy(
            near_distance_m=2.0,
            minimum_upper_middle_near_hit_count=5,
        ),
    )


def _windows_production_frame_request():
    from pipeline.synthetic_village.elevated_topology import (
        build_elevated_topology_plan,
    )
    from pipeline.synthetic_village.production_profile import (
        build_production_camera_plan,
    )
    from pipeline.synthetic_village.production_render import (
        build_local_production_frame_request,
    )
    from pipeline.synthetic_village.scene_plan import build_scene_plan
    from pipeline.synthetic_village.windows_production_build import (
        verify_windows_production_build,
    )
    from tests.test_synthetic_village_production_render import (
        _post_render_policy,
    )

    verified = verify_windows_production_build(
        directory=TEXTURED_RUNTIME_BLEND.parent,
        material_bundle_root=MATERIAL_BUNDLE_ROOT,
        repo_root=ROOT,
        surface_realism_profile_id="source-consistent-multiscale-surface-v1",
    )
    scene = build_scene_plan()
    plan = build_production_camera_plan(
        scene,
        build_elevated_topology_plan(scene),
    )
    return build_local_production_frame_request(
        plan=plan,
        camera_id="camera-ground-route-034",
        build_adapter=verified.adapter,
        build_id=verified.build_id,
        blender_executable_sha256=verified.blender_executable_sha256,
        renderer_script_sha256=_sha256(RENDERER),
        blend_sha256=verified.blend_sha256,
        build_report_sha256=verified.build_report_sha256,
        object_registry=verified.object_registry,
        auxiliary_registry=verified.auxiliary_registry,
        semantic_registry=verified.semantic_registry,
        preflight_id="6" * 64,
        quality_policy_sha256="7" * 64,
        post_render_policy=_post_render_policy(),
    )


@pytest.mark.skipif(
    not TEXTURED_RUNTIME_BLEND.is_file(),
    reason="verified private production Blender scene is unavailable",
)
def test_renderer_accepts_explicit_windows_production_scene_provenance(
    tmp_path: Path,
) -> None:
    from pipeline.synthetic_village.production_render import (
        canonical_local_production_render_request_bytes,
    )

    request = _windows_production_frame_request()
    request_path = tmp_path / "windows-production-request.json"
    request_path.write_bytes(
        canonical_local_production_render_request_bytes(request),
    )
    source = (
        _probe_prelude()
        + "import json\n"
        + f"request = json.loads(open({str(request_path)!r}, encoding='utf-8').read())\n"
        + "ns['_validate_request'](request)\n"
        + "print('NANTAI_WINDOWS_PROVENANCE_OK', flush=True)\n"
    )

    completed = _run_renderer_probe(
        tmp_path,
        source,
        blend_path=TEXTURED_RUNTIME_BLEND,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "NANTAI_WINDOWS_PROVENANCE_OK" in completed.stdout


@pytest.mark.skipif(
    not TEXTURED_RUNTIME_BLEND.is_file(),
    reason="verified private production Blender scene is unavailable",
)
def test_renderer_rejects_rewritten_post_render_rules(tmp_path: Path) -> None:
    from pipeline.synthetic_village.production_render import (
        canonical_local_production_render_request_bytes,
    )

    request_path = tmp_path / "windows-production-request.json"
    request_path.write_bytes(
        canonical_local_production_render_request_bytes(
            _windows_production_frame_request(),
        ),
    )
    source = (
        _probe_prelude()
        + "import hashlib, json\n"
        + f"request = json.loads(open({str(request_path)!r}, encoding='utf-8').read())\n"
        + "request['post_render_policy']['rules'][0]['rule_id'] = "
        + "request['post_render_policy']['rules'][1]['rule_id']\n"
        + "request['post_render_policy_sha256'] = hashlib.sha256("
        + "ns['_canonical_bytes'](request['post_render_policy'])).hexdigest()\n"
        + "try:\n"
        + "    ns['_validate_request'](request)\n"
        + "except ns['RuntimeRenderError']:\n"
        + "    print('NANTAI_POST_POLICY_REJECTED', flush=True)\n"
        + "else:\n"
        + "    raise AssertionError('rewritten post-render rules were accepted')\n"
    )

    completed = _run_renderer_probe(
        tmp_path,
        source,
        blend_path=TEXTURED_RUNTIME_BLEND,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "NANTAI_POST_POLICY_REJECTED" in completed.stdout


@pytest.mark.skipif(
    not RUN_END_TO_END,
    reason="set NANTAI_RUN_BLENDER_RUNTIME_TESTS=1 for the real Blender render",
)
def test_runtime_embeds_measured_production_layer_statistics() -> None:
    from pipeline.synthetic_village.production_render import (
        LocalProductionRenderFrameReport,
        canonical_local_production_render_report_bytes,
        canonical_local_production_render_request_bytes,
    )

    request = _windows_production_frame_request()
    private_root = ROOT / ".nantai-studio/synthetic-village/hybrid-v3/runtime-tests"
    private_root.mkdir(parents=True, exist_ok=True)
    container = private_root / uuid.uuid4().hex
    container.mkdir()
    try:
        blend_path = container / "village-canary.blend"
        shutil.copy2(TEXTURED_RUNTIME_BLEND, blend_path)
        request_path = container / "render-request.json"
        request_path.write_bytes(
            canonical_local_production_render_request_bytes(request),
        )
        staging = container / "frame"
        completed = _run_renderer(
            blend_path,
            "--request",
            str(request_path),
            "--staging",
            str(staging),
            timeout=600,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        report_path = staging / "frame-report.json"
        report = LocalProductionRenderFrameReport.model_validate_json(
            report_path.read_bytes(),
        )
        assert report_path.read_bytes() == (
            canonical_local_production_render_report_bytes(report)
        )
        assert report.post_render_policy_sha256 == (
            request.post_render_policy_sha256
        )
        assert report.layer_statistics.model_dump(mode="json") == {
            "schema_version": (
                "nantai.synthetic-village.production-frame-layer-statistics.v2"
            ),
            "camera_id": "camera-ground-route-034",
            "total_pixel_count": 589824,
            "upper_pixel_count": 294912,
            "valid_depth_pixel_count": 406487,
            "valid_normal_pixel_count": 406487,
            "registered_instance_pixel_count": 319103,
            "valid_semantic_pixel_count": 406487,
            "sky_pixel_count": 183337,
            "upper_ground_pixel_count": 7833,
            "near_depth_pixel_count": 43493,
            "dominant_near_instance_id": 130,
            "dominant_near_instance_pixel_count": 43493,
            "dominant_upper_instance_id": 130,
            "dominant_upper_instance_pixel_count": 87345,
        }
    finally:
        shutil.rmtree(container, ignore_errors=True)


def _read_exr_attributes(path: Path) -> dict[str, tuple[str, bytes]]:
    raw = path.read_bytes()
    assert raw[:4] == b"\x76\x2f\x31\x01"
    offset = 8
    attributes: dict[str, tuple[str, bytes]] = {}
    while True:
        name_end = raw.index(b"\0", offset)
        if name_end == offset:
            break
        name = raw[offset:name_end].decode("ascii")
        offset = name_end + 1
        type_end = raw.index(b"\0", offset)
        attribute_type = raw[offset:type_end].decode("ascii")
        offset = type_end + 1
        size = struct.unpack_from("<I", raw, offset)[0]
        offset += 4
        value = raw[offset : offset + size]
        assert len(value) == size
        offset += size
        attributes[name] = (attribute_type, value)
    return attributes


@pytest.mark.skipif(
    not TEXTURED_RUNTIME_BLEND.is_file(),
    reason="verified private production Blender scene is unavailable",
)
def test_preflight_runtime_measures_bound_production_camera_clearance(
    tmp_path: Path,
) -> None:
    from pipeline.synthetic_village.production_preflight import (
        ProductionClearanceReport,
        canonical_production_clearance_report_bytes,
        canonical_production_clearance_request_bytes,
        verify_production_clearance_report,
    )

    request = _production_clearance_request()
    request_path = tmp_path / "preflight-request.json"
    report_path = tmp_path / "preflight-report.json"
    request_path.write_bytes(
        canonical_production_clearance_request_bytes(request),
    )

    completed = _run_preflight(
        TEXTURED_RUNTIME_BLEND,
        "--request",
        str(request_path),
        "--report",
        str(report_path),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = ProductionClearanceReport.model_validate_json(
        report_path.read_bytes(),
    )
    assert report_path.read_bytes() == (
        canonical_production_clearance_report_bytes(report)
    )
    verify_production_clearance_report(report, request=request)
    decisions = {row.camera_id: row for row in report.decisions}
    assert decisions["camera-ground-route-010"].passes is False
    assert (
        decisions[
            "camera-ground-route-010"
        ].measured_upper_middle_near_hit_count
        == 15
    )
    assert decisions["camera-ground-route-034"].passes is True
    assert (
        decisions[
            "camera-ground-route-034"
        ].measured_upper_middle_near_hit_count
        == 0
    )
    assert decisions["camera-ground-route-039"].passes is False
    assert (
        decisions[
            "camera-ground-route-039"
        ].measured_upper_middle_near_hit_count
        == 5
    )
    assert report.synthetic is True
    assert report.geometry_trust == "simplified-pbr-not-render-parity"
    assert report.trust_effect == "none-quality-filter-only"


@pytest.mark.skipif(
    not TEXTURED_RUNTIME_BLEND.is_file(),
    reason="verified private production Blender scene is unavailable",
)
def test_preflight_runtime_rejects_duplicate_request_keys_without_report(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "duplicate-request.json"
    report_path = tmp_path / "must-not-exist.json"
    request_path.write_bytes(
        b'{"schema_version":"first","schema_version":"second"}\n',
    )

    completed = _run_preflight(
        TEXTURED_RUNTIME_BLEND,
        "--request",
        str(request_path),
        "--report",
        str(report_path),
    )

    assert completed.returncode == 17
    assert "duplicate JSON key: schema_version" in (
        completed.stdout + completed.stderr
    )
    assert not report_path.exists()


def test_runtime_redecodes_written_masks_and_rejects_bad_crc(tmp_path: Path) -> None:
    output = tmp_path / "mask.png"
    result = _run_renderer_probe(
        tmp_path,
        _probe_prelude()
        + "g = ns['_write_grayscale_png'].__globals__\n"
        + "g['WIDTH'] = 2\n"
        + "g['HEIGHT'] = 2\n"
        + "g['PIXELS'] = 4\n"
        + f"path = __import__('pathlib').Path({str(output)!r})\n"
        + "values = [0, 1, 255, 65535]\n"
        + "decoded = ns['_write_grayscale_png'](path, values, 16)\n"
        + "assert decoded == values\n"
        + "raw = bytearray(path.read_bytes())\n"
        + "idat = raw.index(b'IDAT')\n"
        + "length = int.from_bytes(raw[idat - 4:idat], 'big')\n"
        + "raw[idat + 4 + length] ^= 1\n"
        + "path.write_bytes(raw)\n"
        + "try:\n"
        + "    ns['_decode_canonical_png'](path, 16, 0, 1, 'instance mask')\n"
        + "except ns['RuntimeRenderError'] as exc:\n"
        + "    assert 'CRC' in str(exc)\n"
        + "else:\n"
        + "    raise AssertionError('corrupt PNG CRC was accepted')\n"
        + "print('NANTAI_MASK_REDECODE_OK', flush=True)\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NANTAI_MASK_REDECODE_OK" in result.stdout


def test_runtime_writes_canonical_rgb_and_exr_metadata(tmp_path: Path) -> None:
    rgb = tmp_path / "rgb.png"
    exr = tmp_path / "depth.exr"
    result = _run_renderer_probe(
        tmp_path,
        _probe_prelude()
        + "g = ns['_write_float_exr'].__globals__\n"
        + "g['WIDTH'] = 2\n"
        + "g['HEIGHT'] = 2\n"
        + "g['PIXELS'] = 4\n"
        + f"rgb = __import__('pathlib').Path({str(rgb)!r})\n"
        + f"exr = __import__('pathlib').Path({str(exr)!r})\n"
        + "pixels = bytes(range(12))\n"
        + "decoded = ns['_write_rgb_png'](rgb, pixels)\n"
        + "assert decoded == pixels\n"
        + "raw = rgb.read_bytes()\n"
        + "assert b'Date' not in raw and b'C:\\\\Users\\\\' not in raw\n"
        + "values = __import__('array').array('f', [1.0, 2.0, 3.0, 4.0])\n"
        + "decoded_exr = ns['_write_float_exr'](exr, values, ('V',), 'depth')\n"
        + "assert list(decoded_exr) == [1.0, 2.0, 3.0, 4.0]\n"
        + "image_input = ns['oiio'].ImageInput.open(str(exr))\n"
        + "assert image_input is not None\n"
        + "try:\n"
        + "    date_time = image_input.spec().get_string_attribute('DateTime')\n"
        + "    assert date_time == '1970:01:01 00:00:00'\n"
        + "finally:\n"
        + "    image_input.close()\n"
        + "print('NANTAI_CANONICAL_METADATA_OK', flush=True)\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NANTAI_CANONICAL_METADATA_OK" in result.stdout


def test_runtime_registry_requires_exact_renderable_coverage(tmp_path: Path) -> None:
    if not TEXTURED_RUNTIME_BLEND.is_file():
        pytest.skip("textured Windows runtime blend is unavailable")
    report_path = TEXTURED_RUNTIME_BLEND.with_name("build-report.json")
    result = _run_renderer_probe(
        tmp_path,
        _probe_prelude()
        + "import json\n"
        + f"report_path = __import__('pathlib').Path({str(report_path)!r})\n"
        + "report = json.loads(report_path.read_text('utf-8'))\n"
        + "registry = report['object_registry']\n"
        + "auxiliary = report['auxiliary_registry']\n"
        + "ns['_validate_object_registry_contract'](registry)\n"
        + "duplicate = [dict(row) for row in registry]\n"
        + "duplicate[1]['object_id'] = duplicate[0]['object_id']\n"
        + "try:\n"
        + "    ns['_validate_object_registry_contract'](duplicate)\n"
        + "except ns['RuntimeRenderError']:\n"
        + "    pass\n"
        + "else:\n"
        + "    raise AssertionError('duplicate stable ID was accepted')\n"
        + "ns['_validate_registry_mesh_coverage'](registry, auxiliary)\n"
        + "stable_id = registry[0]['object_id']\n"
        + "targets = [\n"
        + "    obj for obj in ns['bpy'].data.objects\n"
        + "    if obj.type == 'MESH' and obj.get('nv_stable_id') == stable_id\n"
        + "]\n"
        + "assert targets\n"
        + "for obj in targets:\n"
        + "    obj.hide_render = True\n"
        + "try:\n"
        + "    ns['_validate_registry_mesh_coverage'](registry, auxiliary)\n"
        + "except ns['RuntimeRenderError']:\n"
        + "    pass\n"
        + "else:\n"
        + "    raise AssertionError('missing renderable registry coverage was accepted')\n"
        + "for obj in targets:\n"
        + "    obj.hide_render = False\n"
        + "targets[0]['nv_instance_id'] = 126\n"
        + "try:\n"
        + "    ns['_validate_registry_mesh_coverage'](registry, auxiliary)\n"
        + "except ns['RuntimeRenderError']:\n"
        + "    pass\n"
        + "else:\n"
        + "    raise AssertionError('mesh registry tag mismatch was accepted')\n"
        + "print('NANTAI_REGISTRY_COVERAGE_OK', flush=True)\n",
        blend_path=TEXTURED_RUNTIME_BLEND,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NANTAI_REGISTRY_COVERAGE_OK" in result.stdout


@pytest.mark.parametrize(
    "mutation",
    [
        "target['nv_root_id'] = 'wrong-root'",
        "target['nv_material_id'] = int(target['nv_material_id']) + 1",
        "target['nv_variant_id'] = 'wrong-variant'",
        "target.parent = None",
        "target.hide_render = True",
        "target.hide_set(True)",
        (
            "stack = [bpy.context.view_layer.layer_collection]\n"
            "while stack:\n"
            "    layer = stack.pop()\n"
            "    if layer.collection in target.users_collection:\n"
            "        layer.exclude = True\n"
            "        break\n"
            "    stack.extend(layer.children)\n"
            "else:\n"
            "    raise AssertionError('target layer collection not found')"
        ),
        (
            "mesh = bpy.data.meshes.new('nv__unexpected-aux-mesh')\n"
            "extra = bpy.data.objects.new('nv__unexpected-aux', mesh)\n"
            "bpy.context.scene.collection.objects.link(extra)\n"
            "extra['nv_auxiliary'] = True"
        ),
        (
            "mesh = bpy.data.meshes.new('nv__unclassified-mesh')\n"
            "extra = bpy.data.objects.new('nv__unclassified', mesh)\n"
            "bpy.context.scene.collection.objects.link(extra)"
        ),
        "bpy.data.objects['nv__aux-terrain']['nv_semantic_id'] = 13",
        "bpy.data.objects['nv__aux-terrain']['nv_stable_id'] = 'wrong-aux-id'",
        "bpy.data.objects['nv__aux-terrain'].name = 'wrong-aux-name'",
        "bpy.data.objects['nv__aux-terrain'].hide_render = True",
        "bpy.data.worlds['World']['nv_auxiliary_id'] = 'wrong-world'",
        "bpy.data.objects['nv__camera-outer-001'].data.lens += 1.0",
        "bpy.data.objects['nv__camera-outer-001'].data.type = 'ORTHO'",
        "bpy.data.objects['nv__camera-outer-001'].data.sensor_fit = 'VERTICAL'",
        "bpy.data.objects['nv__camera-outer-001'].data.sensor_width = 35.0",
        "bpy.data.objects['nv__camera-outer-001'].data.shift_x = 0.1",
        "bpy.data.objects['nv__camera-outer-001'].data.shift_y = 0.1",
        "bpy.data.objects['nv__camera-outer-001'].data.clip_start = 0.2",
        "bpy.data.objects['nv__camera-outer-001'].data.clip_end = 1100.0",
        "bpy.data.objects['nv__camera-outer-001'].data.dof.use_dof = True",
    ],
    ids=(
        "root-id",
        "material-id",
        "variant-id",
        "parent",
        "hide-render",
        "hide-get",
        "collection-exclude",
        "extra-auxiliary",
        "unclassified",
        "auxiliary-semantic",
        "auxiliary-id",
        "auxiliary-name",
        "auxiliary-hidden",
        "world-id",
        "camera-lens",
        "camera-type",
        "camera-sensor-fit",
        "camera-sensor-width",
        "camera-shift-x",
        "camera-shift-y",
        "camera-clip-start",
        "camera-clip-end",
        "camera-dof",
    ),
)
def test_runtime_rejects_scene_contract_tampering_before_staging(
    tmp_path: Path,
    mutation: str,
) -> None:
    if not TEXTURED_RUNTIME_BLEND.is_file():
        pytest.skip("textured Windows runtime blend is unavailable")
    import pipeline.synthetic_village.canary as canary

    request = _textured_render_request()
    request_path = tmp_path / "render-request.json"
    request_path.write_bytes(canary.canonical_render_request_bytes(request))
    staging = tmp_path / "staging"
    indented_mutation = "\n".join(f"    {line}" for line in mutation.splitlines())
    result = _run_renderer_probe(
        tmp_path,
        _probe_prelude()
        + "import json\n"
        + "bpy = ns['bpy']\n"
        + f"request_path = __import__('pathlib').Path({str(request_path)!r})\n"
        + f"staging = __import__('pathlib').Path({str(staging)!r})\n"
        + "request = json.loads(request_path.read_text('utf-8'))\n"
        + "target = next(\n"
        + "    obj for obj in bpy.data.objects\n"
        + "    if obj.type == 'MESH' and not obj.get('nv_auxiliary', False)\n"
        + ")\n"
        + "def mutate():\n"
        + indented_mutation
        + "\nmutate()\n"
        + "try:\n"
        + "    ns['_validate_scene_and_prepare_indices'](request)\n"
        + "except ns['RuntimeRenderError']:\n"
        + "    assert not staging.exists()\n"
        + "else:\n"
        + "    staging.mkdir()\n"
        + "    raise AssertionError('tampered scene contract was accepted')\n"
        + "print('NANTAI_SCENE_TAMPER_REJECTED', flush=True)\n",
        blend_path=TEXTURED_RUNTIME_BLEND,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NANTAI_SCENE_TAMPER_REJECTED" in result.stdout


def test_runtime_derives_measured_opencv_pose(tmp_path: Path) -> None:
    result = _run_renderer_probe(
        tmp_path,
        _probe_prelude()
        + "matrix = [\n"
        + "    [1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0],\n"
        + "    [9.0, 10.0, 11.0, 12.0], [0.0, 0.0, 0.0, 1.0],\n"
        + "]\n"
        + "expected = [\n"
        + "    [1.0, -2.0, -3.0, 4.0], [5.0, -6.0, -7.0, 8.0],\n"
        + "    [9.0, -10.0, -11.0, 12.0], [0.0, 0.0, 0.0, 1.0],\n"
        + "]\n"
        + "assert ns['_blender_c2w_to_opencv'](matrix) == expected\n"
        + "print('NANTAI_MEASURED_POSE_OK', flush=True)\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NANTAI_MEASURED_POSE_OK" in result.stdout


@pytest.mark.skipif(
    not RUN_END_TO_END,
    reason="set NANTAI_RUN_BLENDER_RUNTIME_TESTS=1 for the real Blender render",
)
def test_runtime_renders_one_formal_camera_with_repeatable_private_outputs() -> None:
    if not TEXTURED_RUNTIME_BLEND.is_file():
        pytest.skip("textured Windows runtime blend is unavailable")
    import pipeline.synthetic_village.canary as canary

    request = _textured_render_request()
    ignored_root = ROOT / ".nantai-studio/synthetic-village/hybrid-v3/runtime-tests"
    ignored_root.mkdir(parents=True, exist_ok=True)
    container = ignored_root / uuid.uuid4().hex
    container.mkdir()
    try:
        isolated_blend = container / "village-canary.blend"
        shutil.copy2(TEXTURED_RUNTIME_BLEND, isolated_blend)
        request_path = container / "render-request.json"
        request_path.write_bytes(canary.canonical_render_request_bytes(request))
        staging_paths = (container / "run-a", container / "run-b")
        for staging in staging_paths:
            result = _run_renderer(
                isolated_blend,
                "--request",
                str(request_path),
                "--staging",
                str(staging),
                timeout=600,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert "NANTAI_RENDER_OK" in result.stdout
            assert not staging.with_name(
                f".{staging.name}.tmp-{request.render_id[:12]}",
            ).exists()

        camera_id = request.camera.camera_id
        relative_outputs = (
            f"rgb/{camera_id}.png",
            f"depth/{camera_id}.exr",
            f"normal/{camera_id}.exr",
            f"instance/{camera_id}.png",
            f"semantic/{camera_id}.png",
            f"cameras/{camera_id}.json",
        )
        for staging in staging_paths:
            assert {
                path.relative_to(staging).as_posix()
                for path in staging.rglob("*")
                if path.is_file()
            } == {*relative_outputs, "frame-report.json"}
            report = json.loads((staging / "frame-report.json").read_text("utf-8"))
            assert report["blender_executable_sha256"] == request.blender_executable_sha256
            assert (
                report["settings_sha256"]
                == hashlib.sha256(
                    canary._canonical_json_bytes(request.settings.model_dump(mode="json")),  # noqa: SLF001
                ).hexdigest()
            )
            assert len(report["artifacts"]) == 6
            for artifact in report["artifacts"]:
                artifact_path = staging / Path(artifact["path"])
                assert _sha256(artifact_path) == artifact["sha256"]
                assert artifact_path.stat().st_size == artifact["size_bytes"]

            rgb_contract = _decode_canonical_png(staging / relative_outputs[0])
            instance_contract = _decode_canonical_png(staging / relative_outputs[3])
            semantic_contract = _decode_canonical_png(staging / relative_outputs[4])
            assert rgb_contract[:4] == (1024, 576, 8, 2)
            assert instance_contract[:4] == (1024, 576, 16, 0)
            assert semantic_contract[:4] == (1024, 576, 8, 0)

            for relative in relative_outputs[1:3]:
                attributes = _read_exr_attributes(staging / relative)
                assert attributes["capDate"][1].rstrip(b"\0") == b"1970:01:01 00:00:00"
                metadata_blob = b"\n".join(
                    name.encode("ascii") + b"=" + value
                    for name, (_kind, value) in attributes.items()
                ).lower()
                assert b".nantai-studio" not in metadata_blob
                assert b"/users/" not in metadata_blob
                assert b"\\users\\" not in metadata_blob
                assert b"appdata" not in metadata_blob
                assert b"/tmp/" not in metadata_blob
                assert b"\\temp\\" not in metadata_blob

            camera_metadata = json.loads((staging / relative_outputs[5]).read_text("utf-8"))
            assert camera_metadata["blender_executable_sha256"] == (
                request.blender_executable_sha256
            )
            assert (
                camera_metadata["requested_c2w_opencv"]
                == request.camera.model_dump(
                    mode="json",
                )["c2w_opencv"]
            )
            assert (
                camera_metadata["requested_c2w_blender"]
                == request.camera.model_dump(
                    mode="json",
                )["c2w_blender"]
            )
            assert camera_metadata["measured_c2w_blender"] == [
                list(row) for row in request.measured_c2w_blender
            ]
            assert camera_metadata["measured_c2w_opencv"] == [
                list(row)
                for row in canary._blender_c2w_to_opencv(request.measured_c2w_blender)  # noqa: SLF001
            ]
            assert (
                max(
                    abs(
                        camera_metadata["requested_c2w_blender"][row][column]
                        - camera_metadata["measured_c2w_blender"][row][column]
                    )
                    for row in range(4)
                    for column in range(4)
                )
                > 0.0
            )

        first, second = staging_paths
        for relative in relative_outputs[1:]:
            assert (first / relative).read_bytes() == (second / relative).read_bytes()
        first_rgb = _decode_canonical_png(first / relative_outputs[0])[4]
        second_rgb = _decode_canonical_png(second / relative_outputs[0])[4]
        assert len(first_rgb) == len(second_rgb) == 1024 * 576 * 3
        maximum_rgb_delta = max(
            abs(left - right) for left, right in zip(first_rgb, second_rgb, strict=True)
        )
        assert maximum_rgb_delta <= 1
    finally:
        shutil.rmtree(container, ignore_errors=True)


def test_render_runtime_rejects_missing_request_before_staging(tmp_path: Path) -> None:
    if not TEXTURED_RUNTIME_BLEND.is_file():
        pytest.skip("textured Windows runtime blend is unavailable")
    result = _run_renderer(
        TEXTURED_RUNTIME_BLEND,
        "--request",
        str(tmp_path / "missing.json"),
        "--staging",
        str(tmp_path / "staging"),
    )

    assert result.returncode == 17
    assert "NANTAI_RENDER_ERROR request file does not exist" in (result.stdout + result.stderr)
    assert not (tmp_path / "staging").exists()


def test_runtime_rejects_missing_request_with_stable_error(tmp_path: Path) -> None:
    result = _run_builder(
        "--request",
        str(tmp_path / "missing.json"),
        "--staging",
        str(tmp_path / "staging"),
    )

    assert result.returncode == 17
    assert "NANTAI_BUILD_ERROR request file does not exist" in (result.stdout + result.stderr)
    assert not (tmp_path / "staging").exists()


def test_runtime_rejects_relative_argv_before_path_resolution(tmp_path: Path) -> None:
    result = _run_builder(
        "--request",
        "relative-request.json",
        "--staging",
        str(tmp_path / "staging"),
    )

    assert result.returncode == 17
    # The builder supports both legacy (--request/--staging) and textured
    # (--request/--materials/--staging) argv shapes; the absolute-path
    # guard covers all three paths, so the message mentions "material"
    # even when --materials is absent.
    assert "NANTAI_BUILD_ERROR request, material, and staging paths must be absolute" in (
        result.stdout + result.stderr
    )
    assert not (tmp_path / "staging").exists()


def test_runtime_rejects_duplicate_request_keys_before_creating_staging(
    tmp_path: Path,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_bytes(b'{"schema_version":"first","schema_version":"second"}\n')

    result = _run_builder(
        "--request",
        str(request_path),
        "--staging",
        str(tmp_path / "staging"),
    )

    assert result.returncode == 17
    assert "NANTAI_BUILD_ERROR request contains duplicate JSON key: schema_version" in (
        result.stdout + result.stderr
    )
    assert not (tmp_path / "staging").exists()


def test_runtime_rejects_redirected_request_leaf_before_reading(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"{}\n")
    redirected = tmp_path / "redirected.json"
    try:
        os.symlink(target, redirected)
    except OSError as exc:
        pytest.skip(f"file symlink creation is unavailable: {exc}")

    result = _run_builder(
        "--request",
        str(redirected),
        "--staging",
        str(tmp_path / "staging"),
    )

    assert result.returncode == 17
    assert "NANTAI_BUILD_ERROR request path is redirected" in (result.stdout + result.stderr)
    assert not (tmp_path / "staging").exists()


def test_runtime_rejects_junction_request_parent_before_reading(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    (real_parent / "request.json").write_bytes(b"{}\n")
    junction = tmp_path / "redirected-parent"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real_parent)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation is unavailable: {created.stdout}{created.stderr}")
    try:
        result = _run_builder(
            "--request",
            str(junction / "request.json"),
            "--staging",
            str(tmp_path / "staging"),
        )
    finally:
        os.rmdir(junction)

    assert result.returncode == 17
    assert "NANTAI_BUILD_ERROR request path is redirected" in (result.stdout + result.stderr)
    assert not (tmp_path / "staging").exists()


@pytest.mark.skipif(
    not RUN_END_TO_END,
    reason="set NANTAI_RUN_BLENDER_RUNTIME_TESTS=1 for the real Blender build",
)
def test_runtime_builds_and_reports_the_complete_canary(tmp_path: Path) -> None:
    from pipeline.synthetic_village.camera_plan import build_camera_plan
    from pipeline.synthetic_village.canary import (
        build_canary_request,
        canonical_build_request_bytes,
    )
    from pipeline.synthetic_village.scene_plan import build_scene_plan

    scene = build_scene_plan()
    camera = build_camera_plan(scene)
    request = build_canary_request(
        repo_root=ROOT,
        scene_plan=scene,
        camera_plan=camera,
        visual_pack_root=(ROOT / ".nantai-studio/synthetic-village/hybrid-v3/visual-sources"),
    )
    request_path = tmp_path / "request.json"
    request_path.write_bytes(canonical_build_request_bytes(request))
    staging = tmp_path / "staging"

    result = _run_builder(
        "--request",
        str(request_path),
        "--staging",
        str(staging),
        timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    expected = {
        "build-report.json",
        "preview-bridge.png",
        "preview-central.png",
        "preview-outer.png",
        "preview-upper.png",
        "village-canary.blend",
        "village-canary.glb",
    }
    assert {path.name for path in staging.iterdir()} == expected
    report = json.loads((staging / "build-report.json").read_text("utf-8"))
    assert report["build_id"] == request.build_id
    assert report["fidelity"] == "simplified-pbr-not-render-parity"
    assert report["counts"]["canonical_roots"] == 130
    assert report["counts"]["visual_materials"] == 24
    assert report["counts"]["cameras"] == 24
    assert report["counts"]["auxiliary_semantic_objects"] == 2
    assert report["validation"]["finite_nonempty_meshes"] is True
    assert report["validation"]["all_visual_material_slots_built"] is True
    assert report["validation"]["canary_critical_slots_fulfilled"] is True
    assert report["validation"]["prop_type_counts"] == {
        "bamboo-basket": 2,
        "farming-tools": 2,
        "firewood-stack": 2,
        "grain-rack": 2,
        "handcart": 2,
        "stone-trough": 2,
        "water-jar": 2,
        "wooden-bench": 2,
    }
    assert all(
        path.stat().st_size > 0 for path in staging.iterdir() if path.name != "build-report.json"
    )
