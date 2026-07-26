import assert from 'node:assert/strict';
import test from 'node:test';

let captureModule;
try {
  captureModule = await import('./capture_viewer_acceptance.mjs');
} catch (error) {
  captureModule = { __loadError: error };
}

function subject() {
  assert.equal(
    captureModule.__loadError,
    undefined,
    `capture_viewer_acceptance.mjs must load: ${
      captureModule.__loadError?.message
    }`,
  );
  return captureModule;
}

function pose(index) {
  return {
    schema: 'nantai.viewer-camera-pose.v1',
    position: {
      east: index * 5 + 1,
      north: index * -3 - 2,
      up: index + 1.5,
    },
    look_at: {
      east: index,
      north: index + 10,
      up: 1,
    },
  };
}

function cameraSet() {
  const { poseIdFor } = subject();
  const poses = [0, 1, 2].map((index) => {
    const payload = pose(index);
    return { pose_id: poseIdFor(payload), ...payload };
  });
  return {
    schema: 'nantai.viewer-camera-set.v1',
    poses,
  };
}

function policy(cameraSetValue = cameraSet()) {
  return {
    schema: 'nantai.viewer-performance-policy.v1',
    required_pose_ids: cameraSetValue.poses.map((item) => item.pose_id),
    viewport_width: 1280,
    viewport_height: 720,
    warmup_frame_count: 120,
    measured_frame_count: 600,
    maximum_interactive_ms: 15_000,
    maximum_p50_frame_ms: 33.34,
    maximum_p95_frame_ms: 50,
    maximum_worst_frame_ms: 250,
    percentile_method: 'nearest-rank',
    required_representation: 'full-3dgs',
    required_http_cache: 'empty',
    allow_software_renderer: false,
  };
}

test('camera pose ids bind exact canonical ENU payloads', () => {
  const { canonicalJson, poseIdFor } = subject();
  const first = pose(0);
  const reordered = {
    look_at: {
      up: 1,
      north: 10,
      east: 0,
    },
    position: {
      up: 1.5,
      north: -2,
      east: 1,
    },
    schema: 'nantai.viewer-camera-pose.v1',
  };

  assert.equal(canonicalJson(first), canonicalJson(reordered));
  assert.equal(poseIdFor(first), poseIdFor(reordered));
  assert.match(poseIdFor(first), /^pose-[0-9a-f]{64}$/);
  assert.notEqual(
    poseIdFor(first),
    poseIdFor({
      ...first,
      position: { ...first.position, east: 1.001 },
    }),
  );
});

test('capture contract accepts exactly three bound poses on local Studio', () => {
  const { validateCaptureContract } = subject();
  const cameras = cameraSet();

  const contract = validateCaptureContract({
    policy: policy(cameras),
    cameraSet: cameras,
    studioUrl: 'http://127.0.0.1:8767/web/studio/',
    sourceRole: 'production-acceptance',
  });

  assert.equal(contract.studioUrl.href, 'http://127.0.0.1:8767/web/studio/');
  assert.equal(contract.cameraSet.poses.length, 3);
  assert.equal(contract.policy.warmup_frame_count, 120);
  assert.equal(contract.sourceRole, 'production-acceptance');
});

test('capture contract rejects remote, direct-viewer, and credentialed URLs', () => {
  const { validateCaptureContract } = subject();
  const cameras = cameraSet();
  const base = {
    policy: policy(cameras),
    cameraSet: cameras,
    sourceRole: 'internal-canary',
  };
  for (const studioUrl of [
    'https://example.com/web/studio/',
    'http://127.0.0.1:8000/web/viewer/',
    'http://user:secret@localhost:8000/web/studio/',
    'file:///tmp/web/studio/',
  ]) {
    assert.throws(
      () => validateCaptureContract({ ...base, studioUrl }),
      /local Studio URL/,
    );
  }
});

test('capture contract rejects pose tamper, order drift, and count drift', () => {
  const { validateCaptureContract } = subject();
  const cameras = cameraSet();
  const base = {
    policy: policy(cameras),
    studioUrl: 'http://localhost:8000/web/studio/',
    sourceRole: 'internal-canary',
  };
  const tampered = structuredClone(cameras);
  tampered.poses[0].position.east += 1;
  assert.throws(
    () => validateCaptureContract({ ...base, cameraSet: tampered }),
    /content hash/,
  );

  const reordered = structuredClone(cameras);
  reordered.poses.reverse();
  assert.throws(
    () => validateCaptureContract({ ...base, cameraSet: reordered }),
    /pose order/,
  );

  const tooFew = structuredClone(cameras);
  tooFew.poses.pop();
  assert.throws(
    () => validateCaptureContract({ ...base, cameraSet: tooFew }),
    /exactly three/,
  );
});

