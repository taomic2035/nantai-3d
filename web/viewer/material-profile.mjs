export const H3_PROFILE_ID = 'h3-ai-ktx2-4k';
export const H2_PROFILE_ID = 'h2-png-1k-fallback';
export const MAX_H3_COMPRESSED_TEXTURE_BYTES = 512 * 1024 * 1024;
export const KTX2_TRANSCODER_PATH = (
  './vendor/three/examples/jsm/libs/basis/'
);

const SHA256 = /^[0-9a-f]{64}$/;
const FALLBACK_MESSAGES = Object.freeze({
  compressed_memory_budget: '压缩纹理超过 512 MiB 预算，已使用 H2 1K 回退。',
  invalid_selection_evidence: '材质配置证据不完整，已使用 H2 1K 回退。',
  ktx2_capability_unavailable: '当前渲染器无法确认 KTX2 支持，已使用 H2 1K 回退。',
  canary_descriptor_invalid: 'KTX2 探针描述无效，已使用 H2 1K 回退。',
  canary_fetch_failed: 'KTX2 探针不可用，已使用 H2 1K 回退。',
  canary_redirect_rejected: 'KTX2 探针发生未授权跳转，已使用 H2 1K 回退。',
  canary_mime_mismatch: 'KTX2 探针媒体类型不符，已使用 H2 1K 回退。',
  canary_length_mismatch: 'KTX2 探针字节数不符，已使用 H2 1K 回退。',
  canary_sha256_mismatch: 'KTX2 探针完整性校验失败，已使用 H2 1K 回退。',
  canary_verification_failed: 'KTX2 探针无法验证，已使用 H2 1K 回退。',
  canary_decode_failed: 'KTX2 探针解码失败，已使用 H2 1K 回退。',
  runtime_h3_failure: 'H3 运行时加载失败，已完整回退到 H2 1K。',
});

export class MaterialProfileError extends Error {
  constructor(code, message = code) {
    super(message);
    this.name = 'MaterialProfileError';
    this.code = code;
  }
}

