import assert from 'node:assert/strict';
import test from 'node:test';

import { MockStudioAdapter } from './mock-adapter.mjs';

class MemoryStorage {
  constructor() { this.values = new Map(); }
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}

test('ready scenario reflects the current eleven-of-eleven asset consumption', async () => {
  const adapter = new MockStudioAdapter({ storage: new MemoryStorage() });
  const snapshot = await adapter.loadProject();
  assert.deepEqual(
    [snapshot.assets.registered, snapshot.assets.consumed, snapshot.assets.blocked],
    [11, 11, 0],
  );
  assert.ok(snapshot.assets.items.every((item) => item.consumed));
  assert.equal(snapshot.reconstruction.actual_engine, 'mock-proxy');
  assert.equal(snapshot.reconstruction.artifact.kind, '3dgs-ply');
  assert.match(snapshot.reconstruction.artifact.uri, /recon_full\.ply$/);
});

test('assets-partial remains an explicit failure fixture', async () => {
  const adapter = new MockStudioAdapter({ storage: new MemoryStorage() });
  adapter.setScenario('assets-partial');
  const snapshot = await adapter.loadProject();
  assert.deepEqual(
    [snapshot.assets.registered, snapshot.assets.consumed, snapshot.assets.blocked],
    [11, 8, 3],
  );
  assert.equal(snapshot.assets.items.filter((item) => !item.consumed).length, 3);
});

test('mock methods never imply project write capability', async () => {
  const adapter = new MockStudioAdapter({ storage: new MemoryStorage() });
  const capabilities = await adapter.loadCapabilities();

  assert.equal(capabilities.mode, 'read-only');
  assert.equal(capabilities.request_token, null);
  assert.ok(Object.values(capabilities.commands).every((command) => !command.enabled));
  assert.match(capabilities.reason, /mock scenarios/i);
});

test('empty and missing-reconstruction fixtures exercise read-only primary actions', async () => {
  const adapter = new MockStudioAdapter({ storage: new MemoryStorage() });

  adapter.setScenario('empty');
  const empty = await adapter.loadProject();
  assert.deepEqual(
    [empty.sources.images, empty.sources.videos, empty.sources.frames, empty.sources.files.length],
    [0, 0, 0, 0],
  );

  adapter.setScenario('missing-reconstruction');
  const missing = await adapter.loadProject();
  assert.equal(missing.sources.images > 0, true);
  assert.equal(missing.reconstruction.artifact, null);
});

test('running and failed snapshots expose the same active run in the run collection', async () => {
  const adapter = new MockStudioAdapter({ storage: new MemoryStorage() });
  for (const [scenarioName, status] of [['running', 'running'], ['failed', 'failed']]) {
    adapter.setScenario(scenarioName);
    const snapshot = await adapter.loadProject();
    const runs = await adapter.listRuns();
    const active = runs.items.find((run) => run.id === snapshot.active_run.id);
    assert.equal(active?.status, status);
    assert.equal(active?.command, snapshot.active_run.command);
    assert.equal(active?.events.at(-1)?.phase, status === 'failed' ? 'failed' : 'reconstruct');
  }
});
