from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline.synthetic_village import canary
from pipeline.synthetic_village.building_geometry import (
    BUILDING_GEOMETRY_V2,
    expected_variant_counts,
)
from pipeline.synthetic_village.canary import TexturedBuildRequest
from pipeline.synthetic_village.elevated_topology import (
    canonical_elevated_topology_bytes,
)
from pipeline.synthetic_village.glb_material_audit import GlbMaterialAudit
from pipeline.synthetic_village.local_textured_preview import (
    LOCAL_TRAINING_BUILD_ENTRIES,
    LocalBlenderIdentity,
    LocalTexturedBuildReport,
    LocalTexturedPreviewError,
    LocalTexturedPreviewRequest,
    _expected_building_geometry,
    _publish_local_textured_training_build,
    build_local_textured_preview_manifest,
    build_local_textured_preview_request,
    canonical_local_glb_audit_bytes,
    canonical_local_textured_preview_request_bytes,
    verify_local_textured_training_build_layout,
    verify_stored_local_glb_audit,
)
from pipeline.synthetic_village.production_profile import (
    build_production_camera_plan,
)
from pipeline.synthetic_village.production_render import (
    LOCAL_PRODUCTION_RENDER_REPORT_SCHEMA,
    build_local_production_frame_request,
    canonical_local_production_render_request_bytes,
)
from pipeline.synthetic_village.scene_plan import build_scene_plan
from pipeline.synthetic_village.surface_realism import (
    LEGACY_SURFACE_PROFILE_ID,
    SURFACE_PROFILE_V1,
    canonical_surface_realism_plan_bytes,
)
from tests.synthetic_material_fixtures import publish_material_fixture

ROOT = Path(__file__).resolve().parents[1]
LOCAL_BLENDER = Path("/Applications/Blender.app/Contents/MacOS/Blender")
BLENDER_BUILDER = ROOT / "scripts/blender/build_synthetic_village.py"
BLENDER_RENDERER = ROOT / "scripts/blender/render_synthetic_village.py"
RUN_LOCAL_ELEVATED_BUILD = os.environ.get("NANTAI_RUN_LOCAL_ELEVATED_BUILD") == "1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _literal_assignment(name: str):
    tree = ast.parse(BLENDER_BUILDER.read_text("utf-8"))
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == name
                for target in statement.targets
            )
        ):
            return ast.literal_eval(statement.value)
    raise AssertionError(f"literal assignment is absent: {name}")


