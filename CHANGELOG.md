# Changelog

## 0.2.0

- Added `demo` command and checked-in quickstart example for first-time users.
- Added `scan` command for local public-safety and pack-readiness checks.
- Added scan baselines, ignore codes, and CI public-safety scanning.
- Added `release-check` command for schema, scan, tests, and demo smoke.
- Added incremental `build --base` pack creation for unchanged file reuse.
- Added automatic incremental snapshot and memory runs using the latest pack.
- Added snapshot `stats` reports for incremental reuse and storage savings.
- Added `diff-context` for source-backed changed-files agent context.
- Added optional memory-cycle diff context artifacts with `memory --diff-context`.
- Added public launch docs, issue templates, badges, and project `.gitignore`.
- Added `memory` cycle command for snapshot, handoff, doctor, prune, and timeline.
- Added `init` config profiles through `repomori.toml`.
- Added dependency-free JSON-lines `agent` bridge.
- Added dependency-free MCP stdio bridge over the same local agent methods.
- Added snapshot doctor and safe prune operations.
- Added schema catalog command and schema/protocol documentation.
- Added source-available company license, commercial-use notice, contribution terms, and public release checklist.

## 0.1.0

- Initial local RepoMori pack format.
- Build, query, restore, verify, context, capsule, handoff, compare, eval, and benchmark commands.
