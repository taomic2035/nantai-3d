import assert from 'node:assert/strict';
import test from 'node:test';

import { RunLedger } from './ledger.mjs';

class MemoryStorage {
  constructor() { this.map = new Map(); }
  getItem(key) { return this.map.get(key) ?? null; }
  setItem(key, value) { this.map.set(key, String(value)); }
  removeItem(key) { this.map.delete(key); }
}

function run(id, status = 'queued', startedAt = '2026-07-14T00:00:00.000Z') {
  return {
    id,
    command: 'reconstruct',
    status,
    retry_of: null,
    input_summary: {},
    parameters: {},
    adapter_kind: 'mock',
    started_at: startedAt,
    finished_at: status === 'queued' ? null : startedAt,
    artifact_ids: [],
    last_event_id: null,
    events: [],
  };
}

test('ledger survives refresh and deduplicates event ids', () => {
  const storage = new MemoryStorage();
  const first = new RunLedger({ storage });
  first.upsertRun(run('run-1'));
  first.appendEvent('run-1', { id: 'evt-1', seq: 1, message: 'start' });
  first.appendEvent('run-1', { id: 'evt-1', seq: 1, message: 'duplicate' });

  const refreshed = new RunLedger({ storage });
  assert.equal(refreshed.listRuns().length, 1);
  assert.equal(refreshed.getRun('run-1').events.length, 1);
  assert.equal(refreshed.getRun('run-1').events[0].message, 'start');
});

test('a stale failure cannot overwrite a succeeded record', () => {
  const ledger = new RunLedger({ storage: new MemoryStorage() });
  ledger.upsertRun(run('run-1', 'succeeded'));
  ledger.upsertRun(run('run-1', 'failed'));
  assert.equal(ledger.getRun('run-1').status, 'succeeded');
});

test('retry creates a new run and preserves retry_of', () => {
  let next = 1;
  const ledger = new RunLedger({
    storage: new MemoryStorage(),
    idFactory: () => `retry-${next++}`,
    now: () => '2026-07-14T01:00:00.000Z',
  });
  ledger.upsertRun(run('failed-run', 'failed'));
  const retried = ledger.retry('failed-run', { quality: 'high' });
  assert.equal(retried.id, 'retry-1');
  assert.equal(retried.retry_of, 'failed-run');
  assert.equal(retried.status, 'queued');
  assert.equal(retried.parameters.quality, 'high');
  assert.equal(ledger.getRun('failed-run').status, 'failed');
});

test('ledger enforces run and per-run event limits', () => {
  const ledger = new RunLedger({
    storage: new MemoryStorage(), maxRuns: 3, maxEventsPerRun: 2,
  });
  for (let i = 0; i < 5; i += 1) {
    ledger.upsertRun(run(`run-${i}`, 'succeeded', `2026-07-14T00:00:0${i}.000Z`));
  }
  assert.deepEqual(ledger.listRuns().map((item) => item.id), ['run-4', 'run-3', 'run-2']);

  ledger.upsertRun(run('active'));
  for (let i = 0; i < 4; i += 1) {
    ledger.appendEvent('active', { id: `evt-${i}`, seq: i, message: String(i) });
  }
  assert.deepEqual(ledger.getRun('active').events.map((event) => event.id), ['evt-2', 'evt-3']);
});
