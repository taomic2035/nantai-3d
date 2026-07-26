#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
import { readFile, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

import { chromium } from 'playwright';
import playwrightPackage from 'playwright/package.json' with { type: 'json' };

const SHA256 = /^[0-9a-f]{64}$/;
const POSE_ID = /^pose-[0-9a-f]{64}$/;
const CAMERA_POSE_SCHEMA = 'nantai.viewer-camera-pose.v1';
const CAMERA_SET_SCHEMA = 'nantai.viewer-camera-set.v1';
const POLICY_SCHEMA = 'nantai.viewer-performance-policy.v1';
const REPORT_SCHEMA = 'nantai.viewer-performance-report.v1';
const PROBE_VERSION = 'nantai.viewer-acceptance-probe.v1';
const OPTIONAL_STUDIO_EVIDENCE = new Set([
  '/web/data/coverage-audit.json',
  '/web/data/production-camera-plan.json',
  '/web/data/roaming-graph.json',
]);
const SOURCE_ROLES = new Set([
  'internal-canary',
  'production-acceptance',
]);
const POLICY_KEYS = [
  'allow_software_renderer',
  'maximum_interactive_ms',
  'maximum_p50_frame_ms',
  'maximum_p95_frame_ms',
  'maximum_worst_frame_ms',
  'measured_frame_count',
  'percentile_method',
  'required_http_cache',
  'required_pose_ids',
  'required_representation',
  'schema',
  'viewport_height',
  'viewport_width',
  'warmup_frame_count',
];

export function isGenericResourceConsoleNoise(message) {
  return /^Failed to load resource:/.test(message);
}

function isOptionalStudioEvidence({
  studioUrl,
  method,
  request,
}) {
  return (
    request.origin === studioUrl.origin
    && String(method).toUpperCase() === 'HEAD'
    && OPTIONAL_STUDIO_EVIDENCE.has(request.pathname)
    && request.search === ''
  );
}

export function httpFailureMessage({
  studioUrl,
  method,
  requestUrl,
  status,
}) {
  if (!Number.isSafeInteger(status) || status < 400) return null;
  const request = new URL(requestUrl);
  const normalizedMethod = String(method).toUpperCase();
  if (
    isOptionalStudioEvidence({
      studioUrl,
      method: normalizedMethod,
      request,
    })
    && status === 404
  ) {
    return null;
  }
  const resource = request.origin === studioUrl.origin
    ? request.pathname
    : `[cross-origin]${request.pathname}`;
  return `${normalizedMethod} ${resource}: HTTP ${status}`;
}

export function requestFailureMessage({
  studioUrl,
  method,
  requestUrl,
  errorText,
}) {
  const request = new URL(requestUrl);
  const normalizedMethod = String(method).toUpperCase();
  if (isOptionalStudioEvidence({
    studioUrl,
    method: normalizedMethod,
    request,
  })) {
    return null;
  }
  const resource = request.origin === studioUrl.origin
    ? request.pathname
    : `[cross-origin]${request.pathname}`;
  return `${normalizedMethod} ${resource}: ${errorText || 'request failed'}`;
}

function plainRecord(value, label) {
  if (
    value === null
    || typeof value !== 'object'
    || Array.isArray(value)
    || Object.getPrototypeOf(value) !== Object.prototype
  ) {
    throw new TypeError(`${label} must be a plain object`);
  }
  return value;
}

function exactKeys(value, expected, label) {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (
    actual.length !== wanted.length
    || actual.some((item, index) => item !== wanted[index])
  ) {
    throw new TypeError(`${label} has an unexpected field set`);
  }
}

function canonicalValue(value, label = 'value') {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new TypeError(`${label} contains a non-finite number`);
    }
    return value;
  }
  if (Array.isArray(value)) {
    return value.map((item, index) => canonicalValue(item, `${label}[${index}]`));
  }
  plainRecord(value, label);
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, canonicalValue(value[key], `${label}.${key}`)]),
  );
}

export function canonicalJson(value) {
  return JSON.stringify(canonicalValue(value));
}

function validatedEnu(value, label) {
  plainRecord(value, label);
  exactKeys(value, ['east', 'north', 'up'], label);
  for (const axis of ['east', 'north', 'up']) {
    if (!Number.isFinite(value[axis])) {
      throw new TypeError(`${label}.${axis} must be finite`);
    }
  }
  return {
    east: value.east,
    north: value.north,
    up: value.up,
  };
}

