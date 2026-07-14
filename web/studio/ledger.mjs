const TERMINAL = new Set(['succeeded', 'failed', 'canceled']);
const STATUS_RANK = new Map([
  ['queued', 0], ['running', 1], ['failed', 2], ['canceled', 2], ['succeeded', 3],
]);

function defaultId() {
  return globalThis.crypto?.randomUUID?.() ?? `run-${Date.now()}-${Math.random()}`;
}

export class RunLedger {
  constructor({
    storage = globalThis.localStorage,
    key = 'nantai-studio-mock-ledger-v1',
    maxRuns = 50,
    maxEventsPerRun = 200,
    idFactory = defaultId,
    now = () => new Date().toISOString(),
  } = {}) {
    if (!storage) throw new Error('RunLedger requires a storage adapter');
    this.storage = storage;
    this.key = key;
    this.maxRuns = maxRuns;
    this.maxEventsPerRun = maxEventsPerRun;
    this.idFactory = idFactory;
    this.now = now;
    this.document = this.#load();
  }

  #load() {
    try {
      const raw = this.storage.getItem(this.key);
      if (!raw) return { schema_version: 1, runs: [] };
      const parsed = JSON.parse(raw);
      if (parsed.schema_version !== 1 || !Array.isArray(parsed.runs)) {
        return { schema_version: 1, runs: [] };
      }
      return parsed;
    } catch {
      return { schema_version: 1, runs: [] };
    }
  }

  #save() {
    this.storage.setItem(this.key, JSON.stringify(this.document));
  }

  #prune() {
    this.document.runs.sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
    if (this.document.runs.length <= this.maxRuns) return;
    const newestFirst = (a, b) => String(b.started_at).localeCompare(String(a.started_at));
    const active = this.document.runs
      .filter((item) => !TERMINAL.has(item.status)).sort(newestFirst);
    const terminal = this.document.runs
      .filter((item) => TERMINAL.has(item.status)).sort(newestFirst);
    const keptActive = active.slice(0, this.maxRuns);
    const terminalSlots = Math.max(0, this.maxRuns - keptActive.length);
    this.document.runs = [...keptActive, ...terminal.slice(0, terminalSlots)];
  }

  listRuns() {
    return structuredClone(this.document.runs)
      .sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
  }

  getRun(id) {
    const item = this.document.runs.find((candidate) => candidate.id === id);
    return item ? structuredClone(item) : null;
  }

  upsertRun(record) {
    if (!record?.id) throw new Error('run id is required');
    const index = this.document.runs.findIndex((item) => item.id === record.id);
    const next = structuredClone({ ...record, events: record.events ?? [] });
    if (index < 0) {
      this.document.runs.push(next);
    } else {
      const current = this.document.runs[index];
      const currentRank = STATUS_RANK.get(current.status) ?? -1;
      const nextRank = STATUS_RANK.get(next.status) ?? -1;
      if (current.status === 'succeeded' && nextRank < currentRank) return this.getRun(record.id);
      const events = current.events ?? [];
      this.document.runs[index] = { ...current, ...next, events };
    }
    this.#prune();
    this.#save();
    return this.getRun(record.id);
  }

  appendEvent(runId, event) {
    const run = this.document.runs.find((item) => item.id === runId);
    if (!run) throw new Error(`unknown run: ${runId}`);
    run.events ??= [];
    if (run.events.some((item) => item.id === event.id)) return this.getRun(runId);
    run.events.push(structuredClone(event));
    run.events.sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
    run.events = run.events.slice(-this.maxEventsPerRun);
    run.last_event_id = event.id;
    this.#save();
    return this.getRun(runId);
  }

  retry(runId, parameterOverrides = {}) {
    const parent = this.document.runs.find((item) => item.id === runId);
    if (!parent) throw new Error(`unknown run: ${runId}`);
    const retried = {
      ...structuredClone(parent),
      id: this.idFactory(),
      status: 'queued',
      retry_of: parent.id,
      parameters: { ...(parent.parameters ?? {}), ...parameterOverrides },
      started_at: this.now(),
      finished_at: null,
      artifact_ids: [],
      last_event_id: null,
      events: [],
    };
    return this.upsertRun(retried);
  }

  clear() {
    this.document = { schema_version: 1, runs: [] };
    this.storage.removeItem(this.key);
  }
}
