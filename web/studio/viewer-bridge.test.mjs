import assert from 'node:assert/strict';
import test from 'node:test';

import { StudioViewerBridge } from './viewer-bridge.mjs';

function harness() {
  const sent = [];
  const listeners = new Map();
  const frameWindow = { postMessage: (message, origin) => sent.push({ message, origin }) };
  const windowObject = {
    location: { origin: 'https://studio.example' },
    addEventListener: (type, listener) => listeners.set(type, listener),
    removeEventListener: (type) => listeners.delete(type),
  };
  return { sent, listeners, frameWindow, windowObject };
}

function viewerMessage(type, payload = {}, requestId = null) {
  return {
    channel: 'nantai-viewer', schema_version: 1, type,
    request_id: requestId, payload,
  };
}

test('ready handshake exposes only declared capabilities', () => {
  const h = harness();
  const bridge = new StudioViewerBridge({
    windowObject: h.windowObject, frameWindow: h.frameWindow,
  });
  bridge.start();
  bridge.handleMessage({
    origin: h.windowObject.location.origin,
    source: h.frameWindow,
    data: viewerMessage('ready', { capabilities: { commands: ['getState', 'resetCamera'] } }),
  });
  assert.equal(bridge.status, 'ready');
  assert.equal(bridge.supports('resetCamera'), true);
  assert.equal(bridge.supports('setLOD'), false);
});

test('capabilitiesChanged replaces the live renderer evidence', () => {
  const h = harness();
  const statuses = [];
  const bridge = new StudioViewerBridge({
    windowObject: h.windowObject,
    frameWindow: h.frameWindow,
    onStatus: (status, capabilities) => statuses.push({ status, capabilities }),
  });
  bridge.handleMessage({
    origin: h.windowObject.location.origin,
    source: h.frameWindow,
    data: viewerMessage('ready', {
      capabilities: { commands: ['getState'], renderer: { fidelity: 'dc-point-preview' } },
    }),
  });
  bridge.handleMessage({
    origin: h.windowObject.location.origin,
    source: h.frameWindow,
    data: viewerMessage('capabilitiesChanged', {
      capabilities: {
        commands: ['getState', 'setLOD'],
        renderer: { fidelity: 'full-3dgs', anisotropic_covariance: true },
      },
    }, 'viewer-capabilities'),
  });

  assert.equal(bridge.status, 'ready');
  assert.equal(bridge.capabilities.renderer.fidelity, 'full-3dgs');
  assert.equal(bridge.supports('setLOD'), true);
  assert.equal(statuses.length, 2);
});

test('commands are blocked before handshake and when unsupported', async () => {
  const h = harness();
  const bridge = new StudioViewerBridge({ windowObject: h.windowObject, frameWindow: h.frameWindow });
  await assert.rejects(() => bridge.command('resetCamera'), /not ready/i);
  bridge.handleMessage({
    origin: h.windowObject.location.origin, source: h.frameWindow,
    data: viewerMessage('ready', { capabilities: { commands: ['getState'] } }),
  });
  await assert.rejects(() => bridge.command('resetCamera'), /unsupported/i);
  assert.equal(h.sent.length, 0);
});

test('correlated viewer response resolves the pending command', async () => {
  const h = harness();
  let next = 0;
  const bridge = new StudioViewerBridge({
    windowObject: h.windowObject, frameWindow: h.frameWindow,
    idFactory: () => `request-${++next}`,
  });
  bridge.handleMessage({
    origin: h.windowObject.location.origin, source: h.frameWindow,
    data: viewerMessage('ready', { capabilities: { commands: ['getState'] } }),
  });
  const pending = bridge.command('getState');
  assert.equal(h.sent[0].message.request_id, 'request-1');
  bridge.handleMessage({
    origin: h.windowObject.location.origin, source: h.frameWindow,
    data: viewerMessage('stateChanged', { result: { lod: 2 } }, 'request-1'),
  });
  assert.deepEqual(await pending, { lod: 2 });
});

test('cross-origin and wrong-frame messages are ignored', () => {
  const h = harness();
  const bridge = new StudioViewerBridge({ windowObject: h.windowObject, frameWindow: h.frameWindow });
  bridge.handleMessage({
    origin: 'https://attacker.example', source: h.frameWindow,
    data: viewerMessage('ready', { capabilities: { commands: ['getState'] } }),
  });
  bridge.handleMessage({
    origin: h.windowObject.location.origin, source: {},
    data: viewerMessage('ready', { capabilities: { commands: ['getState'] } }),
  });
  assert.equal(bridge.status, 'waiting');
});
