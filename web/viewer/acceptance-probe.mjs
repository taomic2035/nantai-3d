const PROBE_VERSION = 'nantai.viewer-acceptance-probe.v1';
const STATE_SCHEMA = 'nantai.viewer-acceptance-probe-state.v1';
const POSE_ID_PATTERN = /^pose-[0-9a-f]{64}$/;
const REPRESENTATIONS = new Set([
  'full-3dgs',
  'dc-point-preview',
  'mesh-preview',
  'unavailable',
]);

function finiteTime(value, label) {
  if (!Number.isFinite(value)) {
    throw new TypeError(`${label} must be finite`);
  }
  return value;
}

function positiveFrameCount(value, label) {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new TypeError(`${label} must be a positive integer`);
  }
  return value;
}

export function classifyRenderedRepresentation({
  presentationMode,
  reconstructionVisible,
  rendererState,
}) {
  if (reconstructionVisible !== true) return 'unavailable';
  if (presentationMode === 'mesh' || presentationMode === 'model') {
    return 'mesh-preview';
  }
  if (presentationMode !== 'points') return 'unavailable';
  if (rendererState?.mode === 'spark' || rendererState?.mode === 'spark-chunks') {
    return 'full-3dgs';
  }
  if (
    rendererState?.mode === 'dc-point-preview'
    || rendererState?.mode === 'dc-point-chunks'
  ) {
    return 'dc-point-preview';
  }
  return 'unavailable';
}

export function createViewerAcceptanceProbe({
  warmupFrameCount = 120,
  measuredFrameCount = 600,
} = {}) {
  positiveFrameCount(warmupFrameCount, 'warmupFrameCount');
  positiveFrameCount(measuredFrameCount, 'measuredFrameCount');

  let evidence = null;
  let startedAtMs = null;
  let previousFrameAtMs = null;
  let lastObservedAtMs = null;

  function requireActive() {
    if (evidence === null) {
      throw new Error('beginPose must be called before recording evidence');
    }
  }

  function snapshot() {
    requireActive();
    return {
      schema: STATE_SCHEMA,
      probe_version: PROBE_VERSION,
      state: evidence.state,
      pose_id: evidence.pose_id,
      requested_representation: evidence.requested_representation,
      representation: evidence.representation,
      interactive_ms: evidence.interactive_ms,
      warmup_frame_ms: [...evidence.warmup_frame_ms],
      measured_frame_ms: [...evidence.measured_frame_ms],
      timed_out: evidence.timed_out,
      sample_overflow: evidence.sample_overflow,
    };
  }

  function beginPose({
    poseId,
    requestedRepresentation,
    startedAtMs: requestedStartedAtMs,
  }) {
    if (!POSE_ID_PATTERN.test(poseId)) {
      throw new TypeError('poseId must be a content-addressed pose id');
    }
    if (requestedRepresentation !== 'full-3dgs') {
      throw new TypeError('acceptance probe requires full-3dgs');
    }
    startedAtMs = finiteTime(requestedStartedAtMs, 'startedAtMs');
    previousFrameAtMs = null;
    lastObservedAtMs = null;
    evidence = {
      state: 'waiting-for-representation',
      pose_id: poseId,
      requested_representation: requestedRepresentation,
      representation: 'unavailable',
      interactive_ms: null,
      warmup_frame_ms: [],
      measured_frame_ms: [],
      timed_out: false,
      sample_overflow: false,
    };
    return snapshot();
  }

  function recordRenderedFrame({ nowMs, representation }) {
    requireActive();
    finiteTime(nowMs, 'nowMs');
    if (!REPRESENTATIONS.has(representation)) {
      throw new TypeError('unsupported rendered representation');
    }
    if (nowMs < startedAtMs || (
      lastObservedAtMs !== null && nowMs <= lastObservedAtMs
    )) {
      throw new RangeError('render timestamps must be monotonic');
    }
    lastObservedAtMs = nowMs;

    if (
      evidence.state === 'complete'
      || evidence.state === 'timed-out'
      || evidence.state === 'representation-lost'
    ) {
      return {
        became_interactive: false,
        completed: evidence.state === 'complete',
        ...snapshot(),
      };
    }

    evidence.representation = representation;
    if (representation !== evidence.requested_representation) {
      if (evidence.interactive_ms !== null) {
        evidence.state = 'representation-lost';
      }
      return {
        became_interactive: false,
        completed: false,
        ...snapshot(),
      };
    }

    let becameInteractive = false;
    if (evidence.interactive_ms === null) {
      evidence.interactive_ms = nowMs - startedAtMs;
      evidence.state = 'warming-up';
      previousFrameAtMs = nowMs;
      becameInteractive = true;
    } else {
      const interval = nowMs - previousFrameAtMs;
      previousFrameAtMs = nowMs;
      if (evidence.warmup_frame_ms.length < warmupFrameCount) {
        evidence.warmup_frame_ms.push(interval);
        if (evidence.warmup_frame_ms.length === warmupFrameCount) {
          evidence.state = 'measuring';
        }
      } else if (evidence.measured_frame_ms.length < measuredFrameCount) {
        evidence.measured_frame_ms.push(interval);
        if (evidence.measured_frame_ms.length === measuredFrameCount) {
          evidence.state = 'complete';
        }
      }
    }

    return {
      became_interactive: becameInteractive,
      completed: evidence.state === 'complete',
      ...snapshot(),
    };
  }

  function markTimedOut({ nowMs }) {
    requireActive();
    finiteTime(nowMs, 'nowMs');
    if (nowMs < startedAtMs || (
      lastObservedAtMs !== null && nowMs < lastObservedAtMs
    )) {
      throw new RangeError('timeout timestamp must be monotonic');
    }
    if (evidence.state !== 'complete') {
      evidence.state = 'timed-out';
      evidence.timed_out = true;
    }
    return snapshot();
  }

  return Object.freeze({
    beginPose,
    markTimedOut,
    recordRenderedFrame,
    snapshot,
  });
}
