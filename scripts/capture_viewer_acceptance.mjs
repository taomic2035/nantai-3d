#!/usr/bin/env node

import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { constants as fsConstants, existsSync } from 'node:fs';
import {
  lstat,
  link,
  mkdir,
  open,
  readFile,
  realpath,
  unlink,
  writeFile,
} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { chromium } from 'playwright';
import playwrightPackage from 'playwright/package.json' with { type: 'json' };

const SHA256 = /^[0-9a-f]{64}$/;
const POSE_ID = /^pose-[0-9a-f]{64}$/;
const CAMERA_POSE_SCHEMA = 'nantai.viewer-camera-pose.v1';
const CAMERA_SET_SCHEMA = 'nantai.viewer-camera-set.v1';
const POLICY_SCHEMA = 'nantai.viewer-performance-policy.v1';
const REPORT_SCHEMA = 'nantai.viewer-performance-report.v1';
const REPORT_SCHEMA_V2 = 'nantai.viewer-performance-report.v2';
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
  return `pose-${createHash('sha256')
    .update(canonicalJson(numericHashProjection(payload)))
    .digest('hex')}`;
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

export function assertCanonicalPolicyBytes(value, bytes) {
  const policy = validatedPolicy(value);
  const pythonFloat = (number) => (
    Number.isInteger(number) ? `${number}.0` : String(number)
  );
  const expected = Buffer.from([
    `{"allow_software_renderer":${policy.allow_software_renderer}`,
    `,"maximum_interactive_ms":${pythonFloat(policy.maximum_interactive_ms)}`,
    `,"maximum_p50_frame_ms":${pythonFloat(policy.maximum_p50_frame_ms)}`,
    `,"maximum_p95_frame_ms":${pythonFloat(policy.maximum_p95_frame_ms)}`,
    `,"maximum_worst_frame_ms":${pythonFloat(policy.maximum_worst_frame_ms)}`,
    `,"measured_frame_count":${policy.measured_frame_count}`,
    `,"percentile_method":${JSON.stringify(policy.percentile_method)}`,
    `,"required_http_cache":${JSON.stringify(policy.required_http_cache)}`,
    `,"required_pose_ids":${JSON.stringify(policy.required_pose_ids)}`,
    `,"required_representation":${JSON.stringify(policy.required_representation)}`,
    `,"schema":${JSON.stringify(policy.schema)}`,
    `,"viewport_height":${policy.viewport_height}`,
    `,"viewport_width":${policy.viewport_width}`,
    `,"warmup_frame_count":${policy.warmup_frame_count}}\n`,
  ].join(''));
  if (!Buffer.from(bytes).equals(expected)) {
    throw new TypeError('viewer policy must be canonical JSON');
  }
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

function validatedArtifactBinding(binding, label) {
  plainRecord(binding, label);
  exactKeys(binding, ['byte_length', 'path', 'sha256'], label);
  if (
    typeof binding.path !== 'string'
    || binding.path.length === 0
    || binding.path.length > 240
    || binding.path.includes('\\')
    || binding.path.includes('\0')
    || path.posix.isAbsolute(binding.path)
    || path.posix.normalize(binding.path) !== binding.path
    || binding.path.split('/').some((part) => ['', '.', '..'].includes(part))
  ) {
    throw new TypeError(`${label} path must be portable and relative`);
  }
  if (!SHA256.test(binding.sha256)) {
    throw new TypeError(`${label} SHA-256 is invalid`);
  }
  if (
    !Number.isSafeInteger(binding.byte_length)
    || binding.byte_length <= 0
    || binding.byte_length > 100 * 1024 * 1024
  ) {
    throw new TypeError(`${label} byte length is invalid`);
  }
  return { ...binding };
}

export function verifyServedCaptureArtifact(binding, payload, label) {
  const expected = validatedArtifactBinding(binding, label);
  const bytes = Buffer.from(payload);
  if (
    bytes.byteLength !== expected.byte_length
    || createHash('sha256').update(bytes).digest('hex') !== expected.sha256
  ) {
    throw new TypeError(`${label} served bytes differ from capture binding`);
  }
}

function validatedExecutableSnapshot(snapshot, label) {
  plainRecord(snapshot, label);
  exactKeys(
    snapshot,
    [
      'byte_length',
      'device_id',
      'executable',
      'file_id',
      'mode',
      'mtime_ns',
      'sha256',
    ],
    label,
  );
  if (
    !SHA256.test(snapshot.sha256)
    || !Number.isSafeInteger(snapshot.byte_length)
    || snapshot.byte_length <= 0
    || snapshot.byte_length > 4 * 1024 * 1024 * 1024
    || !/^[0-9]+$/.test(snapshot.device_id)
    || !/^[0-9]+$/.test(snapshot.file_id)
    || !/^[0-9]+$/.test(snapshot.mtime_ns)
    || !Number.isSafeInteger(snapshot.mode)
    || snapshot.mode < 0
    || (snapshot.mode & 0o170000) !== 0o100000
    || snapshot.executable !== true
  ) {
    throw new TypeError(`${label} is not a regular executable identity`);
  }
  return { ...snapshot };
}

function validatedStableExecutable(observation, role) {
  plainRecord(observation, `${role} executable`);
  exactKeys(observation, ['after', 'before', 'role'], `${role} executable`);
  if (observation.role !== role) {
    throw new TypeError(`${role} executable role differs`);
  }
  const before = validatedExecutableSnapshot(
    observation.before,
    `${role} executable before`,
  );
  const after = validatedExecutableSnapshot(
    observation.after,
    `${role} executable after`,
  );
  if (canonicalJson(before) !== canonicalJson(after)) {
    throw new TypeError(`${role} executable changed during Viewer capture`);
  }
  return { role, before, after };
}

function numericHashProjection(value) {
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new TypeError('Viewer v2 report contains a non-finite number');
    }
    const bytes = new ArrayBuffer(8);
    new DataView(bytes).setFloat64(0, value, false);
    return { $f64: Buffer.from(bytes).toString('hex') };
  }
  if (
    value === null
    || typeof value === 'string'
    || typeof value === 'boolean'
  ) return value;
  if (Array.isArray(value)) {
    return value.map((item) => numericHashProjection(item));
  }
  plainRecord(value, 'Viewer v2 report hash value');
  return Object.fromEntries(
    Object.entries(value).map(
      ([key, item]) => [key, numericHashProjection(item)],
    ),
  );
}

