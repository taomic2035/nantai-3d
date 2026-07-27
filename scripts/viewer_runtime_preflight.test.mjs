import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

let preflightModule;
try {
  preflightModule = await import('./viewer_runtime_preflight.mjs');
} catch (error) {
  preflightModule = { __loadError: error };
}

const SHA256 = /^[0-9a-f]{64}$/;
const SCRIPT = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  'viewer_runtime_preflight.mjs',
);

function subject() {
  assert.equal(
    preflightModule.__loadError,
    undefined,
    `viewer_runtime_preflight.mjs must load: ${
      preflightModule.__loadError?.message
    }`,
  );
  return preflightModule;
}

function successfulAdapters(overrides = {}) {
  const browser = {
    version: () => '151.0.7777.1',
    close: async () => {},
    ...overrides.browser,
  };
  return {
    nodeVersion: '22.14.0',
    loadPlaywright: async () => ({
      chromium: {
        executablePath: () => 'C:\\playwright\\chromium\\chrome.exe',
        launch: async () => browser,
      },
    }),
    loadPlaywrightVersion: async () => '1.62.0',
    inspectExecutable: async () => ({
      size: 123_456,
      sha256: 'a'.repeat(64),
    }),
    ...overrides,
  };
}

test('package import failure produces a structured failed report', async () => {
  const { probeViewerRuntime } = subject();
  const report = await probeViewerRuntime(successfulAdapters({
    loadPlaywright: async () => {
      throw new Error('ERR_MODULE_NOT_FOUND with a private stack');
    },
  }));

  assert.equal(report.schema, 'nantai.viewer-runtime-preflight.v1');
  assert.equal(report.status, 'failed');
  assert.deepEqual(report.failure_codes, ['PLAYWRIGHT_PACKAGE_MISSING']);
  assert.equal(report.node.version, '22.14.0');
  assert.equal(report.playwright.version, null);
  assert.equal(report.chromium.executable, null);
  assert.equal(report.chromium.launch_succeeded, false);
  assert.equal(JSON.stringify(report).includes('private stack'), false);
  assert.equal(Object.hasOwn(report, 'stack'), false);
});

test('missing Chromium binary cannot become ready', async () => {
  const { probeViewerRuntime } = subject();
  const report = await probeViewerRuntime(successfulAdapters({
    inspectExecutable: async () => {
      const error = new Error('missing');
      error.code = 'ENOENT';
      throw error;
    },
  }));

  assert.equal(report.status, 'failed');
  assert.deepEqual(report.failure_codes, ['CHROMIUM_BINARY_MISSING']);
  assert.equal(
    report.chromium.executable.path,
    'C:\\playwright\\chromium\\chrome.exe',
  );
  assert.equal(report.chromium.executable.sha256, null);
  assert.equal(report.chromium.executable.size, null);
  assert.equal(report.chromium.launch_succeeded, false);
});

test('Node before major 22 fails without launching Chromium', async () => {
  const { probeViewerRuntime } = subject();
  let launchCalls = 0;
  const adapters = successfulAdapters({ nodeVersion: '21.7.3' });
  const loaded = await adapters.loadPlaywright();
  loaded.chromium.launch = async () => {
    launchCalls += 1;
    throw new Error('must not launch');
  };
  adapters.loadPlaywright = async () => loaded;

  const report = await probeViewerRuntime(adapters);

  assert.equal(report.status, 'failed');
  assert.deepEqual(report.failure_codes, ['NODE_VERSION_UNSUPPORTED']);
  assert.equal(report.node.version, '21.7.3');
  assert.equal(report.chromium.launch_succeeded, false);
  assert.equal(launchCalls, 0);
});

