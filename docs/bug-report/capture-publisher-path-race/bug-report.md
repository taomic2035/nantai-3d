# Capture publisher path-replacement race

Date: 2026-07-18
Reporter: Codex quality-gate review
Status: fixed before the publisher's first commit

## Diagnostic capsule

| Field | Evidence |
|---|---|
| Symptom | Replacing `.nantai-studio/artifacts/capture` with a directory symlink at `before_capture_move` let the test durability backend move the prepared bundle outside the project before post-move verification rejected it. |
| Reproduction | `test_capture_path_replacement_race_cannot_publish_outside_project` deterministically replaced the root at the final fault boundary. |
| Root cause | The publisher validated managed roots before bundle preparation but did not revalidate the capture root and fixed work path immediately before the absent-to-present move. |
| Diagnostic strategy | Trace the destination path from initial root validation through the fault boundary and compare it with the existing B1 promoter's final `_gate()` call. |
| Timeout strategy | If one focused reproduction did not expose an outside write, inspect platform-specific rename semantics before changing production code. |
| Warning signal | Any fix that merely catches the post-move verification error while leaving bytes outside the project is invalid. |
| User-visible correction | No shipped behavior changed; the unsafe implementation was caught before commit. |
| Acceptance | The focused race test passes, the outside directory remains empty, no capture row is committed, and the Studio regression remains green. |

## Fix

Revalidate the real capture artifact root and the exact fixed work bundle after
the last fault/concurrency boundary and immediately before the durable move.
The quarantine root is likewise revalidated immediately before moving a
damaged prepared-intent orphan.

This follows the repository's existing B1 defense-in-depth pattern. It narrows
the path-replacement window around path-based filesystem APIs; it does not claim
an OS-handle-relative rename primitive that the current Win32 backend does not
provide.