export function viewerReportV2ContentSha(report) {
  plainRecord(report, 'Viewer v2 report');
  const payload = structuredClone(report);
  delete payload.report_id;
  delete payload.content_sha256;
  return createHash('sha256')
    .update(canonicalJson(numericHashProjection(payload)))
    .digest('hex');
}

export function buildViewerPerformanceReportV2({
  baseReport,
  sceneManifest,
  viewerPolicy,
  cameraSet,
  captureScript,
  probeModule,
  playwrightPackage,
  nodeExecutable,
  browserExecutable,
  screenshots,
}) {
  plainRecord(baseReport, 'Viewer v1 base report');
  if (
    baseReport.schema !== REPORT_SCHEMA
    || baseReport.source_role !== 'production-acceptance'
  ) {
    throw new TypeError(
      'Viewer v2 requires a production-acceptance v1 base report',
    );
  }
  const bindings = {
    scene_manifest: validatedArtifactBinding(
      sceneManifest,
      'scene manifest',
    ),
    viewer_policy: validatedArtifactBinding(
      viewerPolicy,
      'viewer policy',
    ),
    camera_set: validatedArtifactBinding(cameraSet, 'camera set'),
    capture_script: validatedArtifactBinding(
      captureScript,
      'capture script',
    ),
    probe_module: validatedArtifactBinding(probeModule, 'probe module'),
    playwright_package: validatedArtifactBinding(
      playwrightPackage,
      'Playwright package',
    ),
  };
  if (baseReport.scene_manifest_sha256 !== bindings.scene_manifest.sha256) {
    throw new TypeError('scene manifest binding differs from base report');
  }
  if (
    !Array.isArray(screenshots)
    || screenshots.length !== baseReport.poses.length
  ) {
    throw new TypeError('Viewer v2 screenshot set is incomplete');
  }
  const screenshotBindings = screenshots.map((row, index) => {
    plainRecord(row, `Viewer screenshot ${index}`);
    exactKeys(
      row,
      ['byte_length', 'path', 'pose_id', 'sha256'],
      `Viewer screenshot ${index}`,
    );
    if (row.pose_id !== baseReport.poses[index].pose_id) {
      throw new TypeError(
        'Viewer v2 screenshot pose order differs from measurements',
      );
    }
    return {
      pose_id: row.pose_id,
      ...validatedArtifactBinding(
        {
          path: row.path,
          sha256: row.sha256,
          byte_length: row.byte_length,
        },
        `Viewer screenshot ${index}`,
      ),
    };
  });
  const paths = [
    ...Object.values(bindings).map((binding) => binding.path),
    ...screenshotBindings.map((binding) => binding.path),
  ];
  if (new Set(paths).size !== paths.length) {
    throw new TypeError('Viewer v2 capture paths must be unique');
  }
  const { schema: _schema, ...baseFields } = structuredClone(baseReport);
  const zero = '0'.repeat(64);
  const draft = {
    ...baseFields,
    schema: REPORT_SCHEMA_V2,
    report_id: `viewer-capture-${zero}`,
    content_sha256: zero,
    ...bindings,
    node_executable: validatedStableExecutable(nodeExecutable, 'node'),
    browser_executable: validatedStableExecutable(
      browserExecutable,
      'browser',
    ),
    screenshots: screenshotBindings,
  };
  const digest = viewerReportV2ContentSha(draft);
  return {
    ...draft,
    report_id: `viewer-capture-${digest}`,
    content_sha256: digest,
  };
}

