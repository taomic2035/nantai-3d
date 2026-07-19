import assert from 'node:assert/strict';
import test from 'node:test';

let performanceModule;
try {
  performanceModule = await import('./frame-performance.mjs');
} catch (error) {
  performanceModule = { __loadError: error };
}

function subject() {
  assert.equal(
    performanceModule.__loadError,
    undefined,
    `frame-performance.mjs must load: ${
      performanceModule.__loadError?.message
    }`,
  );
  return performanceModule;
}

test('frame sampler reports no evidence during warmup', () => {
  const { createFrameIntervalSampler } = subject();
  const sampler = createFrameIntervalSampler();

  sampler.record(100);
  sampler.record(5_100);
  sampler.record(10_099);

  assert.deepEqual(sampler.snapshot(), {
    sample_count: 0,
    median_ms: null,
    p95_ms: null,
  });
});

test('frame sampler retains only the newest bounded intervals', () => {
  const { createFrameIntervalSampler } = subject();
  const sampler = createFrameIntervalSampler({
    warmupMs: 0,
    maximumSamples: 3,
  });

  for (const nowMs of [0, 1, 3, 6, 10]) sampler.record(nowMs);

  assert.deepEqual(sampler.snapshot(), {
    sample_count: 3,
    median_ms: 3,
    p95_ms: 4,
  });
});

test('frame sampler uses lower median and nearest-rank p95 exactly', () => {
  const { createFrameIntervalSampler } = subject();
  const sampler = createFrameIntervalSampler({
    warmupMs: 0,
    maximumSamples: 3_600,
  });
  let nowMs = 0;
  sampler.record(nowMs);
  for (let interval = 20; interval >= 1; interval -= 1) {
    nowMs += interval;
    sampler.record(nowMs);
  }

  assert.deepEqual(sampler.snapshot(), {
    sample_count: 20,
    median_ms: 10,
    p95_ms: 19,
  });
});

test('frame sampler rejects invalid configuration and non-finite times', () => {
  const { createFrameIntervalSampler } = subject();

  assert.throws(
    () => createFrameIntervalSampler({ warmupMs: -1 }),
    /warmup/,
  );
  assert.throws(
    () => createFrameIntervalSampler({ maximumSamples: 0 }),
    /maximum/,
  );

  const sampler = createFrameIntervalSampler();
  assert.throws(() => sampler.record(Number.NaN), /finite/);
  assert.throws(() => sampler.record(Number.POSITIVE_INFINITY), /finite/);
});