def _run_builder_probe(
    tmp_path: Path,
    source: str,
) -> subprocess.CompletedProcess[str]:
    probe = tmp_path / "builder-probe.py"
    probe.write_text(source, encoding="utf-8")
    return subprocess.run(
        [
            str(LOCAL_BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(probe),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def _local_request(tmp_path: Path) -> LocalTexturedPreviewRequest:
    visual_root, bundle = publish_material_fixture(tmp_path)
    identity = LocalBlenderIdentity(
        executable_sha256="1" * 64,
        version="4.5.11",
        platform="macos-arm64",
        runtime_build_hash="4db51e9d1e1e",
        runtime_output_sha256="2" * 64,
    )
    return build_local_textured_preview_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
        tool_identity=identity,
    )


def test_builder_declares_approved_surface_geometry_constants() -> None:
    assert _literal_assignment("SURFACE_PROFILE_V1") == (
        "source-consistent-multiscale-surface-v1"
    )
    assert _literal_assignment("SURFACE_TERRAIN_SPACING_M") == 4.0
    assert _literal_assignment("SURFACE_PATH_STEP_M") == 1.0
    assert _literal_assignment("SURFACE_PATH_LATERAL_RAILS") == 6

    source = BLENDER_BUILDER.read_text("utf-8")
    for required in (
        "ShaderNodeVertexColor",
        "ShaderNodeMixRGB",
        'type="FLOAT_COLOR"',
        'domain="CORNER"',
        "nv_surface_color",
    ):
        assert required in source


def test_local_blender_builds_continuous_surface_ribbon_contract(
    tmp_path: Path,
) -> None:
    if not LOCAL_BLENDER.is_file():
        pytest.skip("local Blender runtime is not installed")
    result = _run_builder_probe(
        tmp_path,
        "import runpy\n"
        f"ns = runpy.run_path({str(BLENDER_BUILDER)!r}, run_name='surface_probe')\n"
        "extent = {'width_m': 700.0, 'depth_m': 500.0, 'relief_m': 120.0}\n"
        "assert ns['_surface_terrain_contract'](extent) == (176, 126, 43750)\n"
        "points = [\n"
        "    {'x_m': -1.0, 'y_m': 0.0, 'z_m': 0.0},\n"
        "    {'x_m': 1.0, 'y_m': 0.0, 'z_m': 0.0},\n"
        "]\n"
        "plan = {\n"
        "    'longitudinal_step_m': 1.0,\n"
        "    'lateral_rail_count': 6,\n"
        "    'rut_runs': [],\n"
        "}\n"
        "mesh, intervals = ns['_surface_path_ribbon'](\n"
        "    points, 3.2, plan, extent,\n"
        ")\n"
        "assert intervals == 2\n"
        "assert len(mesh.vertices) == 18\n"
        "assert len(mesh.faces) == 10\n"
        "assert mesh.faces[4][2:] == mesh.faces[9][:2][::-1]\n"
        "assert all(len(set(face)) == 4 for face in mesh.faces)\n"
        "print('NANTAI_SURFACE_RIBBON_OK', flush=True)\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NANTAI_SURFACE_RIBBON_OK" in result.stdout


def test_local_blender_authors_float_corner_surface_color(
    tmp_path: Path,
) -> None:
    if not LOCAL_BLENDER.is_file():
        pytest.skip("local Blender runtime is not installed")
    runtime = ROOT / "scripts/blender/surface_realism_runtime.py"
    result = _run_builder_probe(
        tmp_path,
        "import runpy\n"
        "from types import SimpleNamespace\n"
        f"ns = runpy.run_path({str(BLENDER_BUILDER)!r}, run_name='surface_probe')\n"
        f"runtime_ns = runpy.run_path({str(runtime)!r}, run_name='surface_runtime')\n"
        "runtime = SimpleNamespace(sample_macro_color=runtime_ns['sample_macro_color'])\n"
        "bpy = ns['bpy']\n"
        "mesh = bpy.data.meshes.new('surface-color-probe-mesh')\n"
        "mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])\n"
        "mesh.update()\n"
        "material = bpy.data.materials.new('surface-color-probe-material')\n"
        "material['nv_slot_id'] = 'material-packed-earth-01'\n"
        "mesh.materials.append(material)\n"
        "obj = bpy.data.objects.new('surface-color-probe', mesh)\n"
        "bpy.context.scene.collection.objects.link(obj)\n"
        "obj['nv_root_id'] = 'path-network-001'\n"
            "palette = [[3605 + i % 300, 3700 + i % 250, 3800 + i % 200] for i in range(256)]\n"
        "request = {\n"
        "    'surface_realism_profile_id': ns['SURFACE_PROFILE_V1'],\n"
        "    'surface_realism_plan': {\n"
        "        'scene_seed': 20260715,\n"
        "        'terrain_period_m': 20.0,\n"
        "        'ground_period_m': 10.0,\n"
        "        'macro_palettes': [{\n"
        "            'slot_id': 'material-packed-earth-01',\n"
        "            'source_sha256': 'a' * 64,\n"
        "            'palette_sha256': 'b' * 64,\n"
        "            'multipliers_q': palette,\n"
        "        }],\n"
        "    },\n"
        "}\n"
        "ns['_apply_surface_color_attribute'](obj, request, runtime)\n"
            "layer = mesh.color_attributes['nv_surface_color']\n"
            "assert layer.data_type == 'FLOAT_COLOR'\n"
            "assert layer.domain == 'CORNER'\n"
            "transport = mesh.attributes['_NV_SURFACE_COLOR']\n"
            "assert transport.data_type == 'FLOAT_VECTOR'\n"
            "assert transport.domain == 'CORNER'\n"
            "assert len(transport.data) == len(layer.data)\n"
            "assert mesh.color_attributes.active_color_index == 0\n"
            "assert mesh.color_attributes.render_color_index == 0\n"
        "colors = [tuple(row.color) for row in layer.data]\n"
        "assert len(colors) == 3\n"
            "assert any(color[:3] != (1.0, 1.0, 1.0) for color in colors)\n"
            "assert all(0.88 <= value <= 1.10 for color in colors for value in color[:3])\n"
            "assert obj['nv_surface_color_mode'] == 'macro'\n"
            "obj['nv_surface_detail_class'] = 'damp-patch'\n"
            "request['surface_realism_plan']['macro_palettes'][0]['multipliers_q'] = (\n"
            "    [[3605, 3605, 3605] for _ in range(256)]\n"
            ")\n"
            "ns['_apply_surface_color_attribute'](obj, request, runtime)\n"
            "damp = [tuple(row.color) for row in layer.data]\n"
            "assert len({color[:3] for color in damp}) > 1\n"
            "assert all(0.88 <= value <= 1.10 for color in damp for value in color[:3])\n"
            "assert obj['nv_surface_color_mode'] == 'damp'\n"
            "print('NANTAI_SURFACE_COLOR_OK', flush=True)\n",
        )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NANTAI_SURFACE_COLOR_OK" in result.stdout


def test_local_blender_normalizes_exported_surface_colors_to_float_vec4(
    tmp_path: Path,
) -> None:
    if not LOCAL_BLENDER.is_file():
        pytest.skip("local Blender runtime is not installed")
    output = tmp_path / "normalized-colors.glb"
    result = _run_builder_probe(
        tmp_path,
        "import json\n"
        "import runpy\n"
        "import struct\n"
        "from pathlib import Path\n"
        f"ns = runpy.run_path({str(BLENDER_BUILDER)!r}, run_name='surface_probe')\n"
        "bpy = ns['bpy']\n"
        "bpy.ops.object.select_all(action='SELECT')\n"
        "bpy.ops.object.delete(use_global=False)\n"
        "def add_surface(name, colors, x_offset):\n"
        "    mesh = bpy.data.meshes.new(name + '-mesh')\n"
        "    mesh.from_pydata([(x_offset, 0, 0), (x_offset + 1, 0, 0), "
        "(x_offset, 1, 0)], [], [(0, 1, 2)])\n"
        "    mesh.update()\n"
        "    layer = mesh.color_attributes.new(\n"
        "        name='nv_surface_color', type='FLOAT_COLOR', domain='CORNER',\n"
        "    )\n"
        "    transport = mesh.attributes.new(\n"
        "        name='_NV_SURFACE_COLOR', type='FLOAT_VECTOR', domain='CORNER',\n"
        "    )\n"
        "    for row, carrier, color in zip(\n"
        "        layer.data, transport.data, colors, strict=True,\n"
        "    ):\n"
        "        row.color = (1, 1, 1, 1)\n"
        "        carrier.vector = color[:3]\n"
        "    mesh.color_attributes.active_color = layer\n"
        "    material = bpy.data.materials.new(name + '-material')\n"
        "    material.use_nodes = True\n"
        "    vertex = material.node_tree.nodes.new('ShaderNodeVertexColor')\n"
        "    vertex.layer_name = 'nv_surface_color'\n"
        "    principled = material.node_tree.nodes.get('Principled BSDF')\n"
        "    material.node_tree.links.new(\n"
        "        vertex.outputs['Color'], principled.inputs['Base Color'],\n"
        "    )\n"
        "    mesh.materials.append(material)\n"
        "    obj = bpy.data.objects.new(name, mesh)\n"
        "    bpy.context.scene.collection.objects.link(obj)\n"
        "add_surface('white', [(1, 1, 1, 1)] * 3, 0)\n"
        "add_surface('macro', [\n"
        "    (0.88, 0.92, 1.04, 1),\n"
        "    (0.94, 1.00, 1.08, 1),\n"
        "    (1.02, 1.06, 1.10, 1),\n"
        "], 2)\n"
        "add_surface('damp', [\n"
        "    (3605 / 4096 + index / 255,) * 3 + (1,)\n"
        "    for index in range(3)\n"
        "], 4)\n"
        f"output = Path({str(output)!r})\n"
        "bpy.ops.export_scene.gltf(\n"
        "    filepath=str(output), export_format='GLB', export_apply=True,\n"
        "    export_vertex_color='ACTIVE',\n"
        "    export_all_vertex_colors=False,\n"
        "    export_attributes=True,\n"
        ")\n"
        "ns['_normalize_surface_color_accessors'](output)\n"
        "raw = output.read_bytes()\n"
        "json_length = struct.unpack_from('<I', raw, 12)[0]\n"
        "document = json.loads(raw[20:20 + json_length].decode('utf-8'))\n"
        "color_accessors = [\n"
        "    document['accessors'][primitive['attributes']['COLOR_0']]\n"
        "    for mesh in document['meshes']\n"
        "    for primitive in mesh['primitives']\n"
        "    if 'COLOR_0' in primitive['attributes']\n"
        "]\n"
        "assert all(\n"
        "    '_NV_SURFACE_COLOR' not in primitive['attributes']\n"
        "    for mesh in document['meshes']\n"
        "    for primitive in mesh['primitives']\n"
        ")\n"
        "assert len(color_accessors) == 3\n"
        "assert all(row['componentType'] == 5126 for row in color_accessors)\n"
        "assert all(row['type'] == 'VEC4' for row in color_accessors)\n"
        "assert all(row.get('normalized', False) is False for row in color_accessors)\n"
        "binary_start = 20 + json_length + 8\n"
        "decoded = []\n"
        "for accessor in color_accessors:\n"
        "    view = document['bufferViews'][accessor['bufferView']]\n"
        "    offset = binary_start + view.get('byteOffset', 0)\n"
        "    values = struct.unpack_from(\n"
        "        '<' + 'f' * accessor['count'] * 4, raw, offset,\n"
        "    )\n"
        "    decoded.append(tuple(values))\n"
        "assert all(\n"
        "    values[index] == 1.0\n"
        "    for values in decoded\n"
        "    for index in range(3, len(values), 4)\n"
        ")\n"
        "assert any(max(values) > 1.0 for values in decoded)\n"
        "assert any(all(value == 1.0 for value in values) for values in decoded)\n"
        "assert any(\n"
        "    max(value for index, value in enumerate(values) if index % 4 != 3) < 1.0\n"
        "    and len({values[index:index + 3] for index in range(0, len(values), 4)}) > 1\n"
        "    for values in decoded\n"
        ")\n"
        "print('NANTAI_SURFACE_COLOR_GLB_OK', flush=True)\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NANTAI_SURFACE_COLOR_GLB_OK" in result.stdout


def test_local_blender_builds_three_consolidated_detail_meshes(
    tmp_path: Path,
) -> None:
    if not LOCAL_BLENDER.is_file():
        pytest.skip("local Blender runtime is not installed")
    result = _run_builder_probe(
        tmp_path,
        "import runpy\n"
        f"ns = runpy.run_path({str(BLENDER_BUILDER)!r}, run_name='surface_probe')\n"
        "extent = {'width_m': 700.0, 'depth_m': 500.0, 'relief_m': 120.0}\n"
        "points = [\n"
        "    {'x_m': 0.0, 'y_m': 0.0, 'z_m': 0.0},\n"
        "    {'x_m': 20.0, 'y_m': 0.0, 'z_m': 0.0},\n"
        "]\n"
        "plan = {\n"
        "    'details': [\n"
            "        {'detail_id': 'path-network-001:stone:000', "
            "'detail_class': 'stone-fragment', 'arc_length_m': 5.0, "
            "'side_fraction': 0.72, 'scale': 0.8, 'yaw_deg': 20.0},\n"
            "        {'detail_id': 'path-network-001:leaf:000', "
            "'detail_class': 'leaf-card', 'arc_length_m': 10.0, "
            "'side_fraction': -0.72, 'scale': 0.8, 'yaw_deg': 70.0},\n"
            "        {'detail_id': 'path-network-001:damp:000', "
            "'detail_class': 'damp-patch', 'arc_length_m': 15.0, "
            "'side_fraction': 0.72, 'scale': 0.8, 'yaw_deg': 120.0},\n"
        "    ],\n"
        "}\n"
        "parts, counts = ns['_surface_detail_assemblers'](\n"
        "    points, 3.2, plan, extent,\n"
        ")\n"
        "assert tuple(parts) == ('damp-patch', 'leaf-card', 'stone-fragment')\n"
        "assert counts == {'damp-patch': 1, 'leaf-card': 1, 'stone-fragment': 1}\n"
        "assert len(parts['damp-patch'].faces) == 8\n"
        "assert len(parts['leaf-card'].faces) == 2\n"
        "assert len(parts['stone-fragment'].faces) == 21\n"
        "for part in parts.values():\n"
        "    assert part.vertices and part.faces\n"
        "    assert all(0.6 < abs(vertex[1]) < 1.6 for vertex in part.vertices)\n"
        "print('NANTAI_SURFACE_DETAILS_OK', flush=True)\n",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NANTAI_SURFACE_DETAILS_OK" in result.stdout


def test_builder_declares_combined_surface_budgets() -> None:
    assert _literal_assignment("MAX_SURFACE_GLTF_TRIANGLES") == 125_000
    assert _literal_assignment("MAX_SURFACE_GLB_BYTES") == 160_000_000
    assert _literal_assignment("EXPECTED_SURFACE_GLB_PRIMITIVES") == 577
    assert _literal_assignment("EXPECTED_SURFACE_DETAIL_MESH_OBJECTS") == 18


def test_local_request_is_content_addressed_but_never_authoritative(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)

    assert request.verification_level == "L0"
    assert request.authoritative is False
    assert request.release_channel == "local-preview-only"
    assert request.tool_identity.platform == "macos-arm64"
    assert request.material_algorithm_id == "edge-feather-sobel-orm-v2"
    assert request.building_geometry_profile_id == BUILDING_GEOMETRY_V2
    assert request.surface_realism_profile_id == SURFACE_PROFILE_V1
    assert request.surface_realism_plan is not None
    assert request.surface_realism_plan.plan_sha256 == hashlib.sha256(
        canonical_surface_realism_plan_bytes(request.surface_realism_plan),
    ).hexdigest()
    assert request.elevated_topology.scene_plan_sha256 == (
        request.source_hashes.scene_plan_sha256
    )
    assert request.source_hashes.elevated_topology_sha256 == hashlib.sha256(
        canonical_elevated_topology_bytes(request.elevated_topology),
    ).hexdigest()
    assert (
        hashlib.sha256(
            canonical_local_textured_preview_request_bytes(
                request,
                exclude_preview_id=True,
            ),
        ).hexdigest()
        == request.preview_id
    )
    raw = canonical_local_textured_preview_request_bytes(request)
    assert (
        b'"building_geometry_profile_id": "four-sided-rural-building-v2"'
        in raw
    )
    assert b"source-consistent-multiscale-surface-v1" in raw
    assert raw.endswith(b"\n")
    assert b".nantai-studio" not in raw
    assert str(Path.home()).encode() not in raw


def test_local_request_cannot_validate_as_authoritative_request(tmp_path: Path) -> None:
    request = _local_request(tmp_path)

    with pytest.raises(ValidationError):
        TexturedBuildRequest.model_validate(request.model_dump())


def test_local_blender_rejects_readdressed_invalid_topology_before_staging(
    tmp_path: Path,
) -> None:
    if not LOCAL_BLENDER.is_file():
        pytest.skip("local Blender runtime is not installed")
    visual_root, bundle = publish_material_fixture(tmp_path / "bundle")
    request = build_local_textured_preview_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
        tool_identity=LocalBlenderIdentity(
            executable_sha256=_sha256_file(LOCAL_BLENDER),
            version="4.5.11",
            platform="macos-arm64",
            runtime_build_hash="4db51e9d1e1e",
            runtime_output_sha256=hashlib.sha256(b"runtime-probe").hexdigest(),
        ),
    )
    payload = request.model_dump(mode="json")
    payload["elevated_topology"]["semantic_id"] = 13
    payload["source_hashes"]["elevated_topology_sha256"] = hashlib.sha256(
        canary._canonical_json_bytes(payload["elevated_topology"]),
    ).hexdigest()
    unsigned = dict(payload)
    unsigned.pop("preview_id")
    payload["preview_id"] = hashlib.sha256(
        canary._canonical_json_bytes(unsigned),
    ).hexdigest()
    request_path = tmp_path / "invalid-topology-request.json"
    request_path.write_bytes(canary._canonical_json_bytes(payload))
    staging = tmp_path / "staging"

    result = subprocess.run(
        [
            str(LOCAL_BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(BLENDER_BUILDER),
            "--",
            "--request",
            str(request_path),
            "--materials",
            str(bundle.final_directory),
            "--staging",
            str(staging),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )

    assert result.returncode == 17
    assert "elevated topology provenance or scene binding is invalid" in (
        result.stdout + result.stderr
    )
    assert not staging.exists()


@pytest.mark.skipif(
    not RUN_LOCAL_ELEVATED_BUILD,
    reason="set NANTAI_RUN_LOCAL_ELEVATED_BUILD=1 for the real local Blender build",
)
def test_local_blender_builds_four_registered_elevated_components(
    tmp_path: Path,
) -> None:
    visual_root, bundle = publish_material_fixture(tmp_path / "bundle")
    request = build_local_textured_preview_request(
        repo_root=ROOT,
        visual_pack_root=visual_root,
        material_bundle_root=bundle.final_directory,
        tool_identity=LocalBlenderIdentity(
            executable_sha256=_sha256_file(LOCAL_BLENDER),
            version="4.5.11",
            platform="macos-arm64",
            runtime_build_hash="4db51e9d1e1e",
            runtime_output_sha256=hashlib.sha256(b"runtime-probe").hexdigest(),
        ),
    )
    request_path = tmp_path / "request.json"
    request_path.write_bytes(
        canonical_local_textured_preview_request_bytes(request),
    )
    invocation_root = tmp_path / "invocation"
    invocation_root.mkdir()
    canary.snapshot_material_inputs(
        request=request,  # type: ignore[arg-type]
        material_bundle_root=bundle.final_directory,
        invocation_root=invocation_root,
    )
    staging = tmp_path / "staging"
    result = subprocess.run(
        [
            str(LOCAL_BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            "--python",
            str(BLENDER_BUILDER),
            "--",
            "--request",
            str(request_path),
            "--materials",
            str(invocation_root / "material-inputs"),
            "--staging",
            str(staging),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(
        (staging / "build-report.json").read_text("utf-8"),
    )
    assert report["counts"]["canonical_roots"] == 130
    probe = tmp_path / "probe-elevated.py"
    probe.write_text(
        """
import bpy

expected = {
    "elevated-switchback-stair-v1": {
        "walkable-stair-treads",
        "collision-side-rails",
        "structural-supports",
    },
    "covered-timber-gallery-v1": {
        "walkable-timber-deck",
        "collision-side-rails",
        "covered-roof",
        "structural-supports",
    },
    "terrace-ramp-junction-v1": {
        "walkable-ramp-deck",
        "collision-side-rails",
        "drainage-separation",
        "structural-supports",
    },
    "cross-level-covered-passage-v1": {
        "walkable-cross-level-decks",
        "collision-side-rails",
        "covered-roof",
        "structural-supports",
    },
}
for instance_id, (component_id, parts) in enumerate(expected.items(), 127):
    root = bpy.data.objects.get(f"nv__{component_id}")
    assert root is not None
    assert root["nv_instance_id"] == instance_id
    assert root["nv_semantic_id"] == 14
    assert root["nv_semantic_class"] == "elevated-walkway"
    actual = {
        child["nv_part_id"]
        for child in root.children
        if child.type == "MESH"
    }
    assert actual == parts
    assert all(child.data.polygons for child in root.children if child.type == "MESH")
print("NANTAI_ELEVATED_COMPONENTS_OK", flush=True)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    probe_result = subprocess.run(
        [
            str(LOCAL_BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            str(staging / "village-canary.blend"),
            "--python",
            str(probe),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert probe_result.returncode == 0, probe_result.stdout + probe_result.stderr
    assert "NANTAI_ELEVATED_COMPONENTS_OK" in probe_result.stdout

    parsed_report = LocalTexturedBuildReport.model_validate_json(
        (staging / "build-report.json").read_bytes(),
    )
    frame_request = build_local_production_frame_request(
        plan=build_production_camera_plan(),
        camera_id="camera-elevated-pedestrian-001",
        build_id=parsed_report.preview_id,
        blender_executable_sha256=_sha256_file(LOCAL_BLENDER),
        renderer_script_sha256=_sha256_file(BLENDER_RENDERER),
        blend_sha256=_sha256_file(staging / "village-canary.blend"),
        build_report_sha256=_sha256_file(staging / "build-report.json"),
        object_registry=parsed_report.object_registry,
        auxiliary_registry=parsed_report.auxiliary_registry,
        semantic_registry=parsed_report.semantic_registry,
    )
    frame_request_path = tmp_path / "production-render-request.json"
    frame_request_path.write_bytes(
        canonical_local_production_render_request_bytes(frame_request),
    )
    frame_staging = tmp_path / "production-frame"
    render_result = subprocess.run(
        [
            str(LOCAL_BLENDER),
            "--background",
            "--factory-startup",
            "--disable-autoexec",
            "--python-exit-code",
            "17",
            str(staging / "village-canary.blend"),
            "--python",
            str(BLENDER_RENDERER),
            "--",
            "--request",
            str(frame_request_path),
            "--staging",
            str(frame_staging),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert render_result.returncode == 0, render_result.stdout + render_result.stderr
    frame_report = json.loads(
        (frame_staging / "frame-report.json").read_text("utf-8"),
    )
    assert frame_report["schema_version"] == LOCAL_PRODUCTION_RENDER_REPORT_SCHEMA
    assert frame_report["verification_level"] == "L0"
    assert frame_report["camera_id"] == "camera-elevated-pedestrian-001"
    assert frame_report["statistics"]["semantic_ids"][-1] == 14
    assert len(frame_report["artifacts"]) == 6


def test_historical_local_request_omits_absent_geometry_profile(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)
    payload = dict(request.__dict__)
    payload.pop("preview_id")
    payload.pop("building_geometry_profile_id")
    historical_id = hashlib.sha256(canary._canonical_json_bytes(payload)).hexdigest()

    historical = LocalTexturedPreviewRequest(
        preview_id=historical_id,
        **payload,
    )
    raw = canonical_local_textured_preview_request_bytes(historical)

    assert historical.building_geometry_profile_id == "front-facade-box-v1"
    assert b"building_geometry_profile_id" not in raw


def test_historical_local_request_omits_absent_surface_defaults(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)
    payload = dict(request.__dict__)
    payload.pop("preview_id")
    payload.pop("surface_realism_profile_id")
    payload.pop("surface_realism_plan")
    historical_id = hashlib.sha256(canary._canonical_json_bytes(payload)).hexdigest()

    historical = LocalTexturedPreviewRequest(
        preview_id=historical_id,
        **payload,
    )
    raw = canonical_local_textured_preview_request_bytes(historical)

    assert historical.surface_realism_profile_id == LEGACY_SURFACE_PROFILE_ID
    assert historical.surface_realism_plan is None
    assert b"surface_realism" not in raw


def test_local_manifest_is_preview_only_and_not_real_photo_texture(
    tmp_path: Path,
) -> None:
    request = _local_request(tmp_path)
    manifest = build_local_textured_preview_manifest(
        request=request,
        glb_sha256="3" * 64,
        glb_bytes=1024,
        build_report_sha256="4" * 64,
        audit_sha256="5" * 64,
    )

    assert manifest.schema_version == 2
    assert manifest.synthetic is True
    assert manifest.geometry_usability == "preview-only"
    assert manifest.material_fidelity == "synthetic-derived-pbr"
    assert manifest.synthetic_pbr_textures is True
    assert manifest.real_photo_textures is False
    assert manifest.dynamic_mesh_relighting is True
    assert manifest.splat_relighting is False
    assert manifest.authoritative is False
    assert manifest.verification_level == "L0"
    assert manifest.release_channel == "local-preview-only"
    assert manifest.model_url.endswith(
        f"/{request.preview_id}/village-canary.glb",
    )
    assert "local-preview-only" in manifest.limitations


def test_historical_local_glb_audit_omits_absent_geometry_evidence() -> None:
    audit = GlbMaterialAudit(
        glb_sha256="3" * 64,
        byte_count=1024,
        mesh_count=1,
        primitive_count=1,
        triangle_count=1,
        material_count=1,
        texture_count=3,
        embedded_image_count=3,
        textured_primitive_count=1,
        uv_primitive_count=1,
        tangent_primitive_count=1,
        slot_ids=("material-fieldstone-01",),
    )

    raw = canonical_local_glb_audit_bytes(audit)

    assert audit.building_geometry is None
    assert b"building_geometry" not in raw
    assert audit.surface_realism is None
    assert b"surface_realism" not in raw


def test_historical_local_glb_audit_remeasures_new_triangle_evidence(
    tmp_path: Path,
) -> None:
    measured = GlbMaterialAudit(
        glb_sha256="3" * 64,
        byte_count=1024,
        mesh_count=1,
        primitive_count=1,
        triangle_count=7,
        material_count=1,
        texture_count=3,
        embedded_image_count=3,
        textured_primitive_count=1,
        uv_primitive_count=1,
        tangent_primitive_count=1,
        slot_ids=("material-fieldstone-01",),
    )
    historical_payload = measured.model_dump(mode="json")
    historical_payload.pop("triangle_count")
    historical_payload.pop("building_geometry")
    historical_payload.pop("surface_realism")
    audit_path = tmp_path / "glb-material-audit.json"
    audit_path.write_bytes(canary._canonical_json_bytes(historical_payload))

    assert (
        verify_stored_local_glb_audit(
            audit_path,
            measured_audit=measured,
        )
        == measured
    )

    historical_payload["primitive_count"] = 2
    audit_path.write_bytes(canary._canonical_json_bytes(historical_payload))
    with pytest.raises(LocalTexturedPreviewError, match="current GLB bytes"):
        verify_stored_local_glb_audit(
            audit_path,
            measured_audit=measured,
        )


def test_local_v2_report_derives_exact_glb_geometry_expectation() -> None:
    building_ids = tuple(
        row.object_id
        for row in build_scene_plan().objects
        if row.semantic_class == "building"
    )
    report = SimpleNamespace(
        building_geometry_profile_id=BUILDING_GEOMETRY_V2,
        building_geometry=SimpleNamespace(
            added_face_count=8659,
            maximum_added_faces_per_building=124,
            variant_counts=expected_variant_counts(
                building_ids,
                BUILDING_GEOMETRY_V2,
            ),
        ),
        counts=SimpleNamespace(glb_primitives=544),
        semantic_registry=(
            SimpleNamespace(semantic_class="building", semantic_id=3),
        ),
        object_registry=tuple(
            SimpleNamespace(object_id=object_id, semantic_id=3)
            for object_id in building_ids
        ),
    )

    expected = _expected_building_geometry(report)

    assert expected is not None
    assert expected.expected_building_ids == building_ids
    assert expected.expected_primitive_count == 544
    assert expected.expected_added_face_count == 8659
    assert expected.expected_maximum_added_faces_per_building == 124
    assert expected.maximum_total_triangles == 100_000

    report.surface_realism_profile_id = SURFACE_PROFILE_V1
    assert _expected_building_geometry(report).maximum_total_triangles == 125_000

    tampered_rows = list(report.object_registry)
    tampered_rows[0] = SimpleNamespace(
        object_id="building-tampered-001",
        semantic_id=3,
    )
    report.object_registry = tuple(tampered_rows)
    with pytest.raises(LocalTexturedPreviewError, match="canonical scene set"):
        _expected_building_geometry(report)


def test_local_models_reject_trust_or_texture_upgrades(tmp_path: Path) -> None:
    request = _local_request(tmp_path)
    request_payload = request.model_dump()
    request_payload["authoritative"] = True
    with pytest.raises(ValidationError):
        LocalTexturedPreviewRequest.model_validate(request_payload)

    manifest = build_local_textured_preview_manifest(
        request=request,
        glb_sha256="3" * 64,
        glb_bytes=1024,
        build_report_sha256="4" * 64,
        audit_sha256="5" * 64,
    )
    for key, value in (
        ("real_photo_textures", True),
        ("geometry_usability", "metric-aligned"),
        ("splat_relighting", True),
        ("authoritative", True),
    ):
        payload = manifest.model_dump()
        payload[key] = value
        with pytest.raises(ValidationError):
            type(manifest).model_validate(payload)


def test_training_build_layout_is_report_content_addressed_and_exact(
    tmp_path: Path,
) -> None:
    report_bytes = b"canonical-build-report\n"
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    directory = tmp_path / report_sha256
    directory.mkdir()
    for name in LOCAL_TRAINING_BUILD_ENTRIES:
        (directory / name).write_bytes(
            report_bytes if name == "build-report.json" else name.encode("utf-8"),
        )

    assert (
        verify_local_textured_training_build_layout(
            directory,
            expected_report_sha256=report_sha256,
        )
        == directory
    )

    (directory / "unexpected.bin").write_bytes(b"no")
    with pytest.raises(LocalTexturedPreviewError, match="exact nine-file set"):
        verify_local_textured_training_build_layout(
            directory,
            expected_report_sha256=report_sha256,
        )


def test_training_build_publication_copies_exact_snapshot_once(
    tmp_path: Path,
) -> None:
    source = tmp_path / "verified-staging"
    source.mkdir()
    report_bytes = b"verified-report\n"
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()
    for name in LOCAL_TRAINING_BUILD_ENTRIES:
        (source / name).write_bytes(
            report_bytes if name == "build-report.json" else name.encode("utf-8"),
        )
    root = tmp_path / "training-builds"
    root.mkdir()

    published = _publish_local_textured_training_build(
        staging=source,
        training_root=root,
        build_report_sha256=report_sha256,
    )

    assert published == root / report_sha256
    assert source.is_dir()
    assert {
        path.name: path.read_bytes() for path in published.iterdir()
    } == {
        path.name: path.read_bytes() for path in source.iterdir()
    }
    assert (
        _publish_local_textured_training_build(
            staging=source,
            training_root=root,
            build_report_sha256=report_sha256,
        )
        == published
    )


def test_builder_keeps_local_schema_and_authoritative_schema_separate() -> None:
    source = (
        ROOT / "scripts/blender/build_synthetic_village.py"
    ).read_text("utf-8")

    assert "local-textured-preview-request.v1" in source
    assert "local-textured-preview-build-report.v1" in source
    assert 'scene["nv_authoritative"] = False' in source
    assert 'tool["platform"] != ("macos-arm64" if local else "windows-x64")' in source
