# RepoMori Compatibility Runbook

`compat` checks whether RepoMori's current reader, handoff, agent, and MCP
contracts still agree.

```powershell
python -m repomori compat D:\Dev\RepoMori\.repomori-packs\latest.repomori --handoff D:\handoffs\repo --verify-pack --json
python -m repomori compat --snapshot-dir D:\Dev\RepoMori\.repomori-packs --format markdown --out D:\Dev\RepoMori\compat.md
python -m repomori contract-check --fixture D:\Dev\RepoMori\tests\fixtures\compat-contracts.json --format markdown --out D:\Dev\RepoMori\contract-diff.md
```

It is local-only: no model calls, no provider config, and no network calls.

## When Compat Fails

- `pack_exists` or `pack_input`: the pack path is wrong, missing, or no latest
  snapshot pack can be resolved from `--snapshot-dir`. Run `memory` first or pass
  the exact `.repomori` path.
- `pack_schema`: the pack metadata schema does not match the current
  `repomori.pack.v1` reader expectation. This usually means a format migration
  needs an explicit reader compatibility path.
- `pack_verification`: exact chunk/file recovery failed. Treat this as a pack
  integrity problem before trusting context, handoff, or capsule output.
- `handoff_integrity`: the handoff manifest, artifact hashes, artifact sizes, JSON
  parseability, or copied-pack verification no longer matches the directory.
  Rebuild the handoff or inspect the reported artifact path.
- `handoff_schemas`: a handoff JSON artifact uses an unexpected
  `schema_version`, or a required core artifact is missing. This usually means a
  handoff writer changed without a matching reader/doc/test update.
- `schema_catalog`: a public schema name required by agent workflows is missing
  from `schema_catalog`.
- `agent_methods`: a required stdio agent method was removed or renamed. Update
  callers intentionally or restore the method alias.
- `mcp_tools`: a required MCP tool name was removed or renamed. Update MCP client
  docs/configs intentionally or restore the tool alias.

## Contract Fixture

The test fixture at `tests/fixtures/compat-contracts.json` records the expected
public schema versions, agent methods, MCP tool names, and full compat check
order. If a deliberate contract change is made, update the code, docs, and this
fixture together so CI records the decision instead of silently drifting.

`contract-check` compares that fixture with the current runtime and explains:

- added or removed schema versions
- added or removed agent methods
- added or removed MCP tools
- compat check order changes
- release-health contract artifact changes

If `contract-check` fails, decide whether the change is intentional. Restore the
removed name or add a compatibility alias when old agents should keep working.
When the change is intentional, update the fixture and docs in the same commit.

## Release Health Artifacts

When `release-health` writes artifacts, it now emits:

- `release-health.json`
- `release-health.md`
- `compat.json`
- `compat.md`
- `contract-check.json`
- `contract-check.md`
- `baseline-drift.jsonl` when drift logging is enabled

Open `contract-check.md` first for public contract drift, then `compat.md` for
runtime pack/handoff/bridge compatibility. The JSON files contain exact diff
lists and artifact paths.