test('invalid or unreadable Chromium hashes fail without launching', async () => {
  const { probeViewerRuntime } = subject();

  for (const [inspectExecutable, expectedCode] of [
    [
      async () => ({ size: 123_456, sha256: 'not-a-sha256' }),
      'CHROMIUM_BINARY_INVALID',
    ],
    [
      async () => {
        const error = new Error('read denied');
        error.code = 'EACCES';
        throw error;
      },
      'CHROMIUM_BINARY_HASH_FAILED',
    ],
  ]) {
    let launchCalls = 0;
    const adapters = successfulAdapters({ inspectExecutable });
    const loaded = await adapters.loadPlaywright();
    loaded.chromium.launch = async () => {
      launchCalls += 1;
      throw new Error('must not launch');
    };
    adapters.loadPlaywright = async () => loaded;

    const report = await probeViewerRuntime(adapters);

    assert.equal(report.status, 'failed');
    assert.deepEqual(report.failure_codes, [expectedCode]);
    assert.equal(report.chromium.launch_succeeded, false);
    assert.equal(launchCalls, 0);
  }
});

test('Chromium launch rejection is fail closed', async () => {
  const { probeViewerRuntime } = subject();
  const adapters = successfulAdapters();
  const loaded = await adapters.loadPlaywright();
  loaded.chromium.launch = async () => {
    throw new Error('launch exposed a private path');
  };
  adapters.loadPlaywright = async () => loaded;

  const report = await probeViewerRuntime(adapters);

  assert.equal(report.status, 'failed');
  assert.deepEqual(report.failure_codes, ['CHROMIUM_LAUNCH_FAILED']);
  assert.equal(report.chromium.launch_succeeded, false);
  assert.equal(report.chromium.browser_version, null);
  assert.equal(report.chromium.close_succeeded, false);
  assert.equal(JSON.stringify(report).includes('private path'), false);
});

test('Chromium close rejection is fail closed', async () => {
  const { probeViewerRuntime } = subject();
  const report = await probeViewerRuntime(successfulAdapters({
    browser: {
      close: async () => {
        throw new Error('close failed');
      },
    },
  }));

  assert.equal(report.status, 'failed');
  assert.deepEqual(report.failure_codes, ['CHROMIUM_CLOSE_FAILED']);
  assert.equal(report.chromium.launch_succeeded, true);
  assert.equal(report.chromium.browser_version, '151.0.7777.1');
  assert.equal(report.chromium.close_succeeded, false);
});

test('post-close executable drift is fail closed', async () => {
  const { probeViewerRuntime } = subject();
  let measurements = 0;
  const report = await probeViewerRuntime(successfulAdapters({
    inspectExecutable: async () => {
      measurements += 1;
      return {
        size: measurements === 1 ? 123_456 : 123_457,
        sha256: measurements === 1 ? 'a'.repeat(64) : 'b'.repeat(64),
      };
    },
  }));

  assert.equal(measurements, 2);
  assert.equal(report.status, 'failed');
  assert.deepEqual(report.failure_codes, ['CHROMIUM_BINARY_DRIFT']);
  assert.equal(report.chromium.launch_succeeded, true);
  assert.equal(report.chromium.close_succeeded, true);
});

test('all runtime bindings and real lifecycle evidence produce ready', async () => {
  const { probeViewerRuntime } = subject();
  let measurements = 0;
  let launchOptions;
  const adapters = successfulAdapters({
    inspectExecutable: async () => {
      measurements += 1;
      return {
        size: 123_456,
        sha256: 'a'.repeat(64),
      };
    },
  });
  const loaded = await adapters.loadPlaywright();
  const launch = loaded.chromium.launch;
  loaded.chromium.launch = async (options) => {
    launchOptions = options;
    return launch(options);
  };
  adapters.loadPlaywright = async () => loaded;

  const report = await probeViewerRuntime(adapters);

  assert.equal(report.status, 'ready');
  assert.deepEqual(report.failure_codes, []);
  assert.equal(measurements, 2);
  assert.deepEqual(launchOptions, {
    executablePath: 'C:\\playwright\\chromium\\chrome.exe',
    headless: true,
  });
  assert.deepEqual(report.node, {
    minimum_major: 22,
    version: '22.14.0',
  });
  assert.deepEqual(report.playwright, { version: '1.62.0' });
  assert.deepEqual(report.chromium.executable, {
    path: 'C:\\playwright\\chromium\\chrome.exe',
    sha256: 'a'.repeat(64),
    size: 123_456,
  });
  assert.match(report.chromium.executable.sha256, SHA256);
  assert.equal(report.chromium.launch_succeeded, true);
  assert.equal(report.chromium.browser_version, '151.0.7777.1');
  assert.equal(report.chromium.close_succeeded, true);
});