function portableEvidencePath(value, label) {
  if (
    typeof value !== 'string'
    || value.length === 0
    || value.length > 240
    || value.includes('\\')
    || value.includes('\0')
    || path.posix.isAbsolute(value)
    || path.posix.normalize(value) !== value
    || value.split('/').some((part) => ['', '.', '..'].includes(part))
  ) {
    throw new TypeError(`${label} must be a portable relative path`);
  }
  return value;
}

async function realEvidenceRoot(evidenceRoot) {
  const resolved = await realpath(evidenceRoot);
  const inspected = await lstat(resolved, { bigint: true });
  if (!inspected.isDirectory() || inspected.isSymbolicLink()) {
    throw new TypeError('evidence root must be a real directory');
  }
  return resolved;
}

async function rejectSymlinkComponents(root, relative, label) {
  let current = root;
  for (const part of relative.split('/')) {
    current = path.join(current, part);
    const inspected = await lstat(current, { bigint: true });
    if (inspected.isSymbolicLink()) {
      throw new TypeError(`${label} must not traverse a symlink`);
    }
  }
  return current;
}

function descriptorIdentity(inspected) {
  return [
    inspected.dev.toString(),
    inspected.ino.toString(),
    inspected.size.toString(),
    inspected.mtimeNs.toString(),
    inspected.mode.toString(),
  ].join(':');
}

function namespaceIdentity(inspected) {
  return [
    inspected.ino.toString(),
    inspected.size.toString(),
    inspected.mtimeNs.toString(),
    inspected.mode.toString(),
  ].join(':');
}

