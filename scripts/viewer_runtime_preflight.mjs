#!/usr/bin/env node

import { createHash, randomUUID } from 'node:crypto';
import { link, open, unlink } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { pathToFileURL } from 'node:url';

const REPORT_SCHEMA = 'nantai.viewer-runtime-preflight.v1';
const MINIMUM_NODE_MAJOR = 22;
const SHA256 = /^[0-9a-f]{64}$/;

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

function failedReport(nodeVersion, failureCodes) {
  return {
    schema: REPORT_SCHEMA,
    status: 'failed',
    failure_codes: [...failureCodes],
    node: {
      minimum_major: MINIMUM_NODE_MAJOR,
      version: nodeVersion,
    },
    playwright: {
      version: null,
    },
    chromium: {
      executable: null,
      launch_succeeded: false,
      browser_version: null,
      close_succeeded: false,
    },
  };
}

async function defaultLoadPlaywright() {
  return import('playwright');
}

async function defaultLoadPlaywrightVersion() {
  const packageModule = await import(
    'playwright/package.json',
    { with: { type: 'json' } }
  );
  return packageModule.default?.version;
}

async function defaultInspectExecutable(executablePath) {
  const handle = await open(executablePath, 'r');
  try {
    const file = await handle.stat();
    if (!file.isFile()) {
      const error = new Error('Chromium executable is not a file');
      error.code = 'ENOENT';
      throw error;
    }
    const hash = createHash('sha256');
    const stream = handle.createReadStream({ autoClose: false, start: 0 });
    for await (const chunk of stream) {
      hash.update(chunk);
    }
    return {
      size: file.size,
      sha256: hash.digest('hex'),
    };
  } finally {
    await handle.close();
  }
}

function nodeMajor(version) {
  const major = Number.parseInt(String(version).split('.')[0], 10);
  return Number.isSafeInteger(major) ? major : null;
}

export async function probeViewerRuntime({
  nodeVersion = process.versions.node,
  loadPlaywright = defaultLoadPlaywright,
  loadPlaywrightVersion = defaultLoadPlaywrightVersion,
  inspectExecutable = defaultInspectExecutable,
} = {}) {
  const report = failedReport(String(nodeVersion), []);
  const failures = report.failure_codes;

  if ((nodeMajor(nodeVersion) ?? -1) < MINIMUM_NODE_MAJOR) {
    failures.push('NODE_VERSION_UNSUPPORTED');
  }

  let playwright;
  try {
    playwright = await loadPlaywright();
  } catch {
    failures.push('PLAYWRIGHT_PACKAGE_MISSING');
    return report;
  }

  try {
    const version = await loadPlaywrightVersion();
    if (typeof version !== 'string' || version.trim() === '') {
      throw new TypeError('Playwright version is empty');
    }
    report.playwright.version = version;
  } catch {
    failures.push('PLAYWRIGHT_VERSION_UNAVAILABLE');
  }

  let executablePath;
  try {
    executablePath = playwright.chromium.executablePath();
    if (typeof executablePath !== 'string' || executablePath === '') {
      throw new TypeError('Chromium executable path is empty');
    }
    report.chromium.executable = {
      path: executablePath,
      sha256: null,
      size: null,
    };
  } catch {
    failures.push('CHROMIUM_EXECUTABLE_UNAVAILABLE');
    return report;
  }

  try {
    const binding = await inspectExecutable(executablePath);
    if (
      !Number.isSafeInteger(binding.size)
      || binding.size <= 0
      || !SHA256.test(binding.sha256)
    ) {
      failures.push('CHROMIUM_BINARY_INVALID');
      return report;
    }
    report.chromium.executable.size = binding.size;
    report.chromium.executable.sha256 = binding.sha256;
  } catch (error) {
    failures.push(
      error?.code === 'ENOENT'
        ? 'CHROMIUM_BINARY_MISSING'
        : 'CHROMIUM_BINARY_HASH_FAILED',
    );
    return report;
  }

  if (failures.length !== 0) {
    return report;
  }

  let browser;
  try {
    browser = await playwright.chromium.launch({
      executablePath,
      headless: true,
    });
    report.chromium.launch_succeeded = true;
  } catch {
    failures.push('CHROMIUM_LAUNCH_FAILED');
    return report;
  }

  try {
    const version = browser.version();
    if (typeof version !== 'string' || version.trim() === '') {
      throw new TypeError('Chromium browser version is empty');
    }
    report.chromium.browser_version = version;
  } catch {
    failures.push('CHROMIUM_VERSION_UNAVAILABLE');
  }

  try {
    await browser.close();
    report.chromium.close_succeeded = true;
  } catch {
    failures.push('CHROMIUM_CLOSE_FAILED');
  }

  try {
    const rebound = await inspectExecutable(executablePath);
    if (
      !Number.isSafeInteger(rebound.size)
      || rebound.size <= 0
      || !SHA256.test(rebound.sha256)
      || rebound.size !== report.chromium.executable.size
      || rebound.sha256 !== report.chromium.executable.sha256
    ) {
      failures.push('CHROMIUM_BINARY_DRIFT');
    }
  } catch {
    failures.push('CHROMIUM_BINARY_REVERIFY_FAILED');
  }

  if (failures.length === 0) {
    report.status = 'ready';
  }
  return report;
}

