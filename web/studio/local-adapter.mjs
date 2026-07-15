import { normalizeCapabilities, readOnlyCapabilities } from './capabilities.mjs';

const CAPABILITY_FAILURE_REASON = 'Capability discovery failed; Studio remains read-only.';
const LOCAL_WRITE_FAILURE_REASON = 'Local write capability is unavailable.';

/** Read-only-first adapter for the local Nantai Studio HTTP service. */
export class LocalStudioAdapter {
  constructor({
    fetchImpl = globalThis.fetch,
    baseUrl = globalThis.location?.origin ?? '',
    requestIdFactory = () => globalThis.crypto?.randomUUID?.()
      ?? `request-browser-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  } = {}) {
    if (typeof fetchImpl !== 'function') throw new TypeError('fetch implementation required');
    this.kind = 'local';
    // Keep browser-native fetch detached from the adapter instance receiver.
    this.fetchImpl = (...args) => fetchImpl(...args);
    this.baseUrl = String(baseUrl).replace(/\/$/, '');
    this.requestIdFactory = requestIdFactory;
    this.requestToken = null;
  }

  #url(path) {
    return `${this.baseUrl}${path}`;
  }

  async #request(path, { method = 'GET', body, headers = {} } = {}) {
    const requestHeaders = { ...headers };
    if (body !== undefined) requestHeaders['content-type'] = 'application/json';
    const response = await this.fetchImpl(this.#url(path), {
      method,
      headers: Object.keys(requestHeaders).length ? requestHeaders : undefined,
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
    this.requestToken = null;
    try {
      const capabilities = await this.#request('/api/capabilities');
      const normalized = normalizeCapabilities(capabilities, {
        allowWrite: true,
        fallbackReason: LOCAL_WRITE_FAILURE_REASON,
      });
      if (normalized.mode === 'read-write') this.requestToken = normalized.request_token;
      return normalized;
    } catch {
      return readOnlyCapabilities(CAPABILITY_FAILURE_REASON);
    }
  }

  async listRuns(cursor) {
    const suffix = cursor === undefined ? '' : `?cursor=${encodeURIComponent(cursor)}`;
    const envelope = await this.#request(`/api/runs${suffix}`);
    if (!Array.isArray(envelope.items)) {
      throw new Error('local runs response must contain an items array');
    }
    return envelope;
  }

  async startJob(command, parameters = {}) {
    if (!this.requestToken) throw new Error('local write authorization is unavailable');
    return this.#request('/api/jobs', {
      method: 'POST',
      body: { command, parameters },
      headers: {
        'x-nantai-token': this.requestToken,
        'x-request-id': this.requestIdFactory(),
      },
    });
  }

  async loadRun(runId) {
    if (typeof runId !== 'string' || !runId) throw new TypeError('run ID required');
    return this.#request(`/api/runs/${encodeURIComponent(runId)}`);
  }

  async validateAssetCandidate(candidate) {
    return this.#request('/api/assets/validate', {
      method: 'POST', body: candidate,
    });
  }
}
