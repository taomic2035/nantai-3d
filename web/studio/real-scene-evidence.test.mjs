import assert from 'node:assert/strict';
import test from 'node:test';

import {
  normalizeRealSceneEvidence,
  renderRealSceneEvidence,
} from './real-scene-evidence.mjs';

const SHA = 'a'.repeat(64);
const STAGE_IDS = [
  'dataset',
  'capture',
  'sfm',
  'production-training',
  'import-integrity',
  'render-quality',
  'viewer-performance',
  'human-review',
  'release-rights',
  'metric-alignment',
];

function acceptedCanary() {
  return {
    schema_version: 1,
    role: 'internal-canary',
    decision: 'accepted-canary',
    production_release_allowed: false,
    stages: STAGE_IDS.map((id) => ({
      id,
      state: ['release-rights', 'metric-alignment'].includes(id)
        ? 'not-started'
        : 'succeeded',
    })),
    reasons: [
      'release-rights: internal canary is never a release source',
      'metric-alignment: internal canary remains arbitrary and unaligned',
    ],
    report_sha256: SHA,
  };
}

test('accepted internal canary stays distinct from commercial acceptance', () => {
  const evidence = normalizeRealSceneEvidence(acceptedCanary());

  assert.equal(evidence.decision, 'accepted-canary');
  assert.equal(evidence.production_release_allowed, false);
  assert.equal(evidence.stages.at(-1).state, 'not-started');
  assert.match(renderRealSceneEvidence(evidence), /internal canary ≠ commercial acceptance/i);
});

test('production acceptance requires every closed gate to succeed', () => {
  const raw = acceptedCanary();
  raw.role = 'production-acceptance';
  raw.decision = 'accepted-production';
  raw.production_release_allowed = true;
  raw.stages = STAGE_IDS.map((id) => ({ id, state: 'succeeded' }));
  raw.reasons = [];

  assert.equal(normalizeRealSceneEvidence(raw).decision, 'accepted-production');

  raw.stages[6].state = 'failed';
  assert.equal(normalizeRealSceneEvidence(raw).decision, 'invalid-evidence');
});

test('forged release promotion and malformed stage sets fail closed', () => {
  const promoted = acceptedCanary();
  promoted.production_release_allowed = true;
  assert.equal(normalizeRealSceneEvidence(promoted).decision, 'invalid-evidence');

  const duplicate = acceptedCanary();
  duplicate.stages[1].id = duplicate.stages[0].id;
  assert.equal(normalizeRealSceneEvidence(duplicate).decision, 'invalid-evidence');

  const invented = acceptedCanary();
  invented.stages[0].state = 'passed';
  assert.equal(normalizeRealSceneEvidence(invented).decision, 'invalid-evidence');
});

test('rejected and unknown evidence never default to passed', () => {
  const rejected = acceptedCanary();
  rejected.role = 'production-acceptance';
  rejected.decision = 'rejected';
  rejected.stages = STAGE_IDS.map((id) => ({
    id,
    state: id === 'viewer-performance' ? 'failed' : 'succeeded',
  }));
  rejected.reasons = ['viewer-performance: p95 exceeded'];

  const normalized = normalizeRealSceneEvidence(rejected);
  assert.equal(normalized.decision, 'rejected');
  assert.equal(normalized.production_release_allowed, false);

  assert.equal(
    normalizeRealSceneEvidence({
      ...rejected,
      decision: 'invalid-evidence',
      role: 'unknown',
      stages: STAGE_IDS.map((id) => ({ id, state: 'unknown' })),
      report_sha256: null,
    }).decision,
    'invalid-evidence',
  );
});

test('presentation escapes evidence text and exposes no private report path', () => {
  const rejected = acceptedCanary();
  rejected.role = 'production-acceptance';
  rejected.decision = 'rejected';
  rejected.stages[6].state = 'failed';
  rejected.reasons = ['<img src=x onerror=alert(1)>'];

  const markup = renderRealSceneEvidence(rejected);

  assert.doesNotMatch(markup, /<img/);
  assert.match(markup, /&lt;img/);
  assert.doesNotMatch(markup, /href=|report_path|\.nantai-studio/);
  assert.match(markup, /Acceptance does not upgrade coordinate or geometry trust/);
});
