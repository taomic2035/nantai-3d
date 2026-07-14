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
