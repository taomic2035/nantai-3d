import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeCapabilities, readOnlyCapabilities } from './capabilities.mjs';
import {
  STUDIO_DAG,
  derivePrimaryAction,
  deriveRunActions,
  displayInputPath,
  preferredRunId,
  primaryNavigation,
} from './job-actions.mjs';

const readOnly = readOnlyCapabilities('Job execution is not enabled.');

function writable() {
  return normalizeCapabilities({
    schema_version: 1,
    mode: 'read-write',
    reason: 'Available.',
    request_token: 't'.repeat(43),
    single_writer: true,
    commands: Object.fromEntries(
      ['ingest', 'reconstruct', 'world', 'validate-assets'].map((command) => [
        command,
        { enabled: true, cancel: true, retry: true, reason: 'Available.' },
      ]),
    ),
  });
}

function snapshot() {
  return {
    adapter: { connected: true },
    sources: { images: 1, videos: 0 },
    reconstruction: { artifact: { id: 'recon' } },
    assets: { registered: 2, consumed: 2, blocked: 0 },
    pipeline: {
      sources: { availability: 'ready', execution: 'succeeded', freshness: 'current' },
      reconstruct: { availability: 'ready', execution: 'succeeded', freshness: 'current' },
      assets: { availability: 'ready', execution: 'succeeded', freshness: 'current' },
      stitch: { availability: 'ready', execution: 'succeeded', freshness: 'current' },
    },
    active_run: null,
  };
}

test('Studio views expose the real two-branch DAG instead of a linear pipeline', () => {
  assert.deepEqual(STUDIO_DAG.edges, [
    ['sources', 'reconstruct'],
    ['assets', 'compose'],
    ['reconstruct', 'review'],
    ['compose', 'review'],
  ]);
  assert.equal(STUDIO_DAG.views.find((view) => view.id === 'align').evidenceOf, 'reconstruct');
  assert.equal(STUDIO_DAG.views.find((view) => view.id === 'compose').stateKey, 'stitch');
});

test('empty sources navigate to the Sources evidence card', () => {
  const empty = snapshot();
  empty.sources = { images: 0, videos: 0 };
  const action = derivePrimaryAction(empty, readOnly);

  assert.equal(action.id, 'inspect-sources');
  assert.equal(action.enabled, true);
  assert.deepEqual(primaryNavigation(action), {
    step: 'sources', openDrawer: false, focus: 'source-empty', submitCommand: null,
  });
});

test('active and failed runs navigate to their command evidence and drawer', () => {
  const running = snapshot();
  running.active_run = { id: 'run-1', command: 'reconstruct', status: 'running' };
  assert.equal(derivePrimaryAction(running, readOnly).id, 'view-progress');
  assert.deepEqual(primaryNavigation(derivePrimaryAction(running, readOnly)), {
    step: 'reconstruct', openDrawer: true, focus: 'drawer', submitCommand: null,
  });

  const failed = snapshot();
  failed.active_run = { id: 'run-2', command: 'ingest', status: 'failed' };
  assert.equal(derivePrimaryAction(failed, readOnly).id, 'inspect-failure');
  assert.equal(primaryNavigation(derivePrimaryAction(failed, readOnly)).step, 'sources');
});

test('a missing artifact is a disabled write action in read-only mode', () => {
  const missing = snapshot();
  delete missing.reconstruction.artifact;

  const blocked = derivePrimaryAction(missing, readOnly);
  assert.equal(blocked.id, 'reconstruct');
  assert.equal(blocked.enabled, false);
  assert.match(blocked.reason, /not enabled/i);

  const enabled = derivePrimaryAction(missing, writable());
  assert.equal(enabled.enabled, true);
  assert.equal(primaryNavigation(enabled).submitCommand, 'reconstruct');
});

test('the primary action traverses both DAG branches before Review', () => {
  const needsIngest = snapshot();
  needsIngest.pipeline.sources.execution = 'idle';
  assert.equal(derivePrimaryAction(needsIngest, readOnly).id, 'ingest');

  const needsAssets = snapshot();
  needsAssets.pipeline.assets.availability = 'missing';
  assert.equal(derivePrimaryAction(needsAssets, readOnly).id, 'validate-assets');
  assert.equal(derivePrimaryAction(needsAssets, readOnly).targetStep, 'assets');

  const blockedAssets = snapshot();
  blockedAssets.assets = { registered: 2, consumed: 1, blocked: 1 };
  assert.equal(derivePrimaryAction(blockedAssets, readOnly).id, 'validate-assets');

  const needsWorld = snapshot();
  needsWorld.pipeline.stitch.availability = 'missing';
  assert.equal(derivePrimaryAction(needsWorld, readOnly).id, 'world');
  assert.equal(derivePrimaryAction(needsWorld, readOnly).targetStep, 'stitch');

  assert.equal(derivePrimaryAction(snapshot(), readOnly).id, 'review');
});

test('run navigation uses only the declared command and never guesses from pipeline state', () => {
  const world = snapshot();
  world.active_run = { id: 'run-world', command: 'world', status: 'running' };
  assert.equal(derivePrimaryAction(world, readOnly).targetStep, 'stitch');

  const assets = snapshot();
  assets.active_run = { id: 'run-assets', command: 'validate-assets', status: 'failed' };
  assert.equal(derivePrimaryAction(assets, readOnly).targetStep, 'assets');

  const unknown = snapshot();
  unknown.active_run = { id: 'run-unknown', command: 'other', status: 'failed' };
  assert.equal(derivePrimaryAction(unknown, readOnly).targetStep, 'review');
});

test('cancel and retry require both run state and advertised command capability', () => {
  const running = { id: 'run-1', command: 'reconstruct', status: 'running' };
  const failed = { id: 'run-2', command: 'reconstruct', status: 'failed' };

  assert.deepEqual(deriveRunActions(running, readOnly), []);
  assert.deepEqual(deriveRunActions(failed, readOnly), []);
  assert.deepEqual(deriveRunActions(running, writable()).map((action) => action.id), ['cancel']);
  assert.deepEqual(deriveRunActions(failed, writable()).map((action) => action.id), ['retry']);
});

test('input paths preserve platform separators without duplicate slashes', () => {
  assert.equal(displayInputPath('./'), './input');
  assert.equal(displayInputPath('/workspace/project/'), '/workspace/project/input');
  assert.equal(displayInputPath('D:\\project'), 'D:\\project\\input');
});

test('the snapshot active run outranks a stale drawer selection', () => {
  const runs = [{ id: 'run-failed' }, { id: 'run-old' }];
  assert.equal(preferredRunId(runs, 'run-old', 'run-failed'), 'run-failed');
  assert.equal(preferredRunId(runs, 'run-old', null), 'run-old');
  assert.equal(preferredRunId(runs, 'missing', null), 'run-failed');
});
