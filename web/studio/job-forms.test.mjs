import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DEFAULT_INGEST_PARAMETERS,
  ingestConfirmationModel,
  validateIngestParameters,
} from './job-forms.mjs';

test('ingest confirmation exposes only fixed generic replaceable paths', () => {
  const model = ingestConfirmationModel({ inputPath: 'D:/project/input' });
  assert.equal(model.command, 'ingest');
  assert.equal(model.inputPath, 'D:/project/input');
  assert.equal(model.formalTarget, 'photos/');
  assert.match(model.stagingPath, /<run-id>/);
  assert.match(model.cancelNotice, /不支持.*取消/);
  assert.deepEqual(model.parameters, DEFAULT_INGEST_PARAMETERS);
});

test('ingest parameters use backend bounds and reject unknown fields', () => {
  assert.deepEqual(validateIngestParameters({ fps: 3 }), {
    ...DEFAULT_INGEST_PARAMETERS, fps: 3,
  });
  assert.throws(() => validateIngestParameters({ fps: 0 }), /fps/);
  assert.throws(() => validateIngestParameters({ max_frames: 1.5 }), /max_frames/);
  assert.throws(() => validateIngestParameters({ max_long_edge: 20_000 }), /max_long_edge/);
  assert.throws(() => validateIngestParameters({ path: '../outside' }), /unknown/);
});
