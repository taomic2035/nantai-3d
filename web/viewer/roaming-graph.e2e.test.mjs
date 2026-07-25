import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const graphPath = resolve(
  here,
  '..',
  '..',
  'tests',
  'fixtures',
  'roaming_graph',
  'exact218-candidate.json',
);

const graph = JSON.parse(readFileSync(graphPath, 'utf8'));

const { isRoamingGraph, roamingGraphViewModel } = await import('./roaming-graph.mjs');

test('real generated graph is accepted by JS validator', () => {
  assert.equal(isRoamingGraph(graph), true);
});

test('real generated graph exposes correct view model', () => {
  const model = roamingGraphViewModel(graph);
  assert.equal(model.status, 'graph-connected');
  assert.equal(model.room_count, 2);
  assert.equal(model.reachable_room_count, 2);
  assert.equal(model.component_count, 1);
  assert.equal(model.portal_count, 1);
  assert.equal(model.loop_count, 0);
  assert.equal(model.navigation_nodes.length, 2);
});