function parseArgs(argv) {
  if (argv.length === 0) {
    return { output: null };
  }
  if (
    argv.length === 2
    && argv[0] === '--output'
    && typeof argv[1] === 'string'
    && argv[1] !== ''
  ) {
    return { output: argv[1] };
  }
  throw new TypeError('expected optional --output PATH');
}

function withFailure(report, code) {
  return {
    ...report,
    status: 'failed',
    failure_codes: [...report.failure_codes, code],
  };
}

export async function publishReport(
  outputPath,
  bytes,
  {
    randomId = randomUUID,
    openFile = open,
    linkFile = link,
    unlinkFile = unlink,
  } = {},
) {
  const finalPath = path.resolve(outputPath);
  const tempPath = path.join(
    path.dirname(finalPath),
    `.${path.basename(finalPath)}.${process.pid}.${randomId()}.tmp`,
  );
  let handle = null;
  let ownsTemp = false;
  let published = false;
  let failure = null;

  try {
    handle = await openFile(tempPath, 'wx');
    ownsTemp = true;
    await handle.writeFile(bytes, { encoding: 'utf8' });
    await handle.sync();
    await handle.close();
    handle = null;
    await linkFile(tempPath, finalPath);
    published = true;
  } catch (error) {
    failure = error;
  }

  if (handle !== null) {
    try {
      await handle.close();
    } catch (error) {
      failure ??= error;
    }
  }
  if (ownsTemp) {
    try {
      await unlinkFile(tempPath);
    } catch (error) {
      if (!published) {
        failure ??= error;
      }
    }
  }
  if (failure !== null) {
    throw failure;
  }
}

export async function main(
  argv = process.argv.slice(2),
  {
    probe = probeViewerRuntime,
    publishOutput = publishReport,
    stdout = process.stdout,
  } = {},
) {
  let args;
  let report;
  try {
    args = parseArgs(argv);
  } catch {
    report = failedReport(process.versions.node, ['ARGUMENT_INVALID']);
  }

  if (report === undefined) {
    try {
      report = await probe();
    } catch {
      report = failedReport(process.versions.node, ['PROBE_INTERNAL_FAILURE']);
    }
  }

  if (args?.output !== null && args?.output !== undefined) {
    try {
      const output = `${canonicalJson(report)}\n`;
      await publishOutput(args.output, output);
    } catch {
      report = withFailure(report, 'REPORT_WRITE_FAILED');
    }
  }

  try {
    stdout.write(`${canonicalJson(report)}\n`);
  } catch {
    return 2;
  }
  return report.status === 'ready' ? 0 : 2;
}

if (
  process.argv[1]
  && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href
) {
  process.exitCode = await main();
}