function validatedPosePayload(value) {
  plainRecord(value, 'camera pose');
  exactKeys(value, ['schema', 'position', 'look_at'], 'camera pose');
  if (value.schema !== CAMERA_POSE_SCHEMA) {
    throw new TypeError('camera pose schema is unsupported');
  }
  return {
    schema: CAMERA_POSE_SCHEMA,
    position: validatedEnu(value.position, 'camera pose position'),
    look_at: validatedEnu(value.look_at, 'camera pose look_at'),
  };
}

export function poseIdFor(pose) {
  const payload = validatedPosePayload(pose);
  return `pose-${createHash('sha256').update(canonicalJson(payload)).digest('hex')}`;
}

function localStudioUrl(rawUrl) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new TypeError('capture requires a local Studio URL');
  }
  const loopback = new Set(['127.0.0.1', 'localhost', '[::1]']);
  if (
    url.protocol !== 'http:'
    || !loopback.has(url.hostname)
    || url.username !== ''
    || url.password !== ''
    || !(
      url.pathname === '/web/studio/'
      || url.pathname === '/web/studio/index.html'
    )
  ) {
    throw new TypeError('capture requires a local Studio URL');
  }
  return url;
}

function validatedPolicy(value) {
  plainRecord(value, 'viewer policy');
  exactKeys(value, POLICY_KEYS, 'viewer policy');
  if (value.schema !== POLICY_SCHEMA) {
    throw new TypeError('viewer policy schema is unsupported');
  }
  if (
    value.viewport_width !== 1280
    || value.viewport_height !== 720
    || value.warmup_frame_count !== 120
    || value.measured_frame_count !== 600
    || value.percentile_method !== 'nearest-rank'
    || value.required_representation !== 'full-3dgs'
    || value.required_http_cache !== 'empty'
    || value.allow_software_renderer !== false
  ) {
    throw new TypeError('viewer policy differs from the production capture contract');
  }
  for (const key of [
    'maximum_interactive_ms',
    'maximum_p50_frame_ms',
    'maximum_p95_frame_ms',
    'maximum_worst_frame_ms',
  ]) {
    if (!Number.isFinite(value[key]) || value[key] <= 0) {
      throw new TypeError(`viewer policy ${key} must be finite and positive`);
    }
  }
  if (
    !Array.isArray(value.required_pose_ids)
    || value.required_pose_ids.length !== 3
    || new Set(value.required_pose_ids).size !== 3
    || value.required_pose_ids.some((item) => !POSE_ID.test(item))
  ) {
    throw new TypeError('viewer policy requires exactly three content-addressed poses');
  }
  return structuredClone(value);
}

function validatedCameraSet(value) {
  plainRecord(value, 'camera set');
  exactKeys(value, ['schema', 'poses'], 'camera set');
  if (value.schema !== CAMERA_SET_SCHEMA || !Array.isArray(value.poses)) {
    throw new TypeError('camera set schema is unsupported');
  }
  if (value.poses.length !== 3) {
    throw new TypeError('camera set must contain exactly three poses');
  }
  const poses = value.poses.map((item, index) => {
    plainRecord(item, `camera set pose ${index}`);
    exactKeys(
      item,
      ['pose_id', 'schema', 'position', 'look_at'],
      `camera set pose ${index}`,
    );
    const payload = validatedPosePayload({
      schema: item.schema,
      position: item.position,
      look_at: item.look_at,
    });
    if (!POSE_ID.test(item.pose_id) || poseIdFor(payload) !== item.pose_id) {
      throw new TypeError(`camera set pose ${index} content hash disagrees`);
    }
    return { pose_id: item.pose_id, ...payload };
  });
  if (new Set(poses.map((item) => item.pose_id)).size !== poses.length) {
    throw new TypeError('camera set pose ids must be unique');
  }
  return { schema: CAMERA_SET_SCHEMA, poses };
}