async function readStableFile(
  filePath,
  maximumBytes,
  label,
  { retainPayload = true } = {},
) {
  const flags = fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW ?? 0);
  const handle = await open(filePath, flags);
  try {
    const before = await handle.stat({ bigint: true });
    if (
      !before.isFile()
      || before.size <= 0n
      || before.size > BigInt(maximumBytes)
    ) {
      throw new TypeError(`${label} size or file type is invalid`);
    }
    const byteLength = Number(before.size);
    const payload = retainPayload
      ? Buffer.allocUnsafe(byteLength)
      : undefined;
    const scratch = retainPayload
      ? undefined
      : Buffer.allocUnsafe(Math.min(1024 * 1024, byteLength));
    const hasher = createHash('sha256');
    let offset = 0;
    while (offset < byteLength) {
      const target = payload ?? scratch;
      const targetOffset = payload ? offset : 0;
      const requested = Math.min(
        target.byteLength - targetOffset,
        byteLength - offset,
      );
      const { bytesRead } = await handle.read(
        target,
        targetOffset,
        requested,
        offset,
      );
      if (bytesRead === 0) break;
      hasher.update(
        target.subarray(targetOffset, targetOffset + bytesRead),
      );
      offset += bytesRead;
    }
    const after = await handle.stat({ bigint: true });
    const final = await lstat(filePath, { bigint: true });
    if (
      offset !== byteLength
      || descriptorIdentity(before) !== descriptorIdentity(after)
      || namespaceIdentity(after) !== namespaceIdentity(final)
    ) {
      throw new TypeError(`${label} changed while being read`);
    }
    return {
      payload,
      sha256: hasher.digest('hex'),
      byteLength,
      inspected: after,
    };
  } finally {
    await handle.close();
  }
}

export async function relativeEvidencePath(evidenceRoot, filePath) {
  const root = await realEvidenceRoot(evidenceRoot);
  const sourceLstat = await lstat(filePath, { bigint: true });
  if (sourceLstat.isSymbolicLink()) {
    throw new TypeError('evidence input must not be a symlink');
  }
  const resolved = await realpath(filePath);
  const relative = path.relative(root, resolved);
  if (
    relative === ''
    || relative.startsWith(`..${path.sep}`)
    || relative === '..'
    || path.isAbsolute(relative)
  ) {
    throw new TypeError('evidence input must stay inside the evidence root');
  }
  const portable = portableEvidencePath(
    relative.split(path.sep).join('/'),
    'evidence input path',
  );
  await rejectSymlinkComponents(root, portable, 'evidence input');
  return portable;
}

export async function artifactBindingFromFile(
  evidenceRoot,
  relativePath,
) {
  const portable = portableEvidencePath(
    relativePath,
    'capture artifact path',
  );
  const root = await realEvidenceRoot(evidenceRoot);
  const filePath = await rejectSymlinkComponents(
    root,
    portable,
    'capture artifact',
  );
  const { sha256, byteLength } = await readStableFile(
    filePath,
    100 * 1024 * 1024,
    'capture artifact',
  );
  return {
    path: portable,
    sha256,
    byte_length: byteLength,
  };
}

export async function materializeCaptureInput({
  evidenceRoot,
  relativePath,
  payload,
}) {
  const portable = portableEvidencePath(
    relativePath,
    'capture materialization path',
  );
  const root = await realEvidenceRoot(evidenceRoot);
  const destination = path.join(root, ...portable.split('/'));
  await mkdir(path.dirname(destination), { recursive: true });
  try {
    await writeFile(destination, payload, { flag: 'wx' });
  } catch (error) {
    if (error.code === 'EEXIST') {
      throw new TypeError(
        `capture materialization already exists: ${portable}`,
      );
    }
    try {
      await unlink(destination);
    } catch {
      // A failed create may not have published a directory entry.
    }
    throw error;
  }
  return artifactBindingFromFile(root, portable);
}

export async function executableSnapshotFromFile(filePath) {
  const inspected = await lstat(filePath, { bigint: true });
  if (inspected.isSymbolicLink()) {
    throw new TypeError('Viewer executable must not be a symlink');
  }
  const resolved = await realpath(filePath);
  const {
    sha256,
    byteLength,
    inspected: stable,
  } = await readStableFile(
    resolved,
    4 * 1024 * 1024 * 1024,
    'Viewer executable',
    { retainPayload: false },
  );
  const windowsExecutable = (
    process.platform === 'win32'
    && /\.(?:exe|com|bat|cmd)$/i.test(resolved)
  );
  if (
    !stable.isFile()
    || (!windowsExecutable && (Number(stable.mode) & 0o111) === 0)
  ) {
    throw new TypeError('Viewer executable identity is not executable');
  }
  return {
    sha256,
    byte_length: byteLength,
    device_id: stable.dev.toString(),
    file_id: stable.ino.toString(),
    mtime_ns: stable.mtimeNs.toString(),
    mode: Number(stable.mode),
    executable: true,
  };
}