function bytesToHex(buffer) {
  return [...new Uint8Array(buffer)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');
}

function contentType(response) {
  return response?.headers?.get?.('content-type')
    ?.split(';', 1)[0]
    .trim()
    .toLowerCase() ?? '';
}

function assertCanaryDescriptor(descriptor) {
  if (
    descriptor === null
    || typeof descriptor !== 'object'
    || typeof descriptor.url !== 'string'
    || descriptor.url.length === 0
    || !SHA256.test(descriptor.sha256)
    || !Number.isSafeInteger(descriptor.bytes)
    || descriptor.bytes <= 0
    || descriptor.media_type !== 'image/ktx2'
  ) {
    throw new MaterialProfileError(
      'canary_descriptor_invalid',
      'KTX2 canary descriptor is invalid',
    );
  }
}

export async function verifyKtx2Canary({
  descriptor,
  fetchFn = globalThis.fetch,
  subtle = globalThis.crypto?.subtle,
  baseUrl = globalThis.location?.href,
}) {
  assertCanaryDescriptor(descriptor);
  if (
    typeof fetchFn !== 'function'
    || typeof subtle?.digest !== 'function'
    || typeof baseUrl !== 'string'
  ) {
    throw new MaterialProfileError(
      'canary_verification_failed',
      'KTX2 canary verifier is unavailable',
    );
  }
  let expectedUrl;
  try {
    expectedUrl = new URL(descriptor.url, baseUrl).href;
  } catch {
    throw new MaterialProfileError(
      'canary_descriptor_invalid',
      'KTX2 canary URL is invalid',
    );
  }
  let response;
  try {
    response = await fetchFn(descriptor.url, {
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'error',
    });
  } catch {
    throw new MaterialProfileError(
      'canary_fetch_failed',
      'KTX2 canary request failed',
    );
  }
  if (!response?.ok || response.status !== 200) {
    throw new MaterialProfileError(
      'canary_fetch_failed',
      'KTX2 canary response is unavailable',
    );
  }
  if (response.redirected || response.url !== expectedUrl) {
    throw new MaterialProfileError(
      'canary_redirect_rejected',
      'KTX2 canary response URL changed',
    );
  }
  if (contentType(response) !== 'image/ktx2') {
    throw new MaterialProfileError(
      'canary_mime_mismatch',
      'KTX2 canary response MIME changed',
    );
  }
  const declaredLength = response.headers?.get?.('content-length');
  if (
    declaredLength !== String(descriptor.bytes)
  ) {
    throw new MaterialProfileError(
      'canary_length_mismatch',
      'KTX2 canary response length changed',
    );
  }
  let bytes;
  try {
    bytes = new Uint8Array(await response.arrayBuffer());
  } catch {
    throw new MaterialProfileError(
      'canary_fetch_failed',
      'KTX2 canary response body is unavailable',
    );
  }
  if (bytes.byteLength !== descriptor.bytes) {
    throw new MaterialProfileError(
      'canary_length_mismatch',
      'KTX2 canary body length changed',
    );
  }
  let digest;
  try {
    digest = bytesToHex(await subtle.digest('SHA-256', bytes));
  } catch {
    throw new MaterialProfileError(
      'canary_verification_failed',
      'KTX2 canary digest is unavailable',
    );
  }
  if (digest !== descriptor.sha256) {
    throw new MaterialProfileError(
      'canary_sha256_mismatch',
      'KTX2 canary digest changed',
    );
  }
  return bytes;
}

function fallbackReason(code) {
  const normalized = Object.hasOwn(FALLBACK_MESSAGES, code)
    ? code
    : 'canary_verification_failed';
  return Object.freeze({
    code: normalized,
    message: FALLBACK_MESSAGES[normalized],
  });
}

function publicSelection(profileId, reason) {
  return Object.freeze({
    profileId,
    fallbackReason: reason,
  });
}

function parseCanary(loader, bytes, timeoutMs) {
  const buffer = bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + bytes.byteLength,
  );
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer = null;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      callback(value);
    };
    timer = setTimeout(() => finish(
      reject,
      new MaterialProfileError(
        'canary_decode_failed',
        'KTX2 canary decode timed out',
      ),
    ), timeoutMs);
    try {
      loader.parse(
        buffer,
        (texture) => {
          if (settled) {
            texture?.dispose?.();
            return;
          }
          if (!texture || typeof texture.dispose !== 'function') {
            finish(
              reject,
              new MaterialProfileError(
                'canary_decode_failed',
                'KTX2 canary produced no disposable texture',
              ),
            );
            return;
          }
          finish(resolve, texture);
        },
        () => finish(
          reject,
          new MaterialProfileError(
            'canary_decode_failed',
            'KTX2 canary decode failed',
          ),
        ),
      );
    } catch {
      finish(
        reject,
        new MaterialProfileError(
          'canary_decode_failed',
          'KTX2 canary decode threw',
        ),
      );
    }
  });
}

