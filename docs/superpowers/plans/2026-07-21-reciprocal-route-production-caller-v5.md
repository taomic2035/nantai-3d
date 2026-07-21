# Reciprocal-route Production Caller v5 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed Windows caller that preflights and renders one production camera from a verified exact-218 reciprocal-route Blender build without changing the existing v4/130-root caller.

**Architecture:** A focused host module defines additive request/report models and verifies the reciprocal-route build lineage before Blender runs. Two dedicated Blender wrappers reuse the frozen v4 ray/render engine only after independently validating v5 schemas, exact roots 1..218, reciprocal scene provenance, and their own script identities; a one-camera runner stages evidence privately and publishes it atomically after all six layer hashes and the v2 quality report verify.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, NumPy, Blender 4.5.11 headless Python, existing Nantai production profile/preflight/render/quality contracts.

---

## File map

- Create `pipeline/synthetic_village/reciprocal_route_production.py`: additive host contracts, verified-build loader, canonical serializers, report verifiers, and one-camera runner.
- Create `scripts/blender/preflight_reciprocal_route_cameras.py`: exact-218 clearance entrypoint and frozen v1 ray-engine adapter.
- Create `scripts/blender/render_reciprocal_route_production.py`: exact-218 six-layer entrypoint and frozen v4 pixel-engine adapter.
- Create `tests/test_synthetic_village_reciprocal_route_production.py`: host-contract, lineage, tamper, atomic-publication, and legacy-isolation tests.
- Create `tests/test_synthetic_village_reciprocal_route_production_blender.py`: direct fake-`bpy` wrapper tests.
- Create `handoff/FEEDBACK-HANDOFF-CODEX-009-reciprocal-route-production-caller.md`: measured real-Blender canary identities and honest remaining limits.
- Do not modify `pipeline/synthetic_village/production_preflight.py`, `pipeline/synthetic_village/production_render.py`, `scripts/blender/preflight_production_cameras.py`, or `scripts/blender/render_synthetic_village.py`; their bytes are part of prior identities.

### Task 1: Exact-218 host contracts and verified-build lineage

**Files:**
- Create: `pipeline/synthetic_village/reciprocal_route_production.py`
- Create: `tests/test_synthetic_village_reciprocal_route_production.py`
- Test: `tests/test_synthetic_village_production_preflight.py`
- Test: `tests/test_synthetic_village_production_render.py`

- [ ] **Step 1: Write failing tests for exact registry and transitive identity**

Add fixtures that construct a legal `ReciprocalRouteRuntimeRequest` and `ReciprocalRouteBuildReport`, plus these tests:

```python
@pytest.mark.parametrize("count", (130, 175, 217, 219))
def test_clearance_request_rejects_non_218_registry(count: int) -> None:
    payload = legal_clearance_payload()
    payload["object_registry"] = payload["object_registry"][:count]
    with pytest.raises(ValidationError, match="218|1\\.\\.218"):
        ReciprocalProductionClearanceRequest.model_validate(payload)


def test_frame_request_binds_transitive_build_report_sha() -> None:
    request = build_legal_frame_request()
    mutated = request.model_dump(mode="json")
    mutated["environment_module_build_report_sha256"] = "f" * 64
    with pytest.raises(ValidationError, match="render ID"):
        ReciprocalProductionRenderFrameRequest.model_validate(mutated)


def test_verified_build_rejects_changed_blend_bytes(tmp_path: Path) -> None:
    request, report, report_path, blend_path = write_verified_build(tmp_path)
    blend_path.write_bytes(blend_path.read_bytes() + b"tamper")
    with pytest.raises(ReciprocalProductionError, match="digest|artifact"):
        verify_reciprocal_production_build(
            report_path=report_path,
            runtime_request=request,
        )
```

- [ ] **Step 2: Run the focused tests and observe the missing module failure**

Run:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_production.py -q
```

Expected: collection fails with `ModuleNotFoundError: pipeline.synthetic_village.reciprocal_route_production`.

- [ ] **Step 3: Implement the host models and canonical builders**

Define the additive types and constants in the new module; keep all models frozen and `extra="forbid"` through the repository `FrozenModel` base:

```python
RECIPROCAL_CLEARANCE_REQUEST_SCHEMA = (
    "nantai.synthetic-village.reciprocal-production-clearance-request.v1"
)
RECIPROCAL_CLEARANCE_REPORT_SCHEMA = (
    "nantai.synthetic-village.reciprocal-production-clearance-report.v1"
)
RECIPROCAL_RENDER_REQUEST_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-request.v5"
)
RECIPROCAL_RENDER_REPORT_SCHEMA = (
    "nantai.synthetic-village.local-production-render-frame-report.v4"
)
RECIPROCAL_CAMERA_METADATA_SCHEMA = (
    "nantai.synthetic-village.local-production-camera-metadata.v4"
)
RECIPROCAL_BUILD_ADAPTER = "windows-reciprocal-route-v1"