test('canonical JSON sorts object keys and rejects non-finite numbers', () => {
  const { canonicalJson } = subject();

  assert.equal(
    canonicalJson({ z: 1, a: { y: true, b: null } }),
    '{"a":{"b":null,"y":true},"z":1}',
  );
  assert.throws(
    () => canonicalJson({ value: Number.POSITIVE_INFINITY }),
    /non-finite/,
  );
});

function publisherAdapters({
  writeError = null,
  linkError = null,
  unlinkError = null,
} = {}) {
  const calls = [];
  const handle = {
    writeFile: async (...args) => {
      calls.push(['write', ...args]);
      if (writeError) throw writeError;
    },
    sync: async () => calls.push(['sync']),
    close: async () => calls.push(['close']),
  };
  return {
    calls,
    adapters: {
      randomId: () => 'fixed-id',
      openFile: async (...args) => {
        calls.push(['open', ...args]);
        return handle;
      },
      linkFile: async (...args) => {
        calls.push(['link', ...args]);
        if (linkError) throw linkError;
      },
      unlinkFile: async (...args) => {
        calls.push(['unlink', ...args]);
        if (unlinkError) throw unlinkError;
      },
    },
  };
}

test('publisher uses an owned sibling temp and atomic no-replace link', async () => {
  const { publishReport } = subject();
  const { adapters, calls } = publisherAdapters();
  const outputPath = path.resolve('reports', 'runtime-report.json');
  const bytes = '{"status":"ready"}\n';
  const tempPath = path.join(
    path.dirname(outputPath),
    `.runtime-report.json.${process.pid}.fixed-id.tmp`,
  );

  await publishReport(outputPath, bytes, adapters);

  assert.deepEqual(calls, [
    ['open', tempPath, 'wx'],
    ['write', bytes, { encoding: 'utf8' }],
    ['sync'],
    ['close'],
    ['link', tempPath, outputPath],
    ['unlink', tempPath],
  ]);
});

test('publisher preserves an existing final and cleans its owned temp', async () => {
  const { publishReport } = subject();
  const error = new Error('already exists');
  error.code = 'EEXIST';
  const { adapters, calls } = publisherAdapters({ linkError: error });
  const outputPath = path.resolve('reports', 'runtime-report.json');

  await assert.rejects(
    publishReport(outputPath, '{}\n', adapters),
    { code: 'EEXIST' },
  );

  const linked = calls.find(([operation]) => operation === 'link');
  const unlinked = calls.filter(([operation]) => operation === 'unlink');
  assert.equal(linked[2], outputPath);
  assert.deepEqual(unlinked, [['unlink', linked[1]]]);
  assert.equal(unlinked.some(([, target]) => target === outputPath), false);
});

test('publisher cleans a mid-write temp without creating the final', async () => {
  const { publishReport } = subject();
  const error = new Error('disk write failed');
  error.code = 'EIO';
  const { adapters, calls } = publisherAdapters({ writeError: error });
  const outputPath = path.resolve('reports', 'runtime-report.json');

  await assert.rejects(
    publishReport(outputPath, '{}\n', adapters),
    { code: 'EIO' },
  );

  assert.equal(calls.some(([operation]) => operation === 'link'), false);
  assert.equal(calls.filter(([operation]) => operation === 'close').length, 1);
  assert.equal(calls.filter(([operation]) => operation === 'unlink').length, 1);
  assert.equal(
    calls.some(
      ([operation, target]) => operation === 'unlink' && target === outputPath,
    ),
    false,
  );
});

