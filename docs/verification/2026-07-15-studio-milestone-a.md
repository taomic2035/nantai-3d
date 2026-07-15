# Studio Milestone A Verification

Date: 2026-07-15
Scope: read-only capability negotiation, DAG truth, empty/active/failed navigation, and mock/local consistency.

## Automated evidence

- `python -m pytest -q`: **259 passed, 8 skipped**.
- `node --test web/studio/*.test.mjs`: **55 passed**.
- `node --test web/viewer/*.test.mjs`: **32 passed**.
- `git diff --check`: no whitespace errors; only expected CRLF-to-LF notices from the pending repository `.gitattributes` work.
- `GET /api/capabilities`: schema 1, `mode=read-only`, all four commands disabled, `Cache-Control: no-store`.
- `POST /api/jobs`: remains HTTP 405 after capability discovery.

The eight Python skips are capability-based Windows symlink skips. They are not counted as evidence that POSIX symlink cases passed on this host.

## Browser evidence

Validated in the Codex in-app browser against a fresh local server on loopback. Both `?adapter=mock` and `?adapter=local` were exercised.

| Scenario | Observed result |
|---|---|
| Default mock | Global service mode visibly reports read-only; rail shows two artifact branches and a Review join instead of numbered linear stages. |
| Empty mock | Primary action is “查看输入目录”; click selects Sources and moves focus to the empty-state card; `./input` is shown; rescan is disabled. |
| Empty local | Same interaction selects Sources and focuses the card; absolute path `D:\vibecoding\nantai\input` is shown; rescan is disabled with the server reason. |
| Missing reconstruction | “开始混合重建” is disabled before click; run count remains unchanged. |
| Running | “查看任务进度” selects Reconstruct, expands the drawer, and focuses the drawer toggle. |
| Failed | “查看失败原因” selects Sources and the active `run-mock-failed`; final event shows `broken_clip.mp4: 0 frames decoded`. |
| Assets, mock | Validate action is disabled with “Mock scenarios never authorize project writes.” |
| Assets, local | Validate action is disabled with “Job execution is not enabled in this Studio milestone.” |

No browser console errors were reported in either adapter mode.

## Architecture review

The Opus architecture role completed a second review after the fail-closed and DAG fixes. It reported no remaining or new Critical/Important findings. Capability discovery fallback, Milestone A write rejection, command provenance, and Compose world-evidence validation were all confirmed closed.

## Truth boundary

Milestone A does not make Studio writable. It introduces no job token, subprocess, mutation route, cancellation, retry execution, or publication code. It reads the existing run ledger but adds no ledger mutation path. The capability and action models include future fields so later milestones can advertise functionality, but this milestone always fails closed to read-only.

Cross-platform UI behavior was not browser-tested outside Windows. Server and front-end unit tests are platform-neutral; that is weaker evidence than a future CI/browser matrix and must not be presented as multi-platform browser validation.
