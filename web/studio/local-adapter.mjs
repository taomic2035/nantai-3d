import { normalizeCapabilities, readOnlyCapabilities } from './capabilities.mjs';

const CAPABILITY_FAILURE_REASON = 'Capability discovery failed; Studio remains read-only.';
const MILESTONE_A_REASON = 'Milestone A never authorizes project writes.';

/** Read-only-first adapter for the local Nantai Studio HTTP service. */
export class LocalStudioAdapter {
  constructor({
    fetchImpl = globalThis.fetch,
    baseUrl = globalThis.location?.origin ?? '',
  } = {}) {
    if (typeof fetchImpl !== 'function') throw new TypeError('fetch implementation required');
    this.kind = 'local';
    // Keep browser-native fetch detached from the adapter instance receiver.
    this.fetchImpl = (...args) => fetchImpl(...args);
    this.baseUrl = String(baseUrl).replace(/\/$/, '');
  }

  #url(path) {
    return `${this.baseUrl}${path}`;
  }

  async #request(path, { method = 'GET', body } = {}) {
    const response = await this.fetchImpl(this.#url(path), {
      method,
      headers: body === undefined ? undefined : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let payload;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      const detail = payload?.error ?? {};
      const error = new Error(detail.message ?? `local service request failed (${response.status})`);
      error.code = detail.code ?? 'http-error';
      error.status = response.status;
      error.details = detail.details ?? null;
      throw error;
    }
    if (!payload || typeof payload !== 'object') {
      throw new Error('local service returned invalid JSON');
    }
    return payload;
  }

  async loadProject() {
    const snapshot = await this.#request('/api/project');
    if (snapshot.schema_version !== 2) {
      throw new Error('local project must use schema_version 2');
    }
    return snapshot;
  }

  async loadCapabilities() {
    try {
      const capabilities = await this.#request('/api/capabilities');
      return normalizeCapabilities(capabilities, {
        allowWrite: false,
        fallbackReason: MILESTONE_A_REASON,
      });
    } catch {
      return readOnlyCapabilities(CAPABILITY_FAILURE_REASON);
    }
  }

  async listRuns() {
    const envelope = await this.#request('/api/runs');
    if (!Array.isArray(envelope.items)) {
      throw new Error('local runs response must contain an items array');
    }
    return envelope;
  }

  async startJob(command, parameters = {}) {
    return this.#request('/api/jobs', { method: 'POST', body: { command, parameters } });
  }

  async validateAssetCandidate(candidate) {
    return this.#request('/api/assets/validate', {
      method: 'POST', body: candidate,
    });
  }
}
