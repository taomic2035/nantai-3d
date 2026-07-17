import assert from 'node:assert/strict';
import test from 'node:test';

let loaderModule;
try {
  loaderModule = await import('./coverage-audit-loader.mjs');
} catch (error) {
  loaderModule = { __loadError: error };
}

function subject() {
  assert.equal(
    loaderModule.__loadError,
    undefined,
    `coverage-audit-loader.mjs must load: ${loaderModule.__loadError?.message}`,
  );
  return loaderModule;
}

test('unsupported Viewer capability avoids even probing the optional report', async () => {
  const { loadOptionalCoverageAudit } = subject();
  let fetchCalls = 0;
  const result = await loadOptionalCoverageAudit({
    bridge: {
      supportsArtifactKind: () => false,
      loadArtifact: () => {
        throw new Error('must not load');
      },
    },
    fetchImpl: async () => {
      fetchCalls += 1;
      throw new Error('must not fetch');
    },
  });

  assert.deepEqual(result, { status: 'unsupported' });
  assert.equal(fetchCalls, 0);
});

test('missing canonical report is an honest quiet absence', async () => {
  const { loadOptionalCoverageAudit } = subject();
  let loadCalls = 0;
  const result = await loadOptionalCoverageAudit({
    bridge: {
      supportsArtifactKind: () => true,
      loadArtifact: () => {
        loadCalls += 1;
      },
    },
    fetchImpl: async (url, options) => {
      assert.equal(url, '/web/data/coverage-audit.json');
      assert.deepEqual(options, { method: 'HEAD', cache: 'no-store' });
      return { ok: false, status: 404 };
    },
  });

  assert.deepEqual(result, { status: 'absent' });
  assert.equal(loadCalls, 0);
});

test('present report is loaded through the capability-gated Viewer bridge', async () => {
  const { loadOptionalCoverageAudit } = subject();
  const calls = [];
  const result = await loadOptionalCoverageAudit({
    bridge: {
      supportsArtifactKind: (kind) => kind === 'coverage-audit',
      loadArtifact: async (kind, payload) => {
        calls.push({ kind, payload });
        return { coverage: { status: 'diagnostic-unvalidated' } };
      },
    },
    fetchImpl: async () => ({ ok: true, status: 200 }),
  });

  assert.deepEqual(calls, [{
    kind: 'coverage-audit',
    payload: { url: '/web/data/coverage-audit.json' },
  }]);
  assert.deepEqual(result, {
    status: 'loaded',
    coverage: { status: 'diagnostic-unvalidated' },
  });
});

test('non-404 probe failures remain visible instead of masquerading as absence', async () => {
  const { loadOptionalCoverageAudit } = subject();
  await assert.rejects(
    () => loadOptionalCoverageAudit({
      bridge: {
        supportsArtifactKind: () => true,
        loadArtifact: () => {
          throw new Error('must not load');
        },
      },
      fetchImpl: async () => ({ ok: false, status: 500 }),
    }),
    /probe failed \(500\)/i,
  );
});
