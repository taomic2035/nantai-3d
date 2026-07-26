import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

let probeModule;
try {
  probeModule = await import('./acceptance-probe.mjs');
} catch (error) {
  probeModule = { __loadError: error };
}

const POSE_ID = `pose-${'a'.repeat(64)}`;
const mainSource = readFileSync(new URL('./main.js', import.meta.url), 'utf8');

function subject() {
  assert.equal(
    probeModule.__loadError,
    undefined,
    `acceptance-probe.mjs must load: ${probeModule.__loadError?.message}`,
  );
  return probeModule;
}

test('classification never promotes a point fallback to full 3DGS', () => {
  const { classifyRenderedRepresentation } = subject();

  assert.equal(
    classifyRenderedRepresentation({
      presentationMode: 'points',
      reconstructionVisible: true,
      rendererState: { mode: 'spark-chunks' },
    }),
    'full-3dgs',
  );
  assert.equal(
    classifyRenderedRepresentation({
      presentationMode: 'points',
      reconstructionVisible: true,
      rendererState: { mode: 'spark' },
    }),
    'full-3dgs',
  );
  for (const rendererState of [
    { mode: 'dc-point-chunks' },
    { mode: 'dc-point-preview' },
    null,
  ]) {
    assert.equal(
      classifyRenderedRepresentation({
        presentationMode: 'points',
        reconstructionVisible: true,
        rendererState,
      }),
      rendererState ? 'dc-point-preview' : 'unavailable',
    );
  }
  assert.equal(
    classifyRenderedRepresentation({
      presentationMode: 'mesh',
      reconstructionVisible: true,
      rendererState: { mode: 'spark' },
    }),
    'mesh-preview',
  );
  assert.equal(
    classifyRenderedRepresentation({
      presentationMode: 'points',
      reconstructionVisible: false,
      rendererState: { mode: 'spark' },
    }),
    'unavailable',
  );
});

test('probe starts only after a completed full-3dgs render', () => {
  const { createViewerAcceptanceProbe } = subject();
  const probe = createViewerAcceptanceProbe({
    warmupFrameCount: 2,
    measuredFrameCount: 3,
  });

  probe.beginPose({
    poseId: POSE_ID,
    requestedRepresentation: 'full-3dgs',
    startedAtMs: 100,
  });
  probe.recordRenderedFrame({
    nowMs: 125,
    representation: 'dc-point-preview',
  });
  assert.deepEqual(probe.snapshot(), {
    schema: 'nantai.viewer-acceptance-probe-state.v1',
    probe_version: 'nantai.viewer-acceptance-probe.v1',
    state: 'waiting-for-representation',
    pose_id: POSE_ID,
    requested_representation: 'full-3dgs',
    representation: 'dc-point-preview',
    interactive_ms: null,
    warmup_frame_ms: [],
    measured_frame_ms: [],
    timed_out: false,
    sample_overflow: false,
  });

  const firstFullFrame = probe.recordRenderedFrame({
    nowMs: 140,
    representation: 'full-3dgs',
  });
  assert.equal(firstFullFrame.became_interactive, true);
  assert.equal(firstFullFrame.interactive_ms, 40);
  assert.equal(probe.snapshot().state, 'warming-up');

  probe.recordRenderedFrame({ nowMs: 150, representation: 'full-3dgs' });
  probe.recordRenderedFrame({ nowMs: 162, representation: 'full-3dgs' });
  probe.recordRenderedFrame({ nowMs: 177, representation: 'full-3dgs' });
  probe.recordRenderedFrame({ nowMs: 195, representation: 'full-3dgs' });
  const completed = probe.recordRenderedFrame({
    nowMs: 216,
    representation: 'full-3dgs',
  });

  assert.equal(completed.completed, true);
  assert.deepEqual(probe.snapshot(), {
    schema: 'nantai.viewer-acceptance-probe-state.v1',
    probe_version: 'nantai.viewer-acceptance-probe.v1',
    state: 'complete',
    pose_id: POSE_ID,
    requested_representation: 'full-3dgs',
    representation: 'full-3dgs',
    interactive_ms: 40,
    warmup_frame_ms: [10, 12],
    measured_frame_ms: [15, 18, 21],
    timed_out: false,
    sample_overflow: false,
  });
});

test('completed evidence is bounded and returned as defensive copies', () => {
  const { createViewerAcceptanceProbe } = subject();
  const probe = createViewerAcceptanceProbe({
    warmupFrameCount: 1,
    measuredFrameCount: 1,
  });
  probe.beginPose({
    poseId: POSE_ID,
    requestedRepresentation: 'full-3dgs',
    startedAtMs: 0,
  });
  for (const nowMs of [10, 20, 30, 40, 50]) {
    probe.recordRenderedFrame({ nowMs, representation: 'full-3dgs' });
  }

  const first = probe.snapshot();
  assert.deepEqual(first.warmup_frame_ms, [10]);
  assert.deepEqual(first.measured_frame_ms, [10]);
  assert.equal(first.sample_overflow, false);

  first.warmup_frame_ms.push(999);
  first.measured_frame_ms[0] = 999;
  assert.deepEqual(probe.snapshot().warmup_frame_ms, [10]);
  assert.deepEqual(probe.snapshot().measured_frame_ms, [10]);
});

