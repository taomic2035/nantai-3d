"""Direct tests for the Blender-side exact-218 wrapper boundaries."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from pipeline.synthetic_village import canary

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_WRAPPER = (
    ROOT / "scripts/blender/preflight_reciprocal_route_cameras.py"
)
RENDER_WRAPPER = ROOT / "scripts/blender/render_reciprocal_route_production.py"


def _load_wrapper(path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    fake_bpy = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    spec = importlib.util.spec_from_file_location(
        f"test_{path.stem}",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry_payload(count: int = 218) -> list[dict[str, object]]:
    return [
        canary.ObjectRegistryEntry(
            object_id=f"test-object-{instance_id:03d}",
            instance_id=instance_id,
            semantic_id=3,
            material_id=1,
            variant_id=None,
        ).model_dump(mode="json")
        for instance_id in range(1, count + 1)
    ]


def _boundary_payload(script_path: Path) -> dict[str, object]:
    registry = _registry_payload()
    return {
        "schema_version": (
            "nantai.synthetic-village."
            "reciprocal-production-clearance-request.v1"
        ),
        "preflight_script_sha256": hashlib.sha256(
            script_path.read_bytes(),
        ).hexdigest(),
        "build_id": "a" * 64,
        "reciprocal_route_module_plan_sha256": "b" * 64,
        "object_registry": registry,
        "object_registry_sha256": hashlib.sha256(
            canary._canonical_json_bytes(registry),  # noqa: SLF001
        ).hexdigest(),
    }


def _render_boundary_payload(script_path: Path) -> dict[str, object]:
    registry = _registry_payload()
    return {
        "schema_version": (
            "nantai.synthetic-village.local-production-render-frame-request.v5"
        ),
        "renderer_script_sha256": hashlib.sha256(
            script_path.read_bytes(),
        ).hexdigest(),
        "build_id": "a" * 64,
        "reciprocal_route_module_plan_sha256": "b" * 64,
        "environment_module_build_report_sha256": "c" * 64,
        "build_adapter": "windows-reciprocal-route-v1",
        "object_registry": registry,
        "object_registry_sha256": hashlib.sha256(
            canary._canonical_json_bytes(registry),  # noqa: SLF001
        ).hexdigest(),
    }


def _scene_lineage() -> dict[str, object]:
    return {
        "nv_reciprocal_route_module_build": json.dumps(
            {
                "build_id": "a" * 64,
                "reciprocal_route_module_plan_sha256": "b" * 64,
                "geometry_usability": "preview-only",
                "module_root_count": 43,
                "topology_proxy_count": 6,
                "stage": "modeled-unverified",
                "trust_effect": "none",
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
    }


class _FakeTopologyProxy(dict):
    type = "MESH"
    hide_render = True
    hide_viewport = False


def _topology_proxies() -> list[_FakeTopologyProxy]:
    return [
        _FakeTopologyProxy(
            nv_proxy_topology=True,
            nv_stable_id=f"path-network-001::role-{index}",
            nv_stage="modeled-unverified",
            nv_trust_effect="none",
            nv_geometry_usability="preview-only",
        )
        for index in range(6)
    ]


@pytest.mark.parametrize("wrapper_path", (PREFLIGHT_WRAPPER, RENDER_WRAPPER))
def test_wrapper_hides_six_verified_topology_proxies_from_production(
    wrapper_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(wrapper_path, monkeypatch)
    proxies = _topology_proxies()

    wrapper._prepare_topology_proxies_for_production(proxies)

    assert all(proxy.hide_render for proxy in proxies)
    assert all(proxy.hide_viewport for proxy in proxies)


@pytest.mark.parametrize("wrapper_path", (PREFLIGHT_WRAPPER, RENDER_WRAPPER))
def test_wrapper_rejects_visible_topology_proxy(
    wrapper_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(wrapper_path, monkeypatch)
    proxies = _topology_proxies()
    proxies[0].hide_render = False

    error_type = (
        wrapper.RuntimePreflightError
        if wrapper_path == PREFLIGHT_WRAPPER
        else wrapper.RuntimeRenderError
    )
    with pytest.raises(error_type, match="topology proxy"):
        wrapper._prepare_topology_proxies_for_production(proxies)


def test_preflight_wrapper_accepts_exact_218_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(PREFLIGHT_WRAPPER, monkeypatch)

    wrapper._validate_reciprocal_boundary(
        _boundary_payload(PREFLIGHT_WRAPPER),
        scene=_scene_lineage(),
        script_path=PREFLIGHT_WRAPPER,
    )


@pytest.mark.parametrize("count", (130, 175, 217, 219))
def test_preflight_wrapper_rejects_non_218_registry(
    count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(PREFLIGHT_WRAPPER, monkeypatch)
    request = _boundary_payload(PREFLIGHT_WRAPPER)
    request["object_registry"] = _registry_payload(count)
    request["object_registry_sha256"] = hashlib.sha256(
        canary._canonical_json_bytes(request["object_registry"]),  # noqa: SLF001
    ).hexdigest()

    with pytest.raises(wrapper.RuntimePreflightError, match=r"1\.\.218"):
        wrapper._validate_reciprocal_boundary(
            request,
            scene=_scene_lineage(),
            script_path=PREFLIGHT_WRAPPER,
        )


def test_preflight_wrapper_rejects_scene_build_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(PREFLIGHT_WRAPPER, monkeypatch)
    scene = _scene_lineage()
    lineage = json.loads(scene["nv_reciprocal_route_module_build"])
    lineage["build_id"] = "f" * 64
    scene["nv_reciprocal_route_module_build"] = json.dumps(
        lineage,
        separators=(",", ":"),
        sort_keys=True,
    )

    with pytest.raises(wrapper.RuntimePreflightError, match="scene build ID"):
        wrapper._validate_reciprocal_boundary(
            _boundary_payload(PREFLIGHT_WRAPPER),
            scene=scene,
            script_path=PREFLIGHT_WRAPPER,
        )


def test_preflight_wrapper_rejects_wrong_topology_proxy_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(PREFLIGHT_WRAPPER, monkeypatch)
    scene = _scene_lineage()
    lineage = json.loads(scene["nv_reciprocal_route_module_build"])
    lineage["topology_proxy_count"] = 5
    scene["nv_reciprocal_route_module_build"] = json.dumps(
        lineage,
        separators=(",", ":"),
        sort_keys=True,
    )

    with pytest.raises(wrapper.RuntimePreflightError, match="trust contract"):
        wrapper._validate_reciprocal_boundary(
            _boundary_payload(PREFLIGHT_WRAPPER),
            scene=scene,
            script_path=PREFLIGHT_WRAPPER,
        )


def test_preflight_wrapper_rejects_executing_script_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(PREFLIGHT_WRAPPER, monkeypatch)
    other_script = tmp_path / "wrapper.py"
    other_script.write_bytes(b"different wrapper")

    with pytest.raises(wrapper.RuntimePreflightError, match="script digest"):
        wrapper._validate_reciprocal_boundary(
            _boundary_payload(PREFLIGHT_WRAPPER),
            scene=_scene_lineage(),
            script_path=other_script,
        )


def test_render_wrapper_accepts_exact_218_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(RENDER_WRAPPER, monkeypatch)

    wrapper._validate_reciprocal_boundary(
        _render_boundary_payload(RENDER_WRAPPER),
        scene=_scene_lineage(),
        script_path=RENDER_WRAPPER,
    )


@pytest.mark.parametrize("count", (130, 175, 217, 219))
def test_render_wrapper_rejects_non_218_registry(
    count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(RENDER_WRAPPER, monkeypatch)
    request = _render_boundary_payload(RENDER_WRAPPER)
    request["object_registry"] = _registry_payload(count)
    request["object_registry_sha256"] = hashlib.sha256(
        canary._canonical_json_bytes(request["object_registry"]),  # noqa: SLF001
    ).hexdigest()

    with pytest.raises(wrapper.RuntimeRenderError, match=r"1\.\.218"):
        wrapper._validate_reciprocal_boundary(
            request,
            scene=_scene_lineage(),
            script_path=RENDER_WRAPPER,
        )


def test_render_wrapper_rejects_scene_plan_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(RENDER_WRAPPER, monkeypatch)
    request = _render_boundary_payload(RENDER_WRAPPER)
    request["reciprocal_route_module_plan_sha256"] = "f" * 64

    with pytest.raises(wrapper.RuntimeRenderError, match="scene plan digest"):
        wrapper._validate_reciprocal_boundary(
            request,
            scene=_scene_lineage(),
            script_path=RENDER_WRAPPER,
        )


def test_render_wrapper_rejects_wrong_topology_proxy_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(RENDER_WRAPPER, monkeypatch)
    scene = _scene_lineage()
    lineage = json.loads(scene["nv_reciprocal_route_module_build"])
    lineage["topology_proxy_count"] = 7
    scene["nv_reciprocal_route_module_build"] = json.dumps(
        lineage,
        separators=(",", ":"),
        sort_keys=True,
    )

    with pytest.raises(wrapper.RuntimeRenderError, match="trust contract"):
        wrapper._validate_reciprocal_boundary(
            _render_boundary_payload(RENDER_WRAPPER),
            scene=scene,
            script_path=RENDER_WRAPPER,
        )


def test_render_wrapper_rejects_executing_script_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapper = _load_wrapper(RENDER_WRAPPER, monkeypatch)
    other_script = tmp_path / "renderer.py"
    other_script.write_bytes(b"different renderer")

    with pytest.raises(wrapper.RuntimeRenderError, match="script digest"):
        wrapper._validate_reciprocal_boundary(
            _render_boundary_payload(RENDER_WRAPPER),
            scene=_scene_lineage(),
            script_path=other_script,
        )
