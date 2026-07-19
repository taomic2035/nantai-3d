export function createFrameIntervalSampler({
  warmupMs = 10_000,
  maximumSamples = 3_600,
} = {}) {
  if (!Number.isFinite(warmupMs) || warmupMs < 0) {
    throw new TypeError('frame sampler warmup must be a finite non-negative number');
  }
  if (!Number.isSafeInteger(maximumSamples) || maximumSamples <= 0) {
    throw new TypeError('frame sampler maximum samples must be a positive integer');
  }

  const intervals = [];
  let startedAt = null;
  let previousAt = null;

  return {
    record(nowMs) {
      if (!Number.isFinite(nowMs)) {
        throw new TypeError('frame time must be finite');
      }
      if (startedAt === null) startedAt = nowMs;
      if (previousAt !== null && nowMs - startedAt >= warmupMs) {
        intervals.push(nowMs - previousAt);
        if (intervals.length > maximumSamples) intervals.shift();
      }
      previousAt = nowMs;
    },

    snapshot() {
      const sorted = [...intervals].sort((a, b) => a - b);
      const medianIndex = Math.floor((sorted.length - 1) / 2);
      const p95Index = Math.max(
        0,
        Math.ceil(sorted.length * 0.95) - 1,
      );
      return {
        sample_count: sorted.length,
        median_ms: sorted.length ? sorted[medianIndex] : null,
        p95_ms: sorted.length ? sorted[p95Index] : null,
      };
    },
  };
}
