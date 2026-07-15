# Nantai Studio interaction audit — 2026-07-15

## Scope

This is a read-only interaction audit of the current `main` Studio shell. It covers the
global primary action, pipeline navigation, local adapter capabilities, and run-history
controls. It does not approve a writable job API and does not modify the concurrent
`pipeline/assets.py` Windows portability work.

## Conclusion

The Studio explains artifact truth well, but it is not yet an executable workflow. Four
interaction gaps prevent a user from reliably moving from an empty project or failed run
to a new result. The current UI should continue to be described as a read-only local
snapshot until these gaps and the server execution boundary are designed and implemented.

## Findings

### P0 — Empty-project primary action is a dead end

`derivePrimaryAction()` returns `inspect-sources` when no images or videos exist. The
primary-action click handler has no `inspect-sources` branch, so the action falls through
to the default review view instead of opening the Sources stage. The derivation test does
not include the empty-source case and there is no click-through interaction test.

User impact: the first action shown to a new user does not lead to input guidance.

Required acceptance evidence:

- An empty local and mock snapshot derives `inspect-sources`.
- Clicking the button selects and focuses the Sources stage.
- The Sources inspector presents a concrete supported input path; it must not imply that
  the browser uploaded or copied files when the server did not.

### P0 — Writable actions are presented by a read-only adapter

The local adapter exposes `startJob()` and `validateAssetCandidate()` as POST requests,
while the current server rejects every mutating method with HTTP 405 and explicitly says
that no job was started. The Assets inspector nevertheless always renders “验证 11 个素材”,
and the global primary action can derive “开始混合重建”.

User impact: the UI offers operations that are guaranteed to fail in the default local
mode. The error is technically honest after the click, but capability availability is
misrepresented before the click.

Required acceptance evidence:

- Adapter capabilities explicitly distinguish snapshot-read, job-write, asset-validate,
  cancel, and retry.
- Unsupported actions are either absent or disabled with a visible reason before click.
- Enabling a write action requires a server-advertised capability; adapter kind or method
  presence is not sufficient evidence.

### P1 — Active jobs have no operational control

The mock adapter implements `cancelJob()`, and run statuses include queued, running, and
canceled. The job drawer only selects runs and renders events; it has no cancel action.
`derivePrimaryAction()` also has no queued/running branch, so the global action may continue
to offer review or reconstruction while a write job is active.

User impact: users cannot stop an expensive or incorrect reconstruction and cannot tell
whether another write is permitted.

Required acceptance evidence:

- A queued/running run changes the primary action to “查看任务进度”.
- A server-advertised cancel capability exposes a scoped cancel action with confirmation.
- A single-writer conflict is visible before another job is submitted.
- Canceling never promotes partial output to the current artifact.

### P1 — Failure history cannot be retried from the interface

The ledger model already preserves `retry_of` and can create a new retry record. The
failure primary action only opens Sources and the run drawer; it does not expose retry,
parameter review, or a direct link to the failed phase.

User impact: the interface explains failure but does not provide a recovery path, forcing
the user back to CLI knowledge.

Required acceptance evidence:

- A failed run opens the failed event and the corresponding pipeline stage.
- Retry shows the previous parameters, allows only schema-approved overrides, creates a
  new run, and preserves the failed record.
- Invalid or stale retry preconditions fail before process launch with a structured error.

## Recommended interaction model

Use a stage-driven local task workspace rather than one opaque “run everything” action.
The six existing stages remain the information architecture. Each stage owns its supported
action, preconditions, parameter summary, latest run, and resulting artifacts. The global
primary action is only a shortcut to the next valid stage action. All write controls are
derived from server-advertised capabilities and current single-writer state.

## Verification boundary

This audit is supported by static code and contract inspection. It is not browser runtime
evidence and does not claim the four interactions were fixed. Implementation must add
model tests, DOM interaction tests, HTTP contract tests, and a browser pass against the
actual local adapter before closure.