class ReciprocalProductionClearanceRequest(FrozenModel):
    schema_version: Literal[
        "nantai.synthetic-village.reciprocal-production-clearance-request.v1"
    ] = RECIPROCAL_CLEARANCE_REQUEST_SCHEMA
    production_plan: ProductionCameraPlan
    production_plan_sha256: Sha256
    camera_registry_sha256: Sha256
    selected_camera_ids: tuple[str, ...] = Field(min_length=1, max_length=180)
    build_id: Sha256
    blender_executable_sha256: Sha256
    preflight_script_sha256: Sha256
    blend_sha256: Sha256
    build_report_sha256: Sha256
    environment_module_build_report_sha256: Sha256
    reciprocal_route_module_plan_sha256: Sha256
    object_registry_sha256: Sha256
    object_registry: tuple[canary.ObjectRegistryEntry, ...] = Field(
        min_length=218,
        max_length=218,
    )
    auxiliary_registry: tuple[canary.AuxiliaryRegistryEntry, ...]
    semantic_registry: tuple[canary.SemanticRegistryEntry, ...]
    policy: ProductionClearancePolicy
    policy_sha256: Sha256
    preflight_id: Sha256
    synthetic: Literal[True] = True
    geometry_trust: Literal["simplified-pbr-not-render-parity"] = (
        "simplified-pbr-not-render-parity"
    )
    trust_effect: Literal["none-quality-filter-only"] = (
        "none-quality-filter-only"
    )
```

Add `ReciprocalProductionClearanceReport`, `ReciprocalProductionRenderFrameRequest`, `ReciprocalProductionRenderFrameReport`, and `ReciprocalProductionCameraMetadata` with the fields of their frozen predecessor plus the two lineage SHA values where the request identity needs them. The v5 frame validator computes:

```python
expected_render_id = production_render_id(
    self.production_plan,
    blender_executable_sha256=self.blender_executable_sha256,
    renderer_script_sha256=self.renderer_script_sha256,
    blend_sha256=self.blend_sha256,
    build_report_sha256=self.build_report_sha256,
    camera_registry_sha256=self.camera_registry_sha256,
    preflight_id=self.preflight_id,
    quality_policy_sha256=self.quality_policy_sha256,
    post_render_policy_sha256=self.post_render_policy_sha256,
    build_adapter=RECIPROCAL_BUILD_ADAPTER,
    environment_module_build_report_sha256=(
        self.environment_module_build_report_sha256
    ),
)
```

Every request validator must require `tuple(row.instance_id for row in object_registry) == tuple(range(1, 219))`, recompute the registry SHA, verify plan/camera/policy hashes, and reject an adapter other than `windows-reciprocal-route-v1`.

Implement `VerifiedReciprocalProductionBuild` and `verify_reciprocal_production_build`. The verifier loads canonical report bytes with `load_reciprocal_route_build_report`, calls `verify_reciprocal_route_build_report`, hashes the report file itself, and returns only measured values:

```python
@dataclass(frozen=True)
class VerifiedReciprocalProductionBuild:
    build_id: str
    report_path: Path
    report_sha256: str
    blend_path: Path
    blend_sha256: str
    environment_module_build_report_sha256: str
    reciprocal_route_module_plan_sha256: str
    object_registry: tuple[canary.ObjectRegistryEntry, ...]


def verify_reciprocal_production_build(
    *,
    report_path: Path,
    runtime_request: ReciprocalRouteRuntimeRequest,
) -> VerifiedReciprocalProductionBuild:
    report = load_reciprocal_route_build_report(report_path)
    blend_path = report_path.parent / report.artifact.name
    verify_reciprocal_route_build_report(
        report,
        request=runtime_request,
        output_path=blend_path,
    )
    return VerifiedReciprocalProductionBuild(
        build_id=report.build_id,
        report_path=report_path.resolve(strict=True),
        report_sha256=_sha256_file(report_path),
        blend_path=blend_path.resolve(strict=True),
        blend_sha256=report.artifact.sha256,
        environment_module_build_report_sha256=(
            report.base_build_report_sha256
        ),
        reciprocal_route_module_plan_sha256=(
            report.reciprocal_route_module_plan_sha256
        ),
        object_registry=report.object_registry,
    )
