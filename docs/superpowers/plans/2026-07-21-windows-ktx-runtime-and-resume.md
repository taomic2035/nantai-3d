# Windows KTX Runtime and Resumable H3 Compilation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed Windows x64 KTX 4.4.2 receipt and resumable content-addressed compilation so the accepted H3 authored materials can become a verified 24-texture KTX2 pack on the current machine.

**Architecture:** Preserve the existing Darwin-arm64 receipt and package gate unchanged. Add a separate Windows receipt schema with exact official package, Authenticode, binary, version, and path evidence, then let the shared compiler consume either verified receipt. Move expensive per-texture results into a private content-addressed cache whose entries are fully revalidated before reuse; final pack publication remains an exact atomic closed directory.

**Tech Stack:** Python 3.11/3.13, Pydantic v2, Khronos KTX-Software 4.4.2, PowerShell Authenticode, pytest, canonical JSON, SHA-256.

---

## File map

- Modify `pipeline/synthetic_village/ktx2_toolchain.py`: additive Windows receipt, platform-aware runtime environment, verified cache reuse.
- Modify `scripts/setup_synthetic_tools.py`: select the current supported KTX package and adopt a private Windows NSIS installation without removing the Mac path.
- Modify `tests/test_ktx2_toolchain.py`: receipt, tamper, environment, cache, and legacy Mac stability tests.
- Modify `tests/test_synthetic_village_tool_lock.py`: Windows setup-command lock.
- Create `docs/verification/2026-07-21-windows-h3-ktx2-runtime.md`: real package, signature, timing, repeat-byte, and final-pack evidence.

### Task 1: Freeze Windows package and receipt contract

- [ ] Add failing tests for exact package asset `KTX-Software-4.4.2-Windows-x64.exe`, URL, SHA `1f323b0fec19794f5e6c0425a61d4b1da396872a10be862d105f4f4b2d2957fe`, platform `windows-x64`, runtime paths `bin/toktx.exe`, `bin/ktx.exe`, `bin/ktx.dll`, and license path `share/doc/KTX-Software/html/license.html`.
- [ ] Run `python -m pytest tests/test_ktx2_toolchain.py -k "windows and receipt" -q` and confirm RED because the Windows receipt does not exist.
- [ ] Add a separate `WindowsKtxToolReceipt` schema and `WindowsKtxToolBinary` Authenticode evidence while leaving canonical Darwin `KtxToolReceipt` bytes unchanged.
- [ ] Make `load_ktx_tool_receipt()` discriminate on `platform` and reject unknown platforms, mixed Mac/Windows paths, wrong signer, wrong certificate thumbprint, wrong package digest, and non-canonical JSON.
- [ ] Re-run the focused tests and the existing Mac receipt tests; both must pass.

### Task 2: Adopt and verify the private Windows runtime

- [ ] Add failing tests for an injected PowerShell signature runner that returns installer/binary status, full signer subject, and thumbprint; include invalid status, signer mismatch, missing DLL, redirected path, version mismatch, and changed-during-read cases.
- [ ] Run the focused tests and confirm the intended verification failure.
- [ ] Implement `prepare_private_windows_ktx_runtime(package, installed_root, *, signature_runner=...)` so it copies the pinned installer into `downloads/`, measures every required file, probes both versions, writes canonical receipt bytes atomically, and re-verifies an existing receipt idempotently.
- [ ] Keep `prepare_private_ktx_runtime()` Darwin behavior unchanged; dispatch by measured host platform rather than by caller-supplied trust labels.
- [ ] Extend `scripts/setup_synthetic_tools.py` with the same `--install-ktx-4.4.2` UX while selecting the official package for the measured host and never deleting the other platform gate.

### Task 3: Make compilation platform-aware and resumable

- [ ] Add failing tests proving Windows environment retains required `SystemRoot`, `TEMP`, and tool `bin` PATH while Darwin keeps its exact restricted PATH and `DYLD_LIBRARY_PATH`.
- [ ] Add failing tests proving one verified cached descriptor prevents re-encoding, while any source/tool/options/object/quality mismatch fails closed or recompiles into a new identity without re-registering stale bytes.
- [ ] Implement `_runtime_environment(root, receipt)` and pass the verified receipt to all compile/validate/extract subprocess construction.
- [ ] Introduce a private cache keyed by source SHA, role, frozen command options, package SHA, `toktx` SHA, and `ktx` SHA. Cache records contain the full `KtxTextureDescriptor`; reuse re-runs structural audit, official validation, decoded quality comparison, byte length, and SHA checks.
- [ ] Build final pack staging by copying/hard-linking only verified cached objects into `pack/objects`; manifest closure and atomic rename remain unchanged, and cache files never enter the final pack or Release.
- [ ] Run `python -m pytest tests/test_ktx2_toolchain.py tests/test_material_bundle_v2.py tests/test_mesh_asset_bundle_v3.py -q`.

### Task 4: Real Windows H3 pack and downstream verification

- [ ] Generate the canonical Windows receipt from the installed private runtime and verify installer plus `toktx.exe`, `ktx.exe`, and `ktx.dll` Authenticode status against the frozen Khronos signer and certificate thumbprint.
- [ ] Compile the accepted authored pack `b27eb142bc23c79c5cdd52bc8215604634f115ab12c2f9240fb98ecfc9af1789` with the resumable runner. Preserve completed texture objects across process interruption.
- [ ] Require 8 ordered slots, 24 KTX2 objects, exact 4096→1 mip chains, valid glTF BasisU metadata, decoded quality thresholds, and repeat-byte equality on this Windows machine.
- [ ] Compose/publish MaterialBundle v2 and MeshAssetBundle v3 privately, then run Blender H2/H3 contact render and Viewer H3→H2 rollback tests before changing a default profile.
- [ ] Record package/signature/file SHAs, exact commands, per-role timings, cache reuse evidence, pack IDs, render screenshots, Viewer result, and remaining truth limits in `docs/verification/2026-07-21-windows-h3-ktx2-runtime.md`.
- [ ] Run `git diff --check`, path-limit staging, commit with `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>`, and push `main` only after all gates are green.

## Fixed truth boundary

Successful H3 KTX2 publication improves synthetic material delivery only. It retains `synthetic=true`, `ai_generated=true`, `real_photo_textures=false`, `geometry_usability=preview-only`, `metric_alignment=false`, and `verification_level=L0`; it does not prove real geometry, camera coverage, SfM/3DGS training suitability, or arbitrary-coordinate visual completeness.
