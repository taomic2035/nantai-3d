import assert from 'node:assert/strict';
import test from 'node:test';

import { JobController } from './job-controller.mjs';

test('controller deduplicates cursor events and reports a terminal run once', async () => {
  const updates = [];
  const terminals = [];
  const responses = [
    { items: [{ id: 'run-1', status: 'running' }], events: [{ cursor: 1, run_id: 'run-1' }], cursor: 1 },
    { items: [{ id: 'run-1', status: 'succeeded' }], events: [{ cursor: 2, run_id: 'run-1' }], cursor: 2 },
    { items: [{ id: 'run-1', status: 'succeeded' }], events: [], cursor: 2 },
  ];
  const controller = new JobController({
    adapter: { listRuns: async () => responses.shift() },
    onUpdate: (runs) => updates.push(runs),
    onTerminal: (run) => terminals.push(run.id),
  });

  await controller.pollOnce({ reschedule: false });
  await controller.pollOnce({ reschedule: false });
  await controller.pollOnce({ reschedule: false });

  assert.equal(controller.cursor, 2);
  assert.equal(updates.at(-1)[0].events.length, 2);
  assert.deepEqual(terminals, ['run-1']);
});

test('controller selects active, idle, hidden, and capped backoff intervals', async () => {
  const delays = [];
  let hidden = false;
  let fail = false;
  const controller = new JobController({
    adapter: { listRuns: async () => {
      if (fail) throw new Error('offline');
      return { items: [{ id: 'run-1', status: 'running' }], events: [], cursor: 0 };
    } },
    visibility: () => (hidden ? 'hidden' : 'visible'),
    schedule: (_callback, delay) => { delays.push(delay); return delays.length; },
    cancelSchedule: () => {},
  });
  controller.stopped = false;
  await controller.pollOnce();
  hidden = true;
  await controller.pollOnce();
  fail = true;
  for (let index = 0; index < 8; index += 1) {
    await assert.rejects(() => controller.pollOnce(), /offline/);
  }
  assert.equal(delays[0], 1_000);
  assert.equal(delays[1], 15_000);
  assert.equal(delays.at(-1), 30_000);
});
