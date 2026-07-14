import { LocalStudioAdapter } from './local-adapter.mjs';
import { MockStudioAdapter } from './mock-adapter.mjs';

/** Select local truth when available; mock fallback is always labelled. */
export async function selectStudioAdapter({
  search = globalThis.location?.search ?? '',
  fetchImpl = globalThis.fetch,
  storage = globalThis.localStorage,
  baseUrl = globalThis.location?.origin ?? '',
} = {}) {
  const requested = new URLSearchParams(search).get('adapter') ?? 'auto';
  if (!['auto', 'local', 'mock'].includes(requested)) {
    throw new Error(`unsupported adapter mode: ${requested}`);
  }
  if (requested === 'mock') {
    return {
      adapter: new MockStudioAdapter({ storage }),
      fallbackReason: 'forced by query',
    };
  }

  const local = new LocalStudioAdapter({ fetchImpl, baseUrl });
  try {
    await local.loadProject();
    return { adapter: local, fallbackReason: null };
  } catch (error) {
    if (requested === 'local') {
      throw new Error(`local service unavailable: ${error.message}`, { cause: error });
    }
    return {
      adapter: new MockStudioAdapter({ storage }),
      fallbackReason: `local service unavailable: ${error.message}`,
    };
  }
}
