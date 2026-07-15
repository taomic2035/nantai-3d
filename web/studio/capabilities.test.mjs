import assert from 'node:assert/strict';
import test from 'node:test';

import {
  KNOWN_COMMANDS,
  commandCapability,
  normalizeCapabilities,
} from './capabilities.mjs';

const reason = 'Job execution is not enabled in this Studio milestone.';

function document(mode = 'read-only') {
  return {
    schema_version: 1,
    mode,
    reason,
    request_token: mode === 'read-write' ? 't'.repeat(43) : null,
    single_writer: true,
    commands: Object.fromEntries(KNOWN_COMMANDS.map((command) => [command, {
      enabled: mode === 'read-write',
      cancel: mode === 'read-write',
      retry: mode === 'read-write',
      reason,
    }])),
  };
}

test('read-write mode requires a strong request token and single-writer lease', () => {
  for (const requestToken of [null, '', 'short-token']) {
    const raw = document('read-write');
    raw.request_token = requestToken;
    assert.equal(normalizeCapabilities(raw).mode, 'read-only');
  }

  const multipleWriters = document('read-write');
  multipleWriters.single_writer = false;
  assert.equal(normalizeCapabilities(multipleWriters).mode, 'read-only');
});

test('read-only mode force-disables every known command with a reason', () => {
  const raw = document();
  raw.commands.reconstruct.enabled = true;
  const normalized = normalizeCapabilities(raw);

  assert.equal(normalized.mode, 'read-only');
  for (const command of KNOWN_COMMANDS) {
    assert.deepEqual(commandCapability(normalized, command), {
      enabled: false, cancel: false, retry: false, reason,
    });
  }
});

test('malformed capability documents fail closed as one read-only surface', () => {
  for (const raw of [null, {}, { schema_version: 2 }, document('surprise')]) {
    const normalized = normalizeCapabilities(raw);
    assert.equal(normalized.mode, 'read-only');
    assert.ok(KNOWN_COMMANDS.every(
      (command) => commandCapability(normalized, command).enabled === false,
    ));
    assert.match(normalized.reason, /invalid|unavailable/i);
  }

  const missingCommands = document('read-write');
  delete missingCommands.commands;
  assert.equal(normalizeCapabilities(missingCommands).mode, 'read-only');
});

test('callers can forbid writes even when a fixture claims read-write', () => {
  const normalized = normalizeCapabilities(document('read-write'), {
    allowWrite: false,
    fallbackReason: 'Mock scenarios never authorize project writes.',
  });
  assert.equal(normalized.mode, 'read-only');
  assert.equal(normalized.request_token, null);
  assert.match(normalized.reason, /mock scenarios/i);
});

test('unknown commands are always disabled', () => {
  assert.deepEqual(commandCapability(normalizeCapabilities(document()), 'shell'), {
    enabled: false,
    cancel: false,
    retry: false,
    reason: 'Command is not advertised by this Studio capability schema.',
  });
});
