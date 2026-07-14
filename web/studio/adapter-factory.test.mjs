import assert from 'node:assert/strict';
import test from 'node:test';

import { selectStudioAdapter } from './adapter-factory.mjs';

const storage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };

function jsonResponse(payload, ok = true, status = 200) {
  return { ok, status, json: async () => payload };
}

test('auto mode prefers a reachable local v2 service', async () => {
  const result = await selectStudioAdapter({
    search: '', storage,
    fetchImpl: async () => jsonResponse({
      schema_version: 2, adapter: { kind: 'local', connected: true },
    }),
  });
  assert.equal(result.adapter.kind, 'local');
  assert.equal(result.fallbackReason, null);
});

test('auto mode falls back to an explicitly labelled mock adapter', async () => {
  const result = await selectStudioAdapter({
    search: '', storage,
    fetchImpl: async () => jsonResponse({ error: {} }, false, 404),
  });
  assert.equal(result.adapter.kind, 'mock');
  assert.match(result.fallbackReason, /local service unavailable/i);
});

test('query mode can force deterministic mock fixtures without probing local', async () => {
  let calls = 0;
  const result = await selectStudioAdapter({
    search: '?adapter=mock', storage,
    fetchImpl: async () => { calls += 1; return jsonResponse({}); },
  });
  assert.equal(result.adapter.kind, 'mock');
  assert.equal(result.fallbackReason, 'forced by query');
  assert.equal(calls, 0);
});

test('explicit local mode fails instead of silently substituting mock truth', async () => {
  await assert.rejects(
    () => selectStudioAdapter({
      search: '?adapter=local', storage,
      fetchImpl: async () => jsonResponse({ error: {} }, false, 503),
    }),
    /local service/i,
  );
});