```

- [ ] **Step 4: Run new tests and legacy isolation tests**

Run:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_production.py tests/test_synthetic_village_production_preflight.py tests/test_synthetic_village_production_render.py -q
```

Expected: all selected tests pass; legacy tests continue to reject non-130 v4 registries.

- [ ] **Step 5: Commit the host contract slice**

```powershell
git add -- pipeline/synthetic_village/reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production.py
git commit -m "feat(reciprocal-route): add exact-218 production contracts" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/synthetic_village/reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production.py
```

### Task 2: Fresh Blender clearance wrapper

**Files:**
- Create: `scripts/blender/preflight_reciprocal_route_cameras.py`
- Create: `tests/test_synthetic_village_reciprocal_route_production_blender.py`

- [ ] **Step 1: Write direct wrapper tests with fake `bpy`**

Load the script through `importlib.util.spec_from_file_location` after injecting a minimal fake `bpy` module. Assert `_validate_reciprocal_request` accepts an exact legal 218 registry and rejects each of these independently: 217 roots, duplicate ID 218, mismatched wrapper SHA, mismatched scene build ID, mismatched reciprocal plan SHA, and a non-canonical request.

```python
def test_preflight_wrapper_rejects_scene_build_mismatch(
    fake_bpy: ModuleType,
) -> None:
    module = load_blender_wrapper(
        "scripts/blender/preflight_reciprocal_route_cameras.py",
        fake_bpy=fake_bpy,
    )
    request = legal_clearance_payload()
    fake_bpy.context.scene["nv_reciprocal_route_module_build"] = json.dumps(
        {"build_id": "f" * 64, "reciprocal_route_module_plan_sha256": "a" * 64}
    )
    with pytest.raises(RuntimeError, match="scene build ID"):
        module._validate_reciprocal_request(request, request_path=REQUEST_PATH)
```

- [ ] **Step 2: Run the wrapper tests and observe the missing script failure**

Run:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_production_blender.py -q
```

Expected: tests fail because the wrapper does not exist.

- [ ] **Step 3: Implement the exact-218 preflight wrapper**

The wrapper must hash its own file, reject duplicate JSON keys, require the exact schema keys and exact IDs 1..218, parse the reciprocal scene property, and construct a private v1-engine payload only for the frozen ray implementation:

```python
def _legacy_engine_payload(request: dict[str, object]) -> dict[str, object]:
    payload = copy.deepcopy(request)
    payload.pop("environment_module_build_report_sha256")
    payload.pop("reciprocal_route_module_plan_sha256")
    return payload
```

Load `preflight_production_cameras.py` dynamically, patch only the loaded module object's request/report schema literals and object-registry validation function, and pass all 218 rows to its unchanged ray-casting implementation. The emitted reciprocal report is rebuilt from the original request and measured evidence. The report writer uses `nantai.synthetic-village.reciprocal-production-clearance-report.v1`; no root projection or truncation is permitted.

- [ ] **Step 4: Run direct wrapper and legacy preflight tests**

Run:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_production_blender.py tests/test_synthetic_village_production_preflight.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the clearance wrapper**

```powershell
git add -- scripts/blender/preflight_reciprocal_route_cameras.py tests/test_synthetic_village_reciprocal_route_production_blender.py
git commit -m "feat(reciprocal-route): add exact-218 Blender preflight" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- scripts/blender/preflight_reciprocal_route_cameras.py tests/test_synthetic_village_reciprocal_route_production_blender.py
```

### Task 3: Six-layer Blender render wrapper

**Files:**
- Create: `scripts/blender/render_reciprocal_route_production.py`
- Modify: `tests/test_synthetic_village_reciprocal_route_production_blender.py`

- [ ] **Step 1: Write failing render-wrapper identity tests**

Add tests that require exact keys, wrapper SHA, exact 218 roots, reciprocal scene build ID and plan SHA. Add a test proving IDs 176 and 218 survive into the engine's instance/semantic lookup instead of being silently discarded.

```python
def test_render_wrapper_keeps_reciprocal_instance_ids(wrapper: ModuleType) -> None:
    request = legal_frame_payload()
    adapted = wrapper._validated_engine_request(request, request_path=REQUEST_PATH)
    assert adapted["object_registry"][175]["instance_id"] == 176
    assert adapted["object_registry"][217]["instance_id"] == 218
