import assert from 'node:assert/strict';
import test from 'node:test';

import { LocalStudioAdapter } from './local-adapter.mjs';

function response(body, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    headers: { get: () => 'application/json; charset=utf-8' },
    json: async () => body,
  };
}

test('local adapter loads a v2 project snapshot without rewriting provenance', async () => {
  const requested = [];
  const raw = {
    schema_version: 2,
    adapter: { kind: 'local', connected: true },
    coordinate: { units: 'arbitrary', metric_evidence: [] },
    reconstruction: { actual_engine: 'import', synthetic: false },
  };
  const adapter = new LocalStudioAdapter({
    baseUrl: 'http://127.0.0.1:8000',
    fetchImpl: async (url) => { requested.push(url); return response(raw); },
  });

  assert.deepEqual(await adapter.loadProject(), raw);
  assert.deepEqual(requested, ['http://127.0.0.1:8000/api/project']);
});

test('local adapter rejects non-v2 snapshots fail closed', async () => {
  const adapter = new LocalStudioAdapter({
    fetchImpl: async () => response({ schema_version: 1 }),
  });
  await assert.rejects(() => adapter.loadProject(), /schema_version 2/);
});

test('local adapter normalizes structured HTTP failures', async () => {
  const adapter = new LocalStudioAdapter({
    fetchImpl: async () => response(
      { error: { code: 'artifact-missing', message: 'recon manifest missing' } },
      { ok: false, status: 409 },
    ),
  });
  await assert.rejects(
    () => adapter.loadProject(),
    (error) => error.code === 'artifact-missing' && error.status === 409,
  );
});

test('local adapter validates the run collection envelope', async () => {
  const good = new LocalStudioAdapter({
    fetchImpl: async () => response({ items: [{ id: 'run-1' }] }),
  });
  assert.deepEqual(await good.listRuns(), { items: [{ id: 'run-1' }] });

  const bad = new LocalStudioAdapter({
    fetchImpl: async () => response({ runs: [] }),
  });
  await assert.rejects(() => bad.listRuns(), /items array/);
});
