import assert from 'node:assert/strict';
import test from 'node:test';

let colorModule;
try {
  colorModule = await import('./point-preview-color.mjs');
} catch (error) {
  colorModule = { __loadError: error };
}

function subject() {
  assert.equal(
    colorModule.__loadError,
    undefined,
    `point-preview-color.mjs must load: ${colorModule.__loadError?.message}`,
  );
  return colorModule;
}

test('decodes byte RGB and 3DGS SH0 colors into bounded preview channels', () => {
  const { pointPreviewColorComponent, SH_C0 } = subject();

  assert.deepEqual(pointPreviewColorComponent('r', 255), { index: 0, value: 1 });
  assert.deepEqual(pointPreviewColorComponent('g', 128), {
    index: 1,
    value: 128 / 255,
  });
  assert.deepEqual(pointPreviewColorComponent('b', 0), { index: 2, value: 0 });
  assert.deepEqual(
    pointPreviewColorComponent('f_dc_0', (1 - 0.5) / SH_C0),
    { index: 0, value: 1 },
  );
  assert.deepEqual(
    pointPreviewColorComponent('f_dc_1', (0 - 0.5) / SH_C0),
    { index: 1, value: 0 },
  );
  assert.deepEqual(pointPreviewColorComponent('f_dc_2', 0), {
    index: 2,
    value: 0.5,
  });
});

test('ignores unrelated or non-finite PLY properties', () => {
  const { pointPreviewColorComponent } = subject();
  assert.equal(pointPreviewColorComponent('opacity', 1), null);
  assert.equal(pointPreviewColorComponent('f_dc_0', Number.NaN), null);
});