test('timeout freezes incomplete evidence without inventing representation', () => {
  const { createViewerAcceptanceProbe } = subject();
  const probe = createViewerAcceptanceProbe();
  probe.beginPose({
    poseId: POSE_ID,
    requestedRepresentation: 'full-3dgs',
    startedAtMs: 100,
  });
  probe.markTimedOut({ nowMs: 15_101 });

  const snapshot = probe.snapshot();
  assert.equal(snapshot.state, 'timed-out');
  assert.equal(snapshot.timed_out, true);
  assert.equal(snapshot.representation, 'unavailable');
  assert.equal(snapshot.interactive_ms, null);
});

test('representation loss after first full frame is terminal and fail-closed', () => {
  const { createViewerAcceptanceProbe } = subject();
  const probe = createViewerAcceptanceProbe({
    warmupFrameCount: 1,
    measuredFrameCount: 1,
  });
  probe.beginPose({
    poseId: POSE_ID,
    requestedRepresentation: 'full-3dgs',
    startedAtMs: 0,
  });
  probe.recordRenderedFrame({ nowMs: 10, representation: 'full-3dgs' });
  probe.recordRenderedFrame({
    nowMs: 20,
    representation: 'dc-point-preview',
  });
  probe.recordRenderedFrame({ nowMs: 30, representation: 'full-3dgs' });

  const snapshot = probe.snapshot();
  assert.equal(snapshot.state, 'representation-lost');
  assert.equal(snapshot.representation, 'dc-point-preview');
  assert.deepEqual(snapshot.warmup_frame_ms, []);
  assert.deepEqual(snapshot.measured_frame_ms, []);
});

test('pose reset discards prior samples and validates all public inputs', () => {
  const { createViewerAcceptanceProbe } = subject();
  const probe = createViewerAcceptanceProbe({
    warmupFrameCount: 1,
    measuredFrameCount: 1,
  });

  assert.throws(
    () => probe.beginPose({
      poseId: 'camera-one',
      requestedRepresentation: 'full-3dgs',
      startedAtMs: 0,
    }),
    /content-addressed/,
  );
  assert.throws(
    () => probe.beginPose({
      poseId: POSE_ID,
      requestedRepresentation: 'mesh-preview',
      startedAtMs: 0,
    }),
    /full-3dgs/,
  );
  assert.throws(
    () => probe.recordRenderedFrame({
      nowMs: 0,
      representation: 'full-3dgs',
    }),
    /beginPose/,
  );

  probe.beginPose({
    poseId: POSE_ID,
    requestedRepresentation: 'full-3dgs',
    startedAtMs: 0,
  });
  assert.throws(
    () => probe.recordRenderedFrame({
      nowMs: Number.NaN,
      representation: 'full-3dgs',
    }),
    /finite/,
  );
  assert.throws(
    () => probe.recordRenderedFrame({
      nowMs: 1,
      representation: 'invented',
    }),
    /representation/,
  );
  probe.recordRenderedFrame({ nowMs: 10, representation: 'full-3dgs' });
  assert.throws(
    () => probe.recordRenderedFrame({
      nowMs: 9,
      representation: 'full-3dgs',
    }),
    /monotonic/,
  );

  const nextPoseId = `pose-${'b'.repeat(64)}`;
  probe.beginPose({
    poseId: nextPoseId,
    requestedRepresentation: 'full-3dgs',
    startedAtMs: 50,
  });
  assert.deepEqual(probe.snapshot().warmup_frame_ms, []);
  assert.equal(probe.snapshot().pose_id, nextPoseId);
});

test('viewer exposes measurement only and records after the real render call', () => {
  assert.match(
    mainSource,
    /from ['"]\.\/acceptance-probe\.mjs['"]/,
  );
  assert.match(
    mainSource,
    /window\.__NANTAI_VIEWER_ACCEPTANCE__\s*=\s*Object\.freeze\(\{/,
  );
  assert.match(mainSource, /beginPose:\s*\([^)]*\)\s*=>/);
  assert.match(mainSource, /markTimedOut:\s*\([^)]*\)\s*=>/);
  assert.match(mainSource, /snapshot:\s*\(\)\s*=>/);
  assert.doesNotMatch(
    mainSource,
    /__NANTAI_VIEWER_ACCEPTANCE__[\s\S]{0,600}(setCamera|loadArtifact|setLayer)/,
  );

  const renderOffset = mainSource.indexOf('renderer.render(scene, camera);');
  const measurementOffset = mainSource.indexOf(
    'viewerAcceptanceProbe.recordRenderedFrame',
    renderOffset,
  );
  assert.ok(renderOffset >= 0, 'viewer must render the scene');
  assert.ok(
    measurementOffset > renderOffset,
    'acceptance evidence must be recorded after renderer.render returns',
  );
  assert.match(
    mainSource.slice(renderOffset, measurementOffset + 600),
    /classifyRenderedRepresentation\(\{[\s\S]*presentationMode,[\s\S]*reconstructionVisible:\s*reconVisible,[\s\S]*rendererState:\s*activeReconstructionState\(\)/,
  );
});