export function validateCaptureContract({
  policy,
  cameraSet,
  studioUrl,
  sourceRole,
}) {
  const validatedPolicyValue = validatedPolicy(policy);
  const validatedCameraSetValue = validatedCameraSet(cameraSet);
  if (!SOURCE_ROLES.has(sourceRole)) {
    throw new TypeError('source role is unsupported');
  }
  const poseIds = validatedCameraSetValue.poses.map((item) => item.pose_id);
  if (
    poseIds.some(
      (poseId, index) => validatedPolicyValue.required_pose_ids[index] !== poseId,
    )
  ) {
    throw new TypeError('camera set pose order differs from viewer policy');
  }
  return Object.freeze({
    policy: validatedPolicyValue,
    cameraSet: validatedCameraSetValue,
    studioUrl: localStudioUrl(studioUrl),
    sourceRole,
  });
}

function boundedMessages(messages) {
  if (!Array.isArray(messages)) {
    throw new TypeError('browser messages must be an array');
  }
  const normalized = messages.map((value) => String(value).slice(0, 500));
  if (normalized.length <= 100) return normalized;
  return [
    ...normalized.slice(0, 99),
    `${normalized.length - 99} additional messages omitted`,
  ];
}

function validatedRuntime(runtime) {
  plainRecord(runtime, 'viewer runtime');
  const keys = [
    'browser_name',
    'browser_version',
    'playwright_version',
    'operating_system',
    'gpu_vendor',
    'gpu_renderer',
    'webgl_version',
  ];
  exactKeys(runtime, keys, 'viewer runtime');
  for (const key of keys) {
    if (typeof runtime[key] !== 'string' || runtime[key].trim() === '') {
      throw new TypeError(`viewer runtime ${key} is required`);
    }
  }
  return { ...runtime };
}

export function buildViewerPerformanceReport({
  contract,
  sceneManifestSha256,
  runtime,
  poseSnapshots,
  consoleErrors,
  unhandledRejections,
}) {
  if (!SHA256.test(sceneManifestSha256)) {
    throw new TypeError('scene manifest SHA-256 is invalid');
  }
  if (
    !Array.isArray(poseSnapshots)
    || poseSnapshots.length !== contract.cameraSet.poses.length
  ) {
    throw new TypeError('pose snapshot set is incomplete');
  }
  const poses = poseSnapshots.map((snapshot, index) => {
    plainRecord(snapshot, `pose snapshot ${index}`);
    const expectedId = contract.cameraSet.poses[index].pose_id;
    if (snapshot.pose_id !== expectedId) {
      throw new TypeError('pose snapshot order differs from capture contract');
    }
    return {
      pose_id: expectedId,
      representation: snapshot.representation,
      interactive_ms: snapshot.interactive_ms,
      warmup_frame_ms: [...snapshot.warmup_frame_ms],
      measured_frame_ms: [...snapshot.measured_frame_ms],
      timed_out: snapshot.timed_out,
      sample_overflow: snapshot.sample_overflow,
    };
  });
  return {
    schema: REPORT_SCHEMA,
    probe_version: PROBE_VERSION,
    source_role: contract.sourceRole,
    scene_manifest_sha256: sceneManifestSha256,
    viewport_width: contract.policy.viewport_width,
    viewport_height: contract.policy.viewport_height,
    http_cache: 'empty',
    runtime: validatedRuntime(runtime),
    poses,
    console_errors: boundedMessages(consoleErrors),
    unhandled_rejections: boundedMessages(unhandledRejections),
  };
}

async function sendViewerCommand(page, type, payload, timeoutMs) {
  const requestId = `acceptance-${randomUUID()}`;
  return page.evaluate(
    ({ commandType, commandPayload, commandRequestId, commandTimeoutMs }) => (
      new Promise((resolve, reject) => {
        const frame = document.getElementById('viewer-frame');
        if (!frame?.contentWindow) {
          reject(new Error('Studio viewer frame is unavailable'));
          return;
        }
        const timer = setTimeout(() => {
          window.removeEventListener('message', onMessage);
          reject(new Error(`Viewer command timed out: ${commandType}`));
        }, commandTimeoutMs);
        function onMessage(event) {
          const message = event.data;
          if (
            event.origin !== window.location.origin
            || event.source !== frame.contentWindow
            || message?.channel !== 'nantai-viewer'
            || message?.schema_version !== 1
            || message?.request_id !== commandRequestId
          ) return;
          clearTimeout(timer);
          window.removeEventListener('message', onMessage);
          if (message.type === 'error') {
            reject(new Error(
              message.payload?.message
              ?? message.payload?.code
              ?? 'Viewer command failed',
            ));
          } else {
            resolve(message.payload?.result);
          }
        }
        window.addEventListener('message', onMessage);
        frame.contentWindow.postMessage({
          channel: 'nantai-viewer',
          schema_version: 1,
          type: commandType,
          request_id: commandRequestId,
          payload: commandPayload,
        }, window.location.origin);
      })
    ),
    {
      commandType: type,
      commandPayload: payload,
      commandRequestId: requestId,
      commandTimeoutMs: timeoutMs,
    },
  );
}