async function stableAbsolutePayload(filePath, maximumBytes, label) {
  const inspected = await lstat(filePath, { bigint: true });
  if (inspected.isSymbolicLink()) {
    throw new TypeError(`${label} must not be a symlink`);
  }
  const resolved = await realpath(filePath);
  const { payload } = await readStableFile(
    resolved,
    maximumBytes,
    label,
  );
  return payload;
}

export async function prepareProductionCaptureEvidence({
  evidenceRoot,
  policyPath,
  cameraSetPath,
  sceneManifestPath,
  captureScriptPath,
  probeModulePath,
  playwrightPackagePath,
  nodePath,
  browserPath,
}) {
  const root = await realEvidenceRoot(evidenceRoot);
  const [
    viewerPolicyRelative,
    cameraSetRelative,
    sceneManifestRelative,
    captureScriptPayload,
    probeModulePayload,
    playwrightPackagePayload,
  ] = await Promise.all([
    relativeEvidencePath(root, policyPath),
    relativeEvidencePath(root, cameraSetPath),
    relativeEvidencePath(root, sceneManifestPath),
    stableAbsolutePayload(
      captureScriptPath,
      16 * 1024 * 1024,
      'capture script',
    ),
    stableAbsolutePayload(
      probeModulePath,
      16 * 1024 * 1024,
      'Viewer probe module',
    ),
    stableAbsolutePayload(
      playwrightPackagePath,
      1024 * 1024,
      'Playwright package metadata',
    ),
  ]);
  const [
    viewerPolicy,
    cameraSetBinding,
    sceneManifest,
    captureScript,
    probeModule,
    playwrightPackageBinding,
    nodeExecutableBefore,
    browserExecutableBefore,
  ] = await Promise.all([
    artifactBindingFromFile(root, viewerPolicyRelative),
    artifactBindingFromFile(root, cameraSetRelative),
    artifactBindingFromFile(root, sceneManifestRelative),
    materializeCaptureInput({
      evidenceRoot: root,
      relativePath:
        'viewer/capture-inputs/capture_viewer_acceptance.mjs',
      payload: captureScriptPayload,
    }),
    materializeCaptureInput({
      evidenceRoot: root,
      relativePath: 'viewer/capture-inputs/acceptance-probe.mjs',
      payload: probeModulePayload,
    }),
    materializeCaptureInput({
      evidenceRoot: root,
      relativePath: 'viewer/capture-inputs/playwright-package.json',
      payload: playwrightPackagePayload,
    }),
    executableSnapshotFromFile(nodePath),
    executableSnapshotFromFile(browserPath),
  ]);
  return Object.freeze({
    evidenceRoot: root,
    nodePath,
    browserPath,
    sceneManifest,
    viewerPolicy,
    cameraSet: cameraSetBinding,
    captureScript,
    probeModule,
    playwrightPackage: playwrightPackageBinding,
    nodeExecutableBefore,
    browserExecutableBefore,
  });
}

export async function finalizeProductionCaptureEvidence({
  context,
  baseReport,
  screenshots,
}) {
  const names = [
    'sceneManifest',
    'viewerPolicy',
    'cameraSet',
    'captureScript',
    'probeModule',
    'playwrightPackage',
  ];
  const rebound = await Promise.all(
    names.map((name) => artifactBindingFromFile(
      context.evidenceRoot,
      context[name].path,
    )),
  );
  for (let index = 0; index < names.length; index += 1) {
    if (canonicalJson(rebound[index]) !== canonicalJson(context[names[index]])) {
      throw new TypeError(
        `${names[index]} changed during Viewer capture`,
      );
    }
  }
  const [nodeAfter, browserAfter] = await Promise.all([
    executableSnapshotFromFile(context.nodePath),
    executableSnapshotFromFile(context.browserPath),
  ]);
  if (
    canonicalJson(nodeAfter)
    !== canonicalJson(context.nodeExecutableBefore)
  ) {
    throw new TypeError('node executable changed during Viewer capture');
  }
  if (
    canonicalJson(browserAfter)
    !== canonicalJson(context.browserExecutableBefore)
  ) {
    throw new TypeError('browser executable changed during Viewer capture');
  }
  return buildViewerPerformanceReportV2({
    baseReport,
    sceneManifest: context.sceneManifest,
    viewerPolicy: context.viewerPolicy,
    cameraSet: context.cameraSet,
    captureScript: context.captureScript,
    probeModule: context.probeModule,
    playwrightPackage: context.playwrightPackage,
    nodeExecutable: {
      role: 'node',
      before: context.nodeExecutableBefore,
      after: nodeAfter,
    },
    browserExecutable: {
      role: 'browser',
      before: context.browserExecutableBefore,
      after: browserAfter,
    },
    screenshots,
  });
}

