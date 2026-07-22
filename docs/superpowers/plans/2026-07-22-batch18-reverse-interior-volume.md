# Batch 18 Reverse and Interior Volume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate, inspect and privately retain eight generic reverse-view and interior-volume references that improve modeling coverage for bidirectional, arbitrary-coordinate roaming.

**Architecture:** Four built-in image-generation calls use one exact Batch 17 PNG each as a visual/layout reference for an explicitly uncalibrated reverse-side design. Four further calls create independent interior or vertical-transition scenes. Exact prompts, bindings and machine identities stay in the ignored Batch 18 candidate directory; tracked documentation records only scope, evidence and fail-closed limits.

**Tech Stack:** Built-in OpenAI image generation, PNG, PowerShell, Pillow-based inventory/contact-sheet tooling, SHA-256, Git.

---

### Task 1: Freeze prompts, source bindings and queue

**Files:**
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/prompts/*.txt`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/generation-queue-batch18.json`

- [x] **Step 1:** Create the private Batch 18 prompt directory.
- [x] **Step 2:** Save one exact structured prompt for each approved filename from the design.
- [x] **Step 3:** For jobs 01–04, record the exact Batch 17 source path, source SHA-256 and role `visual-layout-reference-only`.
- [x] **Step 4:** Record eight unique jobs as `pending` with the common fail-closed declarations from the design.
- [x] **Step 5:** Verify every prompt path exists, every output name is unique, and no output PNG exists before generation.

### Task 2: Generate the four reverse-side references

**Files:**
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/design-reverse-*.png`

- [x] **Step 1:** Invoke built-in image generation once with the Batch 17 rear-service-alley PNG as the sole reference.
- [x] **Step 2:** Invoke it once with the Batch 17 courtyard PNG as the sole reference.
- [x] **Step 3:** Invoke it once with the Batch 17 gallery-undercroft PNG as the sole reference.
- [x] **Step 4:** Invoke it once with the Batch 17 bridge-inner-bank PNG as the sole reference.
- [x] **Step 5:** Copy each selected original from `$CODEX_HOME/generated_images/` into its stable Batch 18 filename without overwriting another asset.

### Task 3: Generate the four independent interior and vertical nodes

**Files:**
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/design-interior-*.png`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/design-vertical-*.png`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/design-threshold-*.png`

- [x] **Step 1:** Invoke built-in image generation once for the dual-through workshop.
- [x] **Step 2:** Invoke it once for the watermill machinery and tailrace room.
- [x] **Step 3:** Invoke it once for the switchback stair and roof landing.
- [x] **Step 4:** Invoke it once for the three-way gatehouse threshold.
- [x] **Step 5:** Copy each selected original into its stable Batch 18 filename without resampling or recompression.

### Task 4: Inspect and inventory

**Files:**
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/candidate-sources-batch18.json`
- Create: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/contact-sheet-batch18.png`
- Modify: `.nantai-studio/synthetic-village/hybrid-v4-candidates/batch18/generation-queue-batch18.json`

- [x] **Step 1:** Record image SHA-256, bytes, width, height, mode, prompt SHA-256 and reference binding for every successful candidate.
- [x] **Step 2:** Build a private contact sheet from copied originals; do not treat it as source input.
- [x] **Step 3:** Inspect the contact sheet and every ambiguous source image for route continuity, readable underside/interior surfaces, forbidden text/watermarks and impossible geometry.
- [x] **Step 4:** Mark only generated and visually accepted jobs `complete`; a failure remains explicit and never creates an empty PNG.
- [x] **Step 5:** Verify manifest closure over exactly eight primary prompts,
  every declared correction prompt and eight accepted images before documenting
  `8/8`.

### Task 5: Document and publish tracked evidence

**Files:**
- Create: `handoff/FEEDBACK-IMAGE2-022-batch18-reverse-interior-volume.md`

- [x] **Step 1:** Record each accepted image identity, reference mode, modeling purpose and visual-review result.
- [x] **Step 2:** State that reverse-side images are generated design complements, not calibrated opposite cameras or multiview pairs.
- [x] **Step 3:** State that all candidates are synthetic, uncalibrated, non-metric and forbidden as training or coverage evidence.
- [x] **Step 4:** Verify private manifest closure and confirm Git does not stage Batch 18 candidates or `web/data/`.
- [x] **Step 5:** Path-stage only the tracked design, plan and handoff; commit with `Co-Authored-By: Codex GPT-5.6 Sol <noreply@openai.com>` and push `main`.