async function snapshotProbe(frame) {
  return frame.evaluate(
    () => window.__NANTAI_VIEWER_ACCEPTANCE__.snapshot(),
  );
}

async function timeoutProbe(frame) {
  return frame.evaluate(
    () => window.__NANTAI_VIEWER_ACCEPTANCE__.markTimedOut({
      nowMs: performance.now(),
    }),
  );
}

function isPlaywrightTimeout(error) {
  return error?.name === 'TimeoutError';
}

async function measurePose({
  page,
  viewerFrame,
  pose,
  interactiveTimeoutMs,
  measurementTimeoutMs,
}) {
  await sendViewerCommand(
    page,
    'setCameraPose',
    { position: pose.position, look_at: pose.look_at },
    interactiveTimeoutMs,
  );
  await viewerFrame.evaluate(
    ({ poseId }) => window.__NANTAI_VIEWER_ACCEPTANCE__.beginPose({
      poseId,
      requestedRepresentation: 'full-3dgs',
      startedAtMs: performance.now(),
    }),
    { poseId: pose.pose_id },
  );
  try {
    await viewerFrame.waitForFunction(
      () => {
        const evidence = window.__NANTAI_VIEWER_ACCEPTANCE__.snapshot();
        return (
          evidence.interactive_ms !== null
          || evidence.state === 'representation-lost'
          || evidence.state === 'timed-out'
        );
      },
      undefined,
      { timeout: interactiveTimeoutMs },
    );
  } catch (error) {
    if (!isPlaywrightTimeout(error)) throw error;
    return timeoutProbe(viewerFrame);
  }

  let evidence = await snapshotProbe(viewerFrame);
  if (evidence.state === 'representation-lost' || evidence.state === 'timed-out') {
    return evidence;
  }
  try {
    await viewerFrame.waitForFunction(
      () => {
        const state = window.__NANTAI_VIEWER_ACCEPTANCE__.snapshot().state;
        return (
          state === 'complete'
          || state === 'representation-lost'
          || state === 'timed-out'
        );
      },
      undefined,
      { timeout: measurementTimeoutMs },
    );
  } catch (error) {
    if (!isPlaywrightTimeout(error)) throw error;
    return timeoutProbe(viewerFrame);
  }
  evidence = await snapshotProbe(viewerFrame);
  return evidence;
}

async function runtimeIdentity(viewerFrame, browserVersion) {
  const gpu = await viewerFrame.evaluate(() => {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl');
    if (!gl) {
      return {
        vendor: 'unknown',
        renderer: 'unknown',
        webgl: 'unavailable',
        platform: navigator.platform || 'unknown',
      };
    }
    const debug = gl.getExtension('WEBGL_debug_renderer_info');
    return {
      vendor: String(debug
        ? gl.getParameter(debug.UNMASKED_VENDOR_WEBGL)
        : gl.getParameter(gl.VENDOR)),
      renderer: String(debug
        ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)
        : gl.getParameter(gl.RENDERER)),
      webgl: String(gl.getParameter(gl.VERSION)),
      platform: navigator.userAgentData?.platform
        ?? navigator.platform
        ?? 'unknown',
    };
  });
  return {
    browser_name: 'chromium',
    browser_version: browserVersion,
    playwright_version: playwrightPackage.version,
    operating_system: `${os.platform()} ${os.arch()} (${gpu.platform})`,
    gpu_vendor: gpu.vendor,
    gpu_renderer: gpu.renderer,
    webgl_version: gpu.webgl,
  };
}

