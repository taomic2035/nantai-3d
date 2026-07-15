export const KNOWN_COMMANDS = Object.freeze([
  'ingest', 'reconstruct', 'world', 'validate-assets',
]);

const INVALID_REASON = 'Studio capabilities are unavailable or invalid.';
const UNKNOWN_COMMAND_REASON = 'Command is not advertised by this Studio capability schema.';

function disabledCommand(reason) {
  return { enabled: false, cancel: false, retry: false, reason };
}

export function readOnlyCapabilities(reason) {
  const safeReason = typeof reason === 'string' && reason.trim() ? reason : INVALID_REASON;
  return {
    schema_version: 1,
    mode: 'read-only',
    reason: safeReason,
    request_token: null,
    single_writer: true,
    commands: Object.fromEntries(
      KNOWN_COMMANDS.map((command) => [command, disabledCommand(safeReason)]),
    ),
  };
}

export function normalizeCapabilities(raw, {
  allowWrite = true,
  fallbackReason = INVALID_REASON,
} = {}) {
  const validDocument = raw && typeof raw === 'object'
    && raw.schema_version === 1
    && (raw.mode === 'read-only' || raw.mode === 'read-write')
    && raw.commands && typeof raw.commands === 'object'
    && KNOWN_COMMANDS.every((command) => (
      raw.commands[command] && typeof raw.commands[command] === 'object'
    ));
  if (!validDocument) return readOnlyCapabilities(fallbackReason);
  if (!allowWrite && raw.mode === 'read-write') return readOnlyCapabilities(fallbackReason);

  const rootReason = typeof raw.reason === 'string' && raw.reason.trim()
    ? raw.reason : fallbackReason;
  if (raw.mode === 'read-only') return readOnlyCapabilities(rootReason);
  const validWriteLease = raw.single_writer === true
    && typeof raw.request_token === 'string'
    && raw.request_token.length >= 43;
  if (!validWriteLease) return readOnlyCapabilities(fallbackReason);

  return {
    schema_version: 1,
    mode: 'read-write',
    reason: rootReason,
    request_token: raw.request_token,
    single_writer: true,
    commands: Object.fromEntries(KNOWN_COMMANDS.map((command) => {
      const advertised = raw.commands[command];
      const reason = typeof advertised.reason === 'string' && advertised.reason.trim()
        ? advertised.reason : rootReason;
      return [command, {
        enabled: advertised.enabled === true,
        cancel: advertised.cancel === true,
        retry: advertised.retry === true,
        reason,
      }];
    })),
  };
}

export function commandCapability(capabilities, command) {
  if (!KNOWN_COMMANDS.includes(command)) return disabledCommand(UNKNOWN_COMMAND_REASON);
  const value = capabilities?.commands?.[command];
  if (!value || typeof value !== 'object') {
    return disabledCommand(capabilities?.reason ?? INVALID_REASON);
  }
  return {
    enabled: value.enabled === true,
    cancel: value.cancel === true,
    retry: value.retry === true,
    reason: typeof value.reason === 'string' && value.reason.trim()
      ? value.reason : capabilities?.reason ?? INVALID_REASON,
  };
}
