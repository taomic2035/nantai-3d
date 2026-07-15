const TERMINAL = new Set(['succeeded', 'failed', 'canceled']);

export class JobController {
  constructor({
    adapter,
    onUpdate = () => {},
    onTerminal = () => {},
    visibility = () => globalThis.document?.visibilityState ?? 'visible',
    schedule = (callback, delay) => globalThis.setTimeout(callback, delay),
    cancelSchedule = (timer) => globalThis.clearTimeout(timer),
  }) {
    this.adapter = adapter;
    this.onUpdate = onUpdate;
    this.onTerminal = onTerminal;
    this.visibility = visibility;
    this.schedule = schedule;
    this.cancelSchedule = cancelSchedule;
    this.cursor = 0;
    this.events = new Map();
    this.statuses = new Map();
    this.terminalRuns = new Set();
    this.failures = 0;
    this.timer = null;
    this.stopped = true;
  }

  #delay(runs) {
    if (this.visibility() === 'hidden') return 15_000;
    if (runs.some((run) => ['queued', 'running'].includes(run.status))) return 1_000;
    return 5_000;
  }

  #schedule(delay) {
    if (this.stopped) return;
    if (this.timer !== null) this.cancelSchedule(this.timer);
    this.timer = this.schedule(() => {
      this.pollOnce().catch(() => {});
    }, delay);
  }

  async pollOnce({ reschedule = true } = {}) {
    try {
      const envelope = await this.adapter.listRuns(this.cursor);
      const events = Array.isArray(envelope.events) ? envelope.events : [];
      events.forEach((event) => {
        const current = this.events.get(event.run_id) ?? [];
        if (!current.some((item) => item.cursor === event.cursor)) current.push(event);
        this.events.set(event.run_id, current);
      });
      this.cursor = Number.isInteger(envelope.cursor) ? envelope.cursor : this.cursor;
      const runs = envelope.items.map((run) => ({
        ...run,
        events: [...(this.events.get(run.id) ?? [])],
      }));
      this.failures = 0;
      this.onUpdate(runs, envelope);
      runs.forEach((run) => {
        const previous = this.statuses.get(run.id);
        if (previous && !TERMINAL.has(previous)
          && TERMINAL.has(run.status) && !this.terminalRuns.has(run.id)) {
          this.terminalRuns.add(run.id);
          this.onTerminal(run);
        }
        this.statuses.set(run.id, run.status);
      });
      if (reschedule) this.#schedule(this.#delay(runs));
      return runs;
    } catch (error) {
      this.failures += 1;
      if (reschedule) this.#schedule(Math.min(30_000, 1_000 * (2 ** this.failures)));
      throw error;
    }
  }

  start() {
    if (!this.stopped) return;
    this.stopped = false;
    this.#schedule(0);
  }

  stop() {
    this.stopped = true;
    if (this.timer !== null) this.cancelSchedule(this.timer);
    this.timer = null;
  }
}