async function selectAcceptancePresentation(page, viewerFrame, timeoutMs) {
  let state = await sendViewerCommand(page, 'getState', {}, timeoutMs);
  for (
    let attempt = 0;
    state?.presentation?.mode !== 'points' && attempt < 3;
    attempt += 1
  ) {
    const toggle = viewerFrame.locator('#presentation-toggle');
    if (!await toggle.isVisible()) {
      throw new Error('Viewer cannot switch to the Gaussian presentation');
    }
    await toggle.click();
    state = await sendViewerCommand(page, 'getState', {}, timeoutMs);
  }
  if (state?.presentation?.mode !== 'points') {
    throw new Error('Viewer did not activate the Gaussian presentation');
  }
  await sendViewerCommand(
    page,
    'setLayer',
    { layer: 'reconstruction', visible: true },
    timeoutMs,
  );
  await sendViewerCommand(
    page,
    'setLayer',
    { layer: 'world', visible: false },
    timeoutMs,
  );
}

async function collectUnhandledRejections(page) {
  const collected = [];
  for (const frame of page.frames()) {
    try {
      const messages = await frame.evaluate(
        () => globalThis.__NANTAI_UNHANDLED_REJECTIONS__ ?? [],
      );
      collected.push(...messages);
    } catch {
      // A detached optional frame contributes no evidence.
    }
  }
  return collected;
}

export async function captureViewerEvidence({
  contract,
  sceneManifestSha256,
  headless = false,
  measurementTimeoutMs = 120_000,
}) {
  const browser = await chromium.launch({ headless });
  try {
    const context = await browser.newContext({
      viewport: {
        width: contract.policy.viewport_width,
        height: contract.policy.viewport_height,
      },
      serviceWorkers: 'block',
    });
    await context.addInitScript(() => {
      globalThis.__NANTAI_UNHANDLED_REJECTIONS__ = [];
      globalThis.addEventListener('unhandledrejection', (event) => {
        const reason = event.reason instanceof Error
          ? `${event.reason.name}: ${event.reason.message}`
          : String(event.reason);
        globalThis.__NANTAI_UNHANDLED_REJECTIONS__.push(reason.slice(0, 500));
      });
    });
    const page = await context.newPage();
    const consoleErrors = [];
    page.on('console', (message) => {
      if (
        message.type() === 'error'
        && !isGenericResourceConsoleNoise(message.text())
      ) {
        consoleErrors.push(message.text());
      }
    });
    page.on('pageerror', (error) => {
      consoleErrors.push(`pageerror: ${error.name}: ${error.message}`);
    });
    page.on('response', (response) => {
      const failure = httpFailureMessage({
        studioUrl: contract.studioUrl,
        method: response.request().method(),
        requestUrl: response.url(),
        status: response.status(),
      });
      if (failure) consoleErrors.push(failure);
    });
    page.on('requestfailed', (request) => {
      const failure = requestFailureMessage({
        studioUrl: contract.studioUrl,
        method: request.method(),
        requestUrl: request.url(),
        errorText: request.failure()?.errorText,
      });
      if (failure) consoleErrors.push(failure);
    });

    const cdp = await context.newCDPSession(page);
    await cdp.send('Network.enable');
    await cdp.send('Network.clearBrowserCache');
    await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
    const captureUrl = new URL(contract.studioUrl.href);
    captureUrl.searchParams.set('viewerPresentation', 'points');
    await page.goto(captureUrl.href, {
      waitUntil: 'domcontentloaded',
      timeout: 30_000,
    });
    await page.waitForFunction(
      () => {
        const frame = document.getElementById('viewer-frame');
        const reset = document.getElementById('reset-camera');
        return Boolean(frame?.contentWindow && reset && !reset.disabled);
      },
      undefined,
      { timeout: 30_000 },
    );
    const frameHandle = await page.locator('#viewer-frame').elementHandle();
    const viewerFrame = await frameHandle?.contentFrame();
    if (!viewerFrame) throw new Error('Studio viewer frame did not attach');
    await viewerFrame.waitForFunction(
      () => Boolean(window.__NANTAI_VIEWER_ACCEPTANCE__),
      undefined,
      { timeout: 30_000 },
    );
    await selectAcceptancePresentation(
      page,
      viewerFrame,
      contract.policy.maximum_interactive_ms,
    );

    const runtime = await runtimeIdentity(viewerFrame, browser.version());
    const poseSnapshots = [];
    for (const pose of contract.cameraSet.poses) {
      poseSnapshots.push(await measurePose({
        page,
        viewerFrame,
        pose,
        interactiveTimeoutMs: contract.policy.maximum_interactive_ms,
        measurementTimeoutMs,
      }));
    }
    const report = buildViewerPerformanceReport({
      contract,
      sceneManifestSha256,
      runtime,
      poseSnapshots,
      consoleErrors,
      unhandledRejections: await collectUnhandledRejections(page),
    });
    await context.close();
    return report;
  } finally {
    await browser.close();
  }
}

