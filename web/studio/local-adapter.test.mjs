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

test('local adapter loads explicit capabilities instead of inferring from methods', async () => {
  const requested = [];
  const adapter = new LocalStudioAdapter({
    baseUrl: 'http://127.0.0.1:8000',
    fetchImpl: async (url) => {
      requested.push(url);
      return response({
        schema_version: 1,
        mode: 'read-only',
        reason: 'Jobs unavailable.',
        request_token: null,
        single_writer: true,
        commands: Object.fromEntries(
          ['ingest', 'reconstruct', 'world', 'validate-assets'].map((command) => [
            command,
            { enabled: false, cancel: false, retry: false, reason: 'Jobs unavailable.' },
          ]),
        ),
      });
    },
  });

  const capabilities = await adapter.loadCapabilities();
  assert.equal(capabilities.mode, 'read-only');
  assert.equal(capabilities.commands.reconstruct.enabled, false);
  assert.deepEqual(requested, ['http://127.0.0.1:8000/api/capabilities']);
});

test('capability discovery failures degrade to read-only without blocking project reads', async () => {
  for (const capabilityResponse of [
    response({ error: { message: 'missing' } }, { ok: false, status: 404 }),
    response(null),
  ]) {
    const adapter = new LocalStudioAdapter({
      fetchImpl: async (url) => (
        url.endsWith('/api/project')
          ? response({ schema_version: 2, adapter: { kind: 'local', connected: true } })
          : capabilityResponse
      ),
    });

    assert.equal((await adapter.loadCapabilities()).mode, 'read-only');
    assert.equal((await adapter.loadProject()).schema_version, 2);
  }
});

test('B1 local adapter accepts a valid server write lease', async () => {
  const adapter = new LocalStudioAdapter({
    fetchImpl: async () => response({
      schema_version: 1,
      mode: 'read-write',
      reason: 'Available.',
      request_token: 't'.repeat(43),
      single_writer: true,
      commands: Object.fromEntries(
        ['ingest', 'reconstruct', 'world', 'validate-assets'].map((command) => [
          command, { enabled: true, cancel: true, retry: true, reason: 'Available.' },
        ]),
      ),
    }),
  });

  const capabilities = await adapter.loadCapabilities();
  assert.equal(capabilities.mode, 'read-write');
  assert.equal(capabilities.request_token, 't'.repeat(43));
});

test('local adapter attaches only the latest token and a fresh request ID to writes', async () => {
  const requests = [];
  let requestNumber = 0;
  const adapter = new LocalStudioAdapter({
    baseUrl: 'http://127.0.0.1:8765',
    requestIdFactory: () => `request-browser-${++requestNumber}`,
    fetchImpl: async (url, init) => {
      requests.push({ url, init });
      if (url.endsWith('/api/capabilities')) {
        return response({
          schema_version: 1,
          mode: 'read-write',
          reason: null,
          request_token: 'a'.repeat(43),
          single_writer: true,
          commands: Object.fromEntries(
            ['ingest', 'reconstruct', 'world', 'validate-assets'].map((command) => [
              command,
              {
                enabled: command === 'ingest', cancel: false, retry: false,
                reason: command === 'ingest' ? null : 'Later milestone.',
              },
            ]),
          ),
        });
      }
      return response({ created: true, run: { id: 'run-1' } }, { status: 202 });
    },
  });

  await adapter.loadCapabilities();
  await adapter.startJob('ingest', { fps: 2 });
  await adapter.startJob('ingest', { fps: 3 });

  const writes = requests.filter(({ url }) => url.endsWith('/api/jobs'));
  assert.deepEqual(
    writes.map(({ init }) => init.headers['x-request-id']),
    ['request-browser-1', 'request-browser-2'],
  );
  assert.ok(writes.every(({ init }) => init.headers['x-nantai-token'] === 'a'.repeat(43)));
});

test('capability refresh failure drops stale write authorization', async () => {
  let fail = false;
  const adapter = new LocalStudioAdapter({
    fetchImpl: async (url) => {
      if (url.endsWith('/api/capabilities') && !fail) {
        return response({
          schema_version: 1, mode: 'read-write', reason: null,
          request_token: 'a'.repeat(43), single_writer: true,
          commands: Object.fromEntries(
            ['ingest', 'reconstruct', 'world', 'validate-assets'].map((command) => [
              command, { enabled: command === 'ingest', cancel: false, retry: false },
            ]),
          ),
        });
      }
      throw new Error('offline');
    },
  });
  assert.equal((await adapter.loadCapabilities()).mode, 'read-write');
  fail = true;
  assert.equal((await adapter.loadCapabilities()).mode, 'read-only');
  await assert.rejects(() => adapter.startJob('ingest', {}), /authorization/);
});

test('local adapter forwards cursor and supports run detail reads', async () => {
  const requested = [];
  const adapter = new LocalStudioAdapter({
    fetchImpl: async (url) => {
      requested.push(url);
      return response(url.includes('/api/runs/run-1')
        ? { run: { id: 'run-1' }, events: [] }
        : { items: [], events: [], cursor: 12 });
    },
  });

  await adapter.listRuns(7);
  await adapter.loadRun('run-1');
  assert.deepEqual(requested, ['/api/runs?cursor=7', '/api/runs/run-1']);
});