export async function capturePoseScreenshot({
  target,
  evidenceRoot,
  poseId,
}) {
  if (!POSE_ID.test(poseId)) {
    throw new TypeError('Viewer screenshot pose id is invalid');
  }
  const root = await realEvidenceRoot(evidenceRoot);
  const relative = `viewer/screenshots/${poseId}.png`;
  const destination = path.join(root, ...relative.split('/'));
  await mkdir(path.dirname(destination), { recursive: true });
  const staging = path.join(
    path.dirname(destination),
    `.${poseId}.${randomUUID()}.staging.png`,
  );
  try {
    await target.screenshot({
      path: staging,
      type: 'png',
    });
    try {
      await link(staging, destination);
    } catch (error) {
      if (error.code === 'EEXIST') {
        throw new TypeError(
          `Viewer screenshot already exists: ${relative}`,
        );
      }
      throw error;
    }
  } finally {
    try {
      await unlink(staging);
    } catch {
      // The screenshot producer may have failed before publishing staging.
    }
  }
  const binding = await artifactBindingFromFile(root, relative);
  return { pose_id: poseId, ...binding };
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

function waitForCaptureArtifactResponse(page, studioUrl, pathname) {
  return page.waitForResponse((response) => {
    let responseUrl;
    try {
      responseUrl = new URL(response.url());
    } catch {
      return false;
    }
    return (
      response.request().method() === 'GET'
      && response.status() === 200
      && responseUrl.origin === studioUrl.origin
      && responseUrl.pathname === pathname
      && responseUrl.search === ''
    );
  }, { timeout: 30_000 });
}

export async function captureViewerEvidence({
  contract,
  sceneManifestSha256,
  headless = false,
  measurementTimeoutMs = 120_000,
  productionContext = null,
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
    const servedArtifactResponses = productionContext
      ? Promise.all([
        waitForCaptureArtifactResponse(
          page,
          contract.studioUrl,
          '/web/data/recon/recon_manifest.json',
        ),
        waitForCaptureArtifactResponse(
          page,
          contract.studioUrl,
          '/web/viewer/acceptance-probe.mjs',
        ),
      ])
      : null;
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
    if (servedArtifactResponses) {
      const [sceneResponse, probeResponse] = await servedArtifactResponses;
      const [servedScene, servedProbe] = await Promise.all([
        sceneResponse.body(),
        probeResponse.body(),
      ]);
      verifyServedCaptureArtifact(
        productionContext.sceneManifest,
        servedScene,
        'scene manifest',
      );
      verifyServedCaptureArtifact(
        productionContext.probeModule,
        servedProbe,
        'Viewer probe module',
      );
    }
    await selectAcceptancePresentation(
      page,
      viewerFrame,
      contract.policy.maximum_interactive_ms,
    );

    const runtime = await runtimeIdentity(viewerFrame, browser.version());
    const poseSnapshots = [];
    const screenshots = [];
    const screenshotTarget = page.locator('#viewer-frame');
    for (const pose of contract.cameraSet.poses) {
      poseSnapshots.push(await measurePose({
        page,
        viewerFrame,
        pose,
        interactiveTimeoutMs: contract.policy.maximum_interactive_ms,
        measurementTimeoutMs,
      }));
      if (productionContext) {
        screenshots.push(await capturePoseScreenshot({
          target: screenshotTarget,
          evidenceRoot: productionContext.evidenceRoot,
          poseId: pose.pose_id,
        }));
      }
    }
    const baseReport = buildViewerPerformanceReport({
      contract,
      sceneManifestSha256,
      runtime,
      poseSnapshots,
      consoleErrors,
      unhandledRejections: await collectUnhandledRejections(page),
    });
    await context.close();
    if (!productionContext) return baseReport;
    return finalizeProductionCaptureEvidence({
      context: productionContext,
      baseReport,
      screenshots,
    });
  } finally {
    await browser.close();
  }
}

export function parseCaptureArgs(argv) {
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
    ['--evidence-root', 'evidenceRoot'],
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
  if (
    options.sourceRole === 'production-acceptance'
    && !options.evidenceRoot
  ) {
    throw new TypeError(
      'production capture requires an explicit evidence root',
    );
  }
  return options;
}

export function viewerValidatorArgs({
  policy,
  report,
  decision,
  sourceRole,
  evidenceRoot,
}) {
  const args = [
    '-m',
    'pipeline.viewer_acceptance',
    '--policy',
    policy,
    '--report',
    report,
    '--decision',
    decision,
  ];
  if (sourceRole === 'production-acceptance') {
    if (!evidenceRoot) {
      throw new TypeError(
        'production validator requires an evidence root',
      );
    }
    args.push('--evidence-root', evidenceRoot);
  }
  return args;
}

async function runValidator({
  python,
  policy,
  report,
  decision,
  sourceRole,
  evidenceRoot,
}) {
  return new Promise((resolve, reject) => {
    const child = spawn(python, viewerValidatorArgs({
      policy,
      report,
      decision,
      sourceRole,
      evidenceRoot,
    }), { stdio: 'inherit' });
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
    const args = parseCaptureArgs(argv);
    if (existsSync(args.output) || existsSync(args.decision)) {
      throw new Error('output or decision path already exists');
    }
    const [policyBytes, cameraSetBytes, sceneManifestBytes] = await Promise.all([
      readFile(args.policy),
      readFile(args.cameraSet),
      readFile(args.sceneManifest),
    ]);
    const policy = JSON.parse(policyBytes.toString('utf8'));
    const cameraSet = JSON.parse(cameraSetBytes.toString('utf8'));
    const contract = validateCaptureContract({
      policy,
      cameraSet,
      studioUrl: args.studioUrl,
      sourceRole: args.sourceRole,
    });
    const sceneManifestSha256 = createHash('sha256')
      .update(sceneManifestBytes)
      .digest('hex');
    let productionContext = null;
    if (contract.sourceRole === 'production-acceptance') {
      assertCanonicalPolicyBytes(policy, policyBytes);
      const captureScriptPath = fileURLToPath(import.meta.url);
      const scriptDirectory = path.dirname(captureScriptPath);
      productionContext = await prepareProductionCaptureEvidence({
        evidenceRoot: args.evidenceRoot,
        policyPath: path.resolve(args.policy),
        cameraSetPath: path.resolve(args.cameraSet),
        sceneManifestPath: path.resolve(args.sceneManifest),
        captureScriptPath,
        probeModulePath: path.resolve(
          scriptDirectory,
          '../web/viewer/acceptance-probe.mjs',
        ),
        playwrightPackagePath: fileURLToPath(
          import.meta.resolve('playwright/package.json'),
        ),
        nodePath: process.execPath,
        browserPath: chromium.executablePath(),
      });
      if (
        productionContext.viewerPolicy.byte_length
          !== policyBytes.byteLength
        || productionContext.viewerPolicy.sha256
          !== createHash('sha256').update(policyBytes).digest('hex')
      ) {
        throw new Error(
          'production viewer policy must be canonical JSON',
        );
      }
    }
    const report = await captureViewerEvidence({
      contract,
      sceneManifestSha256,
      headless: args.headless,
      measurementTimeoutMs: args.measurementTimeoutMs,
      productionContext,
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
      sourceRole: args.sourceRole,
      evidenceRoot: args.evidenceRoot,
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
