import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import {
  chmod,
  mkdtemp,
  mkdir,
  rm,
  symlink,
  writeFile,
} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
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

function sha(payload) {
  return createHash('sha256').update(payload).digest('hex');
}

function artifact(path, payload) {
  return {
    path,
    sha256: sha(payload),
    byte_length: Buffer.byteLength(payload),
  };
}

function executable(role, payload) {
  const snapshot = {
    sha256: sha(payload),
    byte_length: Buffer.byteLength(payload),
    device_id: '1',
    file_id: '2',
    mtime_ns: '3',
    mode: 0o100755,
    executable: true,
  };
  return { role, before: snapshot, after: { ...snapshot } };
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
  assert.equal(
    poseIdFor(first),
    'pose-6488fe6fd0d6111852489a7fa16ca9be0554f46562c43dfc0a7edfde65f36a51',
  );
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

test('production v2 report binds capture inputs executables and screenshots', () => {
  const {
    buildViewerPerformanceReport,
    buildViewerPerformanceReportV2,
    validateCaptureContract,
    viewerReportV2ContentSha,
  } = subject();
  const cameras = cameraSet();
  const contract = validateCaptureContract({
    policy: policy(cameras),
    cameraSet: cameras,
    studioUrl: 'http://localhost:8000/web/studio/',
    sourceRole: 'production-acceptance',
  });
  const poseSnapshots = cameras.poses.map((item) => ({
    pose_id: item.pose_id,
    representation: 'full-3dgs',
    interactive_ms: 120,
    warmup_frame_ms: Array(120).fill(16),
    measured_frame_ms: Array(600).fill(16),
    timed_out: false,
    sample_overflow: false,
  }));
  const baseReport = buildViewerPerformanceReport({
    contract,
    sceneManifestSha256: sha('scene'),
    runtime: {
      browser_name: 'chromium',
      browser_version: '151.0',
      playwright_version: '1.62.0',
      operating_system: 'linux x64',
      gpu_vendor: 'NVIDIA',
      gpu_renderer: 'NVIDIA RTX',
      webgl_version: 'WebGL 2.0',
    },
    poseSnapshots,
    consoleErrors: [],
    unhandledRejections: [],
  });
  const screenshots = cameras.poses.map((item, index) => artifact(
    `viewer/screenshots/${item.pose_id}.png`,
    `png-${index}`,
  )).map((binding, index) => ({
    pose_id: cameras.poses[index].pose_id,
    ...binding,
  }));
  const report = buildViewerPerformanceReportV2({
    baseReport,
    sceneManifest: artifact('imported/manifest.json', 'scene'),
    viewerPolicy: artifact('viewer/policy.json', 'policy'),
    cameraSet: artifact('viewer/cameras.json', 'cameras'),
    captureScript: artifact('viewer/capture.mjs', 'capture'),
    probeModule: artifact('viewer/probe.mjs', 'probe'),
    playwrightPackage: artifact(
      'viewer/playwright-package.json',
      'playwright',
    ),
    nodeExecutable: executable('node', 'node'),
    browserExecutable: executable('browser', 'chromium'),
    screenshots,
  });

  assert.equal(report.schema, 'nantai.viewer-performance-report.v2');
  assert.equal(report.content_sha256, viewerReportV2ContentSha(report));
  assert.equal(report.report_id, `viewer-capture-${report.content_sha256}`);
  assert.equal(report.screenshots.length, 3);
  assert.equal(Object.hasOwn(report, 'accepted'), false);
});

test('Viewer v2 content hash includes the canonical LF byte', () => {
  const { viewerReportV2ContentSha } = subject();
  const report = {
    schema: 'fixture',
    label: 'x',
    report_id: `viewer-capture-${'0'.repeat(64)}`,
    content_sha256: '0'.repeat(64),
  };

  assert.equal(
    viewerReportV2ContentSha(report),
    sha('{"label":"x","schema":"fixture"}\n'),
  );
});

test('production v2 report rejects executable and screenshot identity drift', () => {
  const { buildViewerPerformanceReportV2 } = subject();
  const baseReport = {
    schema: 'nantai.viewer-performance-report.v1',
    probe_version: 'nantai.viewer-acceptance-probe.v1',
    source_role: 'production-acceptance',
    scene_manifest_sha256: sha('scene'),
    viewport_width: 1280,
    viewport_height: 720,
    http_cache: 'empty',
    runtime: {
      browser_name: 'chromium',
      browser_version: '151.0',
      playwright_version: '1.62.0',
      operating_system: 'linux x64',
      gpu_vendor: 'NVIDIA',
      gpu_renderer: 'NVIDIA RTX',
      webgl_version: 'WebGL 2.0',
    },
    poses: [0, 1, 2].map((index) => ({
      pose_id: `pose-${String.fromCharCode(97 + index).repeat(64)}`,
      representation: 'full-3dgs',
      interactive_ms: 120,
      warmup_frame_ms: Array(120).fill(16),
      measured_frame_ms: Array(600).fill(16),
      timed_out: false,
      sample_overflow: false,
    })),
    console_errors: [],
    unhandled_rejections: [],
  };
  const inputs = {
    baseReport,
    sceneManifest: artifact('imported/manifest.json', 'scene'),
    viewerPolicy: artifact('viewer/policy.json', 'policy'),
    cameraSet: artifact('viewer/cameras.json', 'cameras'),
    captureScript: artifact('viewer/capture.mjs', 'capture'),
    probeModule: artifact('viewer/probe.mjs', 'probe'),
    playwrightPackage: artifact('viewer/playwright.json', 'playwright'),
    nodeExecutable: executable('node', 'node'),
    browserExecutable: executable('browser', 'browser'),
    screenshots: baseReport.poses.map((row, index) => ({
      pose_id: row.pose_id,
      ...artifact(`viewer/${row.pose_id}.png`, `png-${index}`),
    })),
  };
  const changedBrowser = structuredClone(inputs.browserExecutable);
  changedBrowser.after.file_id = '999';
  assert.throws(
    () => buildViewerPerformanceReportV2({
      ...inputs,
      browserExecutable: changedBrowser,
    }),
    /browser executable changed/,
  );
  assert.throws(
    () => buildViewerPerformanceReportV2({
      ...inputs,
      screenshots: [...inputs.screenshots].reverse(),
    }),
    /screenshot pose order/,
  );
});

test('capture file helpers bind exact in-root bytes and publish copies no-replace', async () => {
  const {
    artifactBindingFromFile,
    executableSnapshotFromFile,
    materializeCaptureInput,
    relativeEvidencePath,
  } = subject();
  const root = await mkdtemp(path.join(os.tmpdir(), 'nantai-viewer-v2-'));
  try {
    const source = path.join(root, 'inputs', 'policy.json');
    await mkdir(path.dirname(source), { recursive: true });
    await writeFile(source, 'policy\n', { flag: 'wx' });
    const relative = await relativeEvidencePath(root, source);
    assert.equal(relative, 'inputs/policy.json');
    assert.deepEqual(
      await artifactBindingFromFile(root, relative),
      artifact('inputs/policy.json', 'policy\n'),
    );

    const copy = await materializeCaptureInput({
      evidenceRoot: root,
      relativePath: 'viewer/capture.mjs',
      payload: Buffer.from('capture\n'),
    });
    assert.deepEqual(copy, artifact('viewer/capture.mjs', 'capture\n'));
    await assert.rejects(
      materializeCaptureInput({
        evidenceRoot: root,
        relativePath: 'viewer/capture.mjs',
        payload: Buffer.from('replacement\n'),
      }),
      /already exists/,
    );

    const executable = path.join(
      root,
      process.platform === 'win32' ? 'tool.exe' : 'tool',
    );
    await writeFile(executable, 'tool\n', { flag: 'wx' });
    await chmod(executable, 0o755);
    const snapshot = await executableSnapshotFromFile(executable);
    assert.equal(snapshot.sha256, sha('tool\n'));
    assert.equal(snapshot.byte_length, 5);
    assert.match(snapshot.device_id, /^[0-9]+$/);
    assert.match(snapshot.file_id, /^[0-9]+$/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test('capture file helpers reject escapes and symlink evidence', async (t) => {
  const { artifactBindingFromFile, relativeEvidencePath } = subject();
  const root = await mkdtemp(path.join(os.tmpdir(), 'nantai-viewer-v2-'));
  const outside = path.join(path.dirname(root), `${path.basename(root)}-outside`);
  try {
    await writeFile(outside, 'outside\n', { flag: 'wx' });
    await assert.rejects(
      relativeEvidencePath(root, outside),
      /inside the evidence root/,
    );
    const link = path.join(root, 'linked.json');
    try {
      await symlink(outside, link);
    } catch (error) {
      if (process.platform === 'win32' && error.code === 'EPERM') {
        t.skip('Windows symlink privilege unavailable');
        return;
      }
      throw error;
    }
    await assert.rejects(
      artifactBindingFromFile(root, 'linked.json'),
      /symlink/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(outside, { force: true });
  }
});

test('production capture context materializes code and detects runtime drift', async () => {
  const {
    finalizeProductionCaptureEvidence,
    prepareProductionCaptureEvidence,
  } = subject();
  const root = await mkdtemp(path.join(os.tmpdir(), 'nantai-viewer-v2-'));
  const sources = await mkdtemp(path.join(os.tmpdir(), 'nantai-viewer-src-'));
  try {
    const policyPath = path.join(root, 'viewer', 'policy.json');
    const cameraSetPath = path.join(root, 'viewer', 'cameras.json');
    const sceneManifestPath = path.join(root, 'imported', 'manifest.json');
    await mkdir(path.dirname(policyPath), { recursive: true });
    await mkdir(path.dirname(sceneManifestPath), { recursive: true });
    await writeFile(policyPath, 'policy\n', { flag: 'wx' });
    await writeFile(cameraSetPath, 'cameras\n', { flag: 'wx' });
    await writeFile(sceneManifestPath, 'scene\n', { flag: 'wx' });
    const captureScriptPath = path.join(sources, 'capture.mjs');
    const probeModulePath = path.join(sources, 'probe.mjs');
    const playwrightPackagePath = path.join(sources, 'package.json');
    await writeFile(captureScriptPath, 'capture\n', { flag: 'wx' });
    await writeFile(probeModulePath, 'probe\n', { flag: 'wx' });
    await writeFile(playwrightPackagePath, 'playwright\n', { flag: 'wx' });
    const executableName = (name) => (
      process.platform === 'win32' ? `${name}.exe` : name
    );
    const nodePath = path.join(sources, executableName('node'));
    const browserPath = path.join(sources, executableName('browser'));
    await writeFile(nodePath, 'node\n', { flag: 'wx' });
    await writeFile(browserPath, 'browser\n', { flag: 'wx' });
    await chmod(nodePath, 0o755);
    await chmod(browserPath, 0o755);

    const context = await prepareProductionCaptureEvidence({
      evidenceRoot: root,
      policyPath,
      cameraSetPath,
      sceneManifestPath,
      captureScriptPath,
      probeModulePath,
      playwrightPackagePath,
      nodePath,
      browserPath,
    });
    assert.equal(context.viewerPolicy.path, 'viewer/policy.json');
    assert.equal(
      context.captureScript.path,
      'viewer/capture-inputs/capture_viewer_acceptance.mjs',
    );
    assert.equal(
      context.nodeExecutableBefore.sha256,
      sha('node\n'),
    );
    await writeFile(browserPath, 'changed\n');
    await assert.rejects(
      finalizeProductionCaptureEvidence({
        context,
        baseReport: {
          schema: 'nantai.viewer-performance-report.v1',
          source_role: 'production-acceptance',
          scene_manifest_sha256: context.sceneManifest.sha256,
          poses: [],
        },
        screenshots: [],
      }),
      /browser executable changed/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(sources, { recursive: true, force: true });
  }
});

test('production CLI requires an explicit evidence root while canary stays v1-compatible', () => {
  const { parseCaptureArgs } = subject();
  const base = [
    '--policy', 'policy.json',
    '--camera-set', 'cameras.json',
    '--studio-url', 'http://localhost:8000/web/studio/',
    '--scene-manifest', 'manifest.json',
    '--output', 'viewer-report.json',
  ];
  assert.throws(
    () => parseCaptureArgs([
      ...base,
      '--source-role', 'production-acceptance',
    ]),
    /evidence root/,
  );
  const canary = parseCaptureArgs([
    ...base,
    '--source-role', 'internal-canary',
  ]);
  assert.equal(canary.evidenceRoot, undefined);
  const production = parseCaptureArgs([
    ...base,
    '--source-role', 'production-acceptance',
    '--evidence-root', 'run',
  ]);
  assert.equal(production.evidenceRoot, 'run');
});

test('production validator command reopens the same evidence root', () => {
  const { viewerValidatorArgs } = subject();
  const common = {
    policy: 'run/viewer/policy.json',
    report: 'run/viewer/report.json',
    decision: 'run/viewer/decision.json',
  };
  assert.deepEqual(
    viewerValidatorArgs({
      ...common,
      sourceRole: 'production-acceptance',
      evidenceRoot: 'run',
    }),
    [
      '-m',
      'pipeline.viewer_acceptance',
      '--policy',
      common.policy,
      '--report',
      common.report,
      '--decision',
      common.decision,
      '--evidence-root',
      'run',
    ],
  );
  assert.throws(
    () => viewerValidatorArgs({
      ...common,
      sourceRole: 'production-acceptance',
    }),
    /evidence root/,
  );
  assert.deepEqual(
    viewerValidatorArgs({
      ...common,
      sourceRole: 'internal-canary',
    }),
    [
      '-m',
      'pipeline.viewer_acceptance',
      '--policy',
      common.policy,
      '--report',
      common.report,
      '--decision',
      common.decision,
    ],
  );
});

test('production capture accepts Python canonical typed policy bytes', () => {
  const { assertCanonicalPolicyBytes, canonicalJson } = subject();
  const value = policy();
  const expected = Buffer.from([
    '{"allow_software_renderer":false',
    ',"maximum_interactive_ms":15000.0',
    ',"maximum_p50_frame_ms":33.34',
    ',"maximum_p95_frame_ms":50.0',
    ',"maximum_worst_frame_ms":250.0',
    ',"measured_frame_count":600',
    ',"percentile_method":"nearest-rank"',
    ',"required_http_cache":"empty"',
    `,"required_pose_ids":${JSON.stringify(value.required_pose_ids)}`,
    ',"required_representation":"full-3dgs"',
    ',"schema":"nantai.viewer-performance-policy.v1"',
    ',"viewport_height":720',
    ',"viewport_width":1280',
    ',"warmup_frame_count":120}\n',
  ].join(''));

  assert.doesNotThrow(
    () => assertCanonicalPolicyBytes(value, expected),
  );
  assert.throws(
    () => assertCanonicalPolicyBytes(
      value,
      Buffer.from(`${canonicalJson(value)}\n`),
    ),
    /viewer policy.*canonical JSON/,
  );
});

test('production capture binds the scene and probe bytes actually served', () => {
  const { verifyServedCaptureArtifact } = subject();
  const scene = artifact('imported/manifest.json', 'scene-bytes');
  const probe = artifact('viewer/acceptance-probe.mjs', 'probe-bytes');

  assert.doesNotThrow(
    () => verifyServedCaptureArtifact(
      scene,
      Buffer.from('scene-bytes'),
      'scene manifest',
    ),
  );
  assert.doesNotThrow(
    () => verifyServedCaptureArtifact(
      probe,
      Buffer.from('probe-bytes'),
      'Viewer probe module',
    ),
  );
  assert.throws(
    () => verifyServedCaptureArtifact(
      scene,
      Buffer.from('other-scene'),
      'scene manifest',
    ),
    /scene manifest.*served bytes/,
  );
  assert.throws(
    () => verifyServedCaptureArtifact(
      probe,
      Buffer.from('other-probe'),
      'Viewer probe module',
    ),
    /Viewer probe module.*served bytes/,
  );
});

test('pose screenshot capture uses a fixed no-replace path and returns exact binding', async () => {
  const { capturePoseScreenshot } = subject();
  const root = await mkdtemp(path.join(os.tmpdir(), 'nantai-viewer-v2-'));
  const poseId = `pose-${'a'.repeat(64)}`;
  const payload = Buffer.from('png-bytes');
  const calls = [];
  const target = {
    async screenshot(options) {
      calls.push(options);
      await mkdir(path.dirname(options.path), { recursive: true });
      await writeFile(options.path, payload, { flag: 'wx' });
    },
  };
  try {
    const binding = await capturePoseScreenshot({
      target,
      evidenceRoot: root,
      poseId,
    });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].type, 'png');
    assert.equal(
      binding.path,
      `viewer/screenshots/${poseId}.png`,
    );
    assert.equal(binding.sha256, sha(payload));
    await assert.rejects(
      capturePoseScreenshot({
        target,
        evidenceRoot: root,
        poseId,
      }),
      /already exists/,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
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