```

- [ ] **Step 2: Run the render-wrapper tests and observe failure**

Run:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_production_blender.py -q
```

Expected: the new test fails because the render wrapper is absent.

- [ ] **Step 3: Implement the render wrapper without changing frozen v4 bytes**

Load `render_synthetic_village.py` dynamically, patch only the loaded module object's schema literals and object-registry validation function, and call its existing layer renderer. Independently validate the original request before any patching:

```python
def _validate_registry(rows: list[dict[str, object]]) -> None:
    ids = tuple(row.get("instance_id") for row in rows)
    if len(rows) != 218 or ids != tuple(range(1, 219)):
        raise RuntimeError("object registry is not exact 1..218")


def _load_engine() -> ModuleType:
    engine = _load_python_module(LEGACY_RENDER_SCRIPT)
    engine.LOCAL_PRODUCTION_RENDER_REQUEST_SCHEMA = (
        "nantai.synthetic-village.local-production-render-frame-request.v5"
    )
    engine.LOCAL_PRODUCTION_RENDER_REPORT_SCHEMA = (
        "nantai.synthetic-village.local-production-render-frame-report.v4"
    )
    engine.LOCAL_PRODUCTION_CAMERA_METADATA_SCHEMA = (
        "nantai.synthetic-village.local-production-camera-metadata.v4"
    )
    engine._validate_object_registry_contract = _validate_registry
    return engine
```

The wrapper validates its own SHA, then passes the original 218-row request to the patched engine. It must not replace `renderer_script_sha256` with the legacy script SHA, must not rewrite `build_id`, and must verify the emitted report/artifact list before exiting zero.

- [ ] **Step 4: Run wrapper and v4 renderer regressions**

Run:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_production_blender.py tests/test_synthetic_village_production_render.py -q
```

Expected: all selected tests pass and the frozen v4 renderer SHA remains unchanged in the legacy fixture.

- [ ] **Step 5: Commit the render wrapper**

```powershell
git add -- scripts/blender/render_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production_blender.py
git commit -m "feat(reciprocal-route): add exact-218 Blender renderer" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- scripts/blender/render_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production_blender.py
```

### Task 4: One-camera atomic runner and v2 quality evidence

**Files:**
- Modify: `pipeline/synthetic_village/reciprocal_route_production.py`
- Modify: `tests/test_synthetic_village_reciprocal_route_production.py`

- [ ] **Step 1: Write failing runner publication tests**

Use a fake subprocess that writes canonical preflight, frame, camera metadata and six artifacts. Assert successful publication contains only immutable request/report/evidence files; then parameterize failures for nonzero Blender exit, timeout, changed input SHA, absent artifact, mismatched artifact SHA, rejected preflight, and failed quality decision. Every failure must leave the final content-addressed directory absent.

```python
def test_runner_does_not_publish_failed_quality(
    tmp_path: Path,
    fake_blender: FakeBlender,
) -> None:
    fake_blender.quality_passes = False
    with pytest.raises(ReciprocalProductionError, match="quality"):
        run_reciprocal_production_camera(
            verified_build=fake_blender.verified_build,
            runtime_request=fake_blender.runtime_request,
            camera_id="camera-ground-route-011",
            output_root=tmp_path / "renders",
            blender_executable=fake_blender.executable,
        )
    assert not any((tmp_path / "renders").glob("[0-9a-f]*"))
```

- [ ] **Step 2: Run the focused runner tests and observe failure**

Run:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_production.py -q
```

Expected: failures report missing `run_reciprocal_production_camera`.

- [ ] **Step 3: Implement private staging, verification and atomic publication**

Implement a result dataclass and runner with dependency-injected `subprocess.run`. The runner re-hashes the Blender executable, wrapper scripts, report, and blend immediately before both subprocesses; writes requests with exclusive-create semantics; parses canonical reports; verifies the selected camera; verifies all six file sizes and SHA values; builds `ProductionFrameQualityRequestV2` and `ProductionFrameQualityReportV2`; then atomically renames staging to `<output_root>/<render_id>/<camera_id>`.

```python
@dataclass(frozen=True)
class ReciprocalProductionCameraResult:
    render_id: str
    camera_id: str
    frame_root: Path
    preflight_request_sha256: str
    preflight_report_sha256: str
    render_request_sha256: str
    render_report_sha256: str
    quality_request_sha256: str
    quality_report_sha256: str


def _publish_directory(staging: Path, final: Path) -> None:
    if final.exists() or canary._is_linklike(final):
        raise ReciprocalProductionError("final frame directory already exists")
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging, final)
    canary._flush_directory(final.parent)
```