test('report maps only raw bounded evidence and never authors acceptance', () => {
  const { buildViewerPerformanceReport, validateCaptureContract } = subject();
  const cameras = cameraSet();
  const contract = validateCaptureContract({
    policy: policy(cameras),
    cameraSet: cameras,
    studioUrl: 'http://localhost:8000/web/studio/',
    sourceRole: 'internal-canary',
  });
  const poseSnapshots = cameras.poses.map((item) => ({
    schema: 'nantai.viewer-acceptance-probe-state.v1',
    probe_version: 'nantai.viewer-acceptance-probe.v1',
    state: 'complete',
    pose_id: item.pose_id,
    requested_representation: 'full-3dgs',
    representation: 'full-3dgs',
    interactive_ms: 120,
    warmup_frame_ms: Array(120).fill(16),
    measured_frame_ms: Array(600).fill(16),
    timed_out: false,
    sample_overflow: false,
    accepted: true,
  }));
  const report = buildViewerPerformanceReport({
    contract,
    sceneManifestSha256: 'd'.repeat(64),
    runtime: {
      browser_name: 'chromium',
      browser_version: '151.0',
      playwright_version: '1.62.0',
      operating_system: 'darwin arm64',
      gpu_vendor: 'Apple',
      gpu_renderer: 'ANGLE Metal Apple M4 Pro',
      webgl_version: 'WebGL 2.0',
    },
    poseSnapshots,
    consoleErrors: Array.from(
      { length: 102 },
      (_, index) => `error-${index}`,
    ),
    unhandledRejections: [],
  });

  assert.equal(report.schema, 'nantai.viewer-performance-report.v1');
  assert.equal(Object.hasOwn(report, 'accepted'), false);
  assert.equal(Object.hasOwn(report.poses[0], 'state'), false);
  assert.equal(Object.hasOwn(report.poses[0], 'accepted'), false);
  assert.equal(report.poses[0].measured_frame_ms.length, 600);
  assert.equal(report.console_errors.length, 100);
  assert.match(report.console_errors.at(-1), /additional messages omitted/);
});

test('only exact same-origin optional HEAD 404 probes are non-errors', () => {
  const { httpFailureMessage, requestFailureMessage } = subject();
  const studioUrl = new URL('http://127.0.0.1:8767/web/studio/');

  for (const pathname of [
    '/web/data/coverage-audit.json',
    '/web/data/roaming-graph.json',
  ]) {
    assert.equal(
      httpFailureMessage({
        studioUrl,
        method: 'HEAD',
        requestUrl: new URL(pathname, studioUrl).href,
        status: 404,
      }),
      null,
    );
    assert.equal(
      requestFailureMessage({
        studioUrl,
        method: 'HEAD',
        requestUrl: new URL(pathname, studioUrl).href,
        errorText: 'net::ERR_ABORTED',
      }),
      null,
    );
  }
  for (const failure of [
    {
      method: 'GET',
      requestUrl: 'http://127.0.0.1:8767/web/data/coverage-audit.json',
      status: 404,
    },
    {
      method: 'HEAD',
      requestUrl: 'http://127.0.0.1:8767/web/data/recon/recon_full.ply',
      status: 404,
    },
    {
      method: 'HEAD',
      requestUrl: 'http://example.com/web/data/coverage-audit.json',
      status: 404,
    },
    {
      method: 'GET',
      requestUrl: 'http://127.0.0.1:8767/api/project',
      status: 500,
    },
  ]) {
    assert.match(
      httpFailureMessage({ studioUrl, ...failure }),
      /HTTP/,
    );
  }
  assert.match(
    requestFailureMessage({
      studioUrl,
      method: 'GET',
      requestUrl: 'http://127.0.0.1:8767/web/data/recon/recon_full.ply',
      errorText: 'net::ERR_FAILED',
    }),
    /ERR_FAILED/,
  );
});

test('Chromium resource noise is replaced by explicit HTTP evidence only', () => {
  const { isGenericResourceConsoleNoise } = subject();

  assert.equal(
    isGenericResourceConsoleNoise(
      'Failed to load resource: the server responded with a status of 404 (Not Found)',
    ),
    true,
  );
  assert.equal(
    isGenericResourceConsoleNoise('shader compile failed'),
    false,
  );
  assert.equal(
    isGenericResourceConsoleNoise('Failed to load scene artifact'),
    false,
  );
});