export function createMaterialProfileController({
  createKtx2Loader = null,
  verifyAndReadCanary = (descriptor) => verifyKtx2Canary({
    descriptor,
  }),
  maxCompressedBytes = MAX_H3_COMPRESSED_TEXTURE_BYTES,
  canaryDecodeTimeoutMs = 10_000,
} = {}) {
  if (
    !Number.isSafeInteger(maxCompressedBytes)
    || maxCompressedBytes <= 0
    || !Number.isSafeInteger(canaryDecodeTimeoutMs)
    || canaryDecodeTimeoutMs <= 0
  ) {
    throw new TypeError('maxCompressedBytes must be a positive safe integer');
  }

  let state = 'unselected';
  let profileId = null;
  let reason = null;
  let loader = null;
  let selectionPromise = null;
  let rollbackPromise = null;

  const snapshot = () => Object.freeze({
    state,
    profileId,
    fallbackReason: reason,
  });

  const disposeLoader = () => {
    const current = loader;
    loader = null;
    try {
      current?.dispose?.();
    } catch {
      // Disposal is best-effort; public state remains fail-closed H2.
    }
  };

  const freezeFallback = (code, nextState = 'frozen') => {
    disposeLoader();
    state = nextState;
    profileId = H2_PROFILE_ID;
    reason = fallbackReason(code);
    return publicSelection(profileId, reason);
  };

  const selectOnce = async ({
    renderer,
    canary,
    predictedCompressedBytes,
  } = {}) => {
    if (
      !Number.isSafeInteger(predictedCompressedBytes)
      || predictedCompressedBytes < 0
      || renderer === null
      || typeof renderer !== 'object'
    ) {
      return freezeFallback('invalid_selection_evidence');
    }
    if (predictedCompressedBytes > maxCompressedBytes) {
      return freezeFallback('compressed_memory_budget');
    }
    if (
      typeof createKtx2Loader !== 'function'
      || typeof verifyAndReadCanary !== 'function'
    ) {
      return freezeFallback('ktx2_capability_unavailable');
    }
    try {
      loader = createKtx2Loader();
      if (
        !loader
        || typeof loader.setTranscoderPath !== 'function'
        || typeof loader.detectSupport !== 'function'
        || typeof loader.parse !== 'function'
      ) {
        return freezeFallback('ktx2_capability_unavailable');
      }
      loader.setTranscoderPath(KTX2_TRANSCODER_PATH);
      loader.detectSupport(renderer);
    } catch {
      return freezeFallback('ktx2_capability_unavailable');
    }

    let bytes;
    try {
      assertCanaryDescriptor(canary);
      bytes = await verifyAndReadCanary(canary);
      if (!(bytes instanceof Uint8Array)) {
        throw new MaterialProfileError(
          'canary_verification_failed',
          'KTX2 canary verifier returned invalid bytes',
        );
      }
      if (
        bytes.byteLength !== canary.bytes
        || bytes.byteLength === 0
      ) {
        throw new MaterialProfileError(
          'canary_length_mismatch',
          'KTX2 canary verifier returned wrong length',
        );
      }
    } catch (error) {
      return freezeFallback(
        error instanceof MaterialProfileError
          ? error.code
          : 'canary_verification_failed',
      );
    }

    let texture;
    try {
      texture = await parseCanary(
        loader,
        bytes,
        canaryDecodeTimeoutMs,
      );
    } catch {
      return freezeFallback('canary_decode_failed');
    }
    try {
      texture.dispose();
    } catch {
      return freezeFallback('canary_decode_failed');
    }
    state = 'frozen';
    profileId = H3_PROFILE_ID;
    reason = null;
    return publicSelection(profileId, reason);
  };

  const select = (input) => {
    if (state === 'selecting') return selectionPromise;
    if (state === 'frozen' || state === 'fallback-frozen') {
      return Promise.resolve(publicSelection(profileId, reason));
    }
    if (state !== 'unselected') {
      return Promise.reject(
        new MaterialProfileError(
          'invalid_state',
          'material profile selection is unavailable',
        ),
      );
    }
    state = 'selecting';
    selectionPromise = selectOnce(input);
    return selectionPromise;
  };

  const rollbackToFallback = ({ disposePrimary } = {}) => {
    if (state === 'rolling-back') return rollbackPromise;
    if (
      state === 'fallback-frozen'
      || (state === 'frozen' && profileId === H2_PROFILE_ID)
    ) {
      return Promise.resolve(publicSelection(profileId, reason));
    }
    if (
      state !== 'frozen'
      || profileId !== H3_PROFILE_ID
      || typeof disposePrimary !== 'function'
    ) {
      return Promise.reject(
        new MaterialProfileError(
          'invalid_state',
          'material profile rollback is unavailable',
        ),
      );
    }
    state = 'rolling-back';
    rollbackPromise = (async () => {
      try {
        await disposePrimary();
      } catch {
        // A failed H3 cleanup cannot restore H3 trust.
      }
      return freezeFallback(
        'runtime_h3_failure',
        'fallback-frozen',
      );
    })();
    return rollbackPromise;
  };

  return Object.freeze({
    select,
    rollbackToFallback,
    snapshot,
    getRuntimeLoader() {
      return (
        state === 'frozen' && profileId === H3_PROFILE_ID
          ? loader
          : null
      );
    },
  });
}