function parsedArgs(argv) {
  const options = {
    headless: false,
    measurementTimeoutMs: 120_000,
    python: existsSync('.venv/bin/python')
      ? '.venv/bin/python'
      : 'python',
  };
  const valueFlags = new Map([
    ['--policy', 'policy'],
    ['--camera-set', 'cameraSet'],
    ['--studio-url', 'studioUrl'],
    ['--scene-manifest', 'sceneManifest'],
    ['--output', 'output'],
    ['--decision', 'decision'],
    ['--source-role', 'sourceRole'],
    ['--python', 'python'],
    ['--measurement-timeout-ms', 'measurementTimeoutMs'],
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === '--headless') {
      options.headless = true;
      continue;
    }
    const key = valueFlags.get(flag);
    if (!key || index + 1 >= argv.length) {
      throw new TypeError(`unsupported or incomplete argument: ${flag}`);
    }
    options[key] = argv[index + 1];
    index += 1;
  }
  for (const key of [
    'policy',
    'cameraSet',
    'studioUrl',
    'sceneManifest',
    'output',
    'sourceRole',
  ]) {
    if (!options[key]) throw new TypeError(`missing required argument: ${key}`);
  }
  options.measurementTimeoutMs = Number(options.measurementTimeoutMs);
  if (
    !Number.isSafeInteger(options.measurementTimeoutMs)
    || options.measurementTimeoutMs <= 0
  ) {
    throw new TypeError('measurement timeout must be a positive integer');
  }
  options.decision ??= `${options.output}.decision.json`;
  return options;
}

async function readJson(filePath, label) {
  try {
    return JSON.parse(await readFile(filePath, 'utf8'));
  } catch (error) {
    throw new Error(`${label} is unreadable: ${error.message}`);
  }
}

async function runValidator({ python, policy, report, decision }) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, [
      '-m',
      'pipeline.viewer_acceptance',
      '--policy',
      policy,
      '--report',
      report,
      '--decision',
      decision,
    ], { stdio: 'inherit' });
    child.once('error', reject);
    child.once('exit', (code, signal) => {
      if (signal) {
        reject(new Error(`viewer validator terminated by ${signal}`));
      } else {
        resolve(code ?? 2);
      }
    });
  });
}

export async function main(argv = process.argv.slice(2)) {
  try {
    const args = parsedArgs(argv);
    if (existsSync(args.output) || existsSync(args.decision)) {
      throw new Error('output or decision path already exists');
    }
    const [policy, cameraSet, sceneManifestBytes] = await Promise.all([
      readJson(args.policy, 'viewer policy'),
      readJson(args.cameraSet, 'camera set'),
      readFile(args.sceneManifest),
    ]);
    const contract = validateCaptureContract({
      policy,
      cameraSet,
      studioUrl: args.studioUrl,
      sourceRole: args.sourceRole,
    });
    const sceneManifestSha256 = createHash('sha256')
      .update(sceneManifestBytes)
      .digest('hex');
    const report = await captureViewerEvidence({
      contract,
      sceneManifestSha256,
      headless: args.headless,
      measurementTimeoutMs: args.measurementTimeoutMs,
    });
    await writeFile(
      args.output,
      `${canonicalJson(report)}\n`,
      { encoding: 'utf8', flag: 'wx' },
    );
    console.log(`Viewer evidence: ${path.resolve(args.output)}`);
    return runValidator({
      python: args.python,
      policy: args.policy,
      report: args.output,
      decision: args.decision,
    });
  } catch (error) {
    console.error(`Viewer acceptance capture failed: ${error.message}`);
    return 2;
  }
}

if (
  process.argv[1]
  && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href
) {
  process.exitCode = await main();
}