test('post-publication temp cleanup failure preserves ready status', async () => {
  const { main, probeViewerRuntime, publishReport } = subject();
  const unlinkError = new Error('temp cleanup denied');
  unlinkError.code = 'EPERM';
  const { adapters, calls } = publisherAdapters({ unlinkError });
  const report = await probeViewerRuntime(successfulAdapters());
  let stdout = '';

  const exitCode = await main(
    ['--output', 'runtime-report.json'],
    {
      probe: async () => report,
      publishOutput: (outputPath, bytes) => (
        publishReport(outputPath, bytes, adapters)
      ),
      stdout: { write: (value) => { stdout += value; } },
    },
  );

  assert.equal(exitCode, 0);
  assert.equal(JSON.parse(stdout).status, 'ready');
  assert.equal(calls.some(([operation]) => operation === 'link'), true);
  assert.equal(calls.some(([operation]) => operation === 'unlink'), true);
});

test('cleanup failure before publication preserves the primary link error', async () => {
  const { publishReport } = subject();
  const linkError = new Error('existing final');
  linkError.code = 'EEXIST';
  const unlinkError = new Error('temp cleanup denied');
  unlinkError.code = 'EPERM';
  const { adapters } = publisherAdapters({ linkError, unlinkError });

  await assert.rejects(
    publishReport('runtime-report.json', '{}\n', adapters),
    { code: 'EEXIST' },
  );
});

test('main publishes the same canonical LF record through the publisher', async () => {
  const { main } = subject();
  const report = await subject().probeViewerRuntime(successfulAdapters());
  const publications = [];
  let stdout = '';
  const outputPath = 'scripts/viewer_runtime_preflight.test.mjs';

  const exitCode = await main(
    ['--output', outputPath],
    {
      probe: async () => report,
      publishOutput: async (...args) => publications.push(args),
      stdout: { write: (value) => { stdout += value; } },
    },
  );

  assert.equal(exitCode, 0);
  assert.deepEqual(publications, [[outputPath, stdout]]);
  assert.equal(stdout.endsWith('\n'), true);
  assert.equal(stdout.split('\n').length, 2);
  assert.equal(JSON.parse(stdout).status, 'ready');
});

test('publication failure emits the final failed report and exit 2', async () => {
  const { main } = subject();
  const report = await subject().probeViewerRuntime(successfulAdapters());
  let stdout = '';
  let publicationCalls = 0;

  const exitCode = await main(
    ['--output', 'scripts/viewer_runtime_preflight.test.mjs'],
    {
      probe: async () => report,
      publishOutput: async () => {
        publicationCalls += 1;
        const error = new Error('existing final');
        error.code = 'EEXIST';
        throw error;
      },
      stdout: { write: (value) => { stdout += value; } },
    },
  );

  assert.equal(exitCode, 2);
  assert.equal(publicationCalls, 1);
  assert.equal(stdout.split('\n').length, 2);
  const published = JSON.parse(stdout);
  assert.equal(published.status, 'failed');
  assert.deepEqual(published.failure_codes, ['REPORT_WRITE_FAILED']);
});

test('invalid CLI arguments emit one canonical failed record and exit 2', () => {
  const result = spawnSync(
    process.execPath,
    [SCRIPT, '--unknown'],
    { encoding: 'utf8' },
  );

  assert.equal(result.status, 2);
  assert.equal(result.signal, null);
  assert.equal(result.stderr, '');
  assert.equal(result.stdout.endsWith('\n'), true);
  assert.equal(result.stdout.split('\n').length, 2);
  const report = JSON.parse(result.stdout);
  assert.equal(report.schema, 'nantai.viewer-runtime-preflight.v1');
  assert.equal(report.status, 'failed');
  assert.deepEqual(report.failure_codes, ['ARGUMENT_INVALID']);
  assert.equal(Object.hasOwn(report, 'stack'), false);
});
