function randomId() {
  return globalThis.crypto?.randomUUID?.() ?? `viewer-${Date.now()}-${Math.random()}`;
}

export class StudioViewerBridge {
  constructor({
    windowObject = globalThis.window,
    frameWindow,
    origin = windowObject?.location?.origin,
    idFactory = randomId,
    timeoutMs = 0,
    onStatus = () => {},
  }) {
    this.windowObject = windowObject;
    this.frameWindow = frameWindow;
    this.origin = origin;
    this.idFactory = idFactory;
    this.timeoutMs = timeoutMs;
    this.onStatus = onStatus;
    this.status = 'waiting';
    this.capabilities = { commands: [] };
    this.pending = new Map();
    this.boundHandle = (event) => this.handleMessage(event);
    this.timer = null;
  }

  start() {
    this.windowObject?.addEventListener?.('message', this.boundHandle);
    if (this.timeoutMs > 0) {
      this.timer = setTimeout(() => {
        if (this.status !== 'waiting') return;
        this.status = 'degraded';
        this.onStatus(this.status, this.capabilities);
      }, this.timeoutMs);
    }
  }

  dispose() {
    this.windowObject?.removeEventListener?.('message', this.boundHandle);
    if (this.timer) clearTimeout(this.timer);
    for (const { reject } of this.pending.values()) reject(new Error('viewer bridge disposed'));
    this.pending.clear();
  }

  supports(command) {
    return this.status === 'ready'
      && Array.isArray(this.capabilities.commands)
      && this.capabilities.commands.includes(command);
  }

  command(type, payload = {}) {
    if (this.status !== 'ready') return Promise.reject(new Error('viewer is not ready'));
    if (!this.supports(type)) return Promise.reject(new Error(`unsupported viewer command: ${type}`));
    const requestId = this.idFactory();
    const message = {
      channel: 'nantai-viewer', schema_version: 1, type,
      request_id: requestId, payload,
    };
    const promise = new Promise((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
    });
    this.frameWindow.postMessage(message, this.origin);
    return promise;
  }

  handleMessage(event) {
    if (event.origin !== this.origin || event.source !== this.frameWindow) return;
    const message = event.data;
    if (!message || message.channel !== 'nantai-viewer' || message.schema_version !== 1) return;
    if (message.type === 'ready') {
      this.status = 'ready';
      this.capabilities = message.payload?.capabilities ?? { commands: [] };
      if (this.timer) clearTimeout(this.timer);
      this.onStatus(this.status, this.capabilities);
      return;
    }
    if (message.type === 'capabilitiesChanged') {
      if (this.status !== 'ready') return;
      this.capabilities = message.payload?.capabilities ?? { commands: [] };
      this.onStatus(this.status, this.capabilities);
      return;
    }
    const pending = this.pending.get(message.request_id);
    if (!pending) return;
    this.pending.delete(message.request_id);
    if (message.type === 'error') {
      pending.reject(new Error(message.payload?.message ?? message.payload?.code ?? 'viewer error'));
    } else {
      pending.resolve(message.payload?.result);
    }
  }
}