Cleanup resolves both paths and proves staging is under the configured private staging root before `shutil.rmtree`. It never removes the final directory and never overwrites accepted evidence.

- [ ] **Step 4: Run runner, quality and legacy tests**

Run:

```powershell
python -m pytest tests/test_synthetic_village_reciprocal_route_production.py tests/test_synthetic_village_production_quality_gates.py tests/test_synthetic_village_local_production_runner.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the runner slice**

```powershell
git add -- pipeline/synthetic_village/reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production.py
git commit -m "feat(reciprocal-route): run one production camera" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- pipeline/synthetic_village/reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production.py
```

### Task 5: Fresh verification, real Blender canary and handoff

**Files:**
- Create: `handoff/FEEDBACK-HANDOFF-CODEX-009-reciprocal-route-production-caller.md`

- [ ] **Step 1: Run static and complete adjacent tests**

Run:

```powershell
python -m ruff check pipeline/synthetic_village/reciprocal_route_production.py scripts/blender/preflight_reciprocal_route_cameras.py scripts/blender/render_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production_blender.py
python -m pytest tests/test_synthetic_village_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production_blender.py tests/test_synthetic_village_production_preflight.py tests/test_synthetic_village_production_render.py tests/test_synthetic_village_production_quality_gates.py tests/test_synthetic_village_local_production_runner.py tests/test_synthetic_village_reciprocal_route_module_runtime.py -q
git diff --check
```

Expected: ruff exits 0, pytest reports zero failed/errors, and `git diff --check` emits no output.

- [ ] **Step 2: Build a fresh Phase 4.1 scene and run one real camera**

Use the repository's verified Blender executable and the reciprocal runtime request to produce a fresh build; then call the one-camera runner for `camera-ground-route-011`, the known non-near-surface plumbing canary. Do not substitute `camera-ground-route-010`, which current measured evidence rejects.

```powershell
third\blender\blender.exe --background --factory-startup --python scripts\blender\apply_reciprocal_route_modules.py -- --request <canonical-runtime-request.json> --report <fresh-build-report.json> --output <fresh-build\village-reciprocal-route.blend>
python -m pipeline.synthetic_village.reciprocal_route_production --build-report <fresh-build-report.json> --runtime-request <canonical-runtime-request.json> --camera-id camera-ground-route-011 --output-root .nantai-studio\sv-prod-win-reciprocal-v1
```

Expected: Blender exits 0 for build, preflight and render; the final directory contains six verified artifacts plus canonical request/report/quality sidecars. A render can still be quality-rejected; if so, record the exact decision and do not publish it as accepted.

- [ ] **Step 3: Record measured identities and honest limits**

The handoff must include exact build ID, final report SHA, transitive report SHA, blend SHA, preflight ID, render ID, request/report SHA values, each of the six artifact SHA values, quality decision, commands, timings, and these explicit limits:

```text
This one-camera L0 synthetic render proves caller plumbing only.
It does not prove route topology, real photographic texture, 180-camera coverage,
metric reconstruction, or 360-degree arbitrary-coordinate visual completeness.
```

- [ ] **Step 4: Re-run fresh verification after documenting evidence**

Run:

```powershell
python -m ruff check pipeline/synthetic_village/reciprocal_route_production.py scripts/blender/preflight_reciprocal_route_cameras.py scripts/blender/render_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production_blender.py
python -m pytest tests/test_synthetic_village_reciprocal_route_production.py tests/test_synthetic_village_reciprocal_route_production_blender.py tests/test_synthetic_village_production_preflight.py tests/test_synthetic_village_production_render.py tests/test_synthetic_village_production_quality_gates.py tests/test_synthetic_village_local_production_runner.py tests/test_synthetic_village_reciprocal_route_module_runtime.py -q
git diff --check
```

Expected: all commands exit 0 and pytest has zero failures/errors.

- [ ] **Step 5: Commit and push the measured handoff**

```powershell
git add -- handoff/FEEDBACK-HANDOFF-CODEX-009-reciprocal-route-production-caller.md
git commit -m "docs(handoff): record reciprocal production canary" -m "Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>" -- handoff/FEEDBACK-HANDOFF-CODEX-009-reciprocal-route-production-caller.md
git push origin main
```

## Follow-up boundary

After this plan passes, write a separate plan for resumable 180-camera journal execution and Studio jobs/ledger/HUD projection. That follow-up consumes these v5 identities and does not create a competing quality-decision path.
