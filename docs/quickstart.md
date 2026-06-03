# RepoMori Quickstart

RepoMori creates compact, source-backed repository memory for local agents and tools.

## Try It In 60 Seconds

```powershell
python -m repomori demo --out D:\Temp\repomori-demo --force --json
```

That command creates a tiny demo repository, builds `demo.repomori`, verifies the pack, builds context, runs a memory cycle, and checks the MCP tool bridge.

Inspect the output:

```powershell
python -m repomori query D:\Temp\repomori-demo\demo.repomori "sqlite connect Store" --json
python -m repomori context D:\Temp\repomori-demo\demo.repomori "sqlite connect Store" --out D:\Temp\repomori-demo\context.md
python -m repomori timeline D:\Temp\repomori-demo\packs --format json
```

## Use Your Own Repository

```powershell
python -m repomori scan D:\Dev\YourRepo --public-release --json
python -m repomori scan D:\Dev\YourRepo --public-release --write-baseline D:\Dev\YourRepo\.repomori-scan-baseline.json --json
python -m repomori init D:\Dev\YourRepo --out-dir D:\Dev\YourRepo\packs
python -m repomori memory --config D:\Dev\YourRepo\repomori.toml --json
python -m repomori memory --config D:\Dev\YourRepo\repomori.toml --anchor-out D:\Temp\repomori-anchor.json --json
python -m repomori memory --config D:\Dev\YourRepo\repomori.toml --anchor-out D:\Temp\repomori-anchor.json --anchor-verify --json
python -m repomori brief D:\Dev\YourRepo\packs --out D:\Dev\YourRepo\agent-brief.md
python -m repomori chain D:\Dev\YourRepo\packs --json
python -m repomori anchor D:\Dev\YourRepo\packs --out D:\Dev\YourRepo\timeline-anchor.json
python -m repomori verify-anchor D:\Dev\YourRepo\timeline-anchor.json D:\Dev\YourRepo\packs --json
python -m repomori stats D:\Dev\YourRepo\packs --format json
python -m repomori build D:\Dev\YourRepo D:\Dev\YourRepo\packs\next.repomori --base D:\Dev\YourRepo\packs\latest.repomori --force --json
python -m repomori diff-context D:\Dev\YourRepo\packs\previous.repomori D:\Dev\YourRepo\packs\latest.repomori "what changed?" --out D:\Dev\YourRepo\diff-context.md
python -m repomori release-check D:\Dev\YourRepo --baseline D:\Dev\YourRepo\.repomori-scan-baseline.json --json
python -m repomori context D:\Dev\YourRepo\packs\latest.repomori "where is storage handled?" --out D:\Temp\context.md
```

`scan` is optional but recommended before publishing or building public packs.
It stays offline and reports likely secrets, generated artifacts, build noise,
large files, local path traces, and license guardrail gaps. Write a baseline
only for intentional findings you want future scans to acknowledge.
`release-check` combines schema sanity, strict scan, unit tests, and demo smoke.
Use `build --base` when you already have a recent pack and want to reuse
unchanged file state. `memory` and `snapshot` do that automatically against the
latest pack unless you pass `--no-incremental`. Use `diff-context` when an
agent needs only the source-backed changes between two packs.

## Recommended Local Workflow

Use `memory` at the end of a work session:

```powershell
python -m repomori memory --config D:\Dev\YourRepo\repomori.toml --diff-context --prune-apply --json
```

This builds a fresh incremental snapshot, creates a handoff package unless disabled, writes changed-files context when a previous snapshot exists, checks snapshot health, safely prunes old generated artifacts when requested, and returns the recent timeline. Use `brief` on the pack directory to create one agent-readable start file, `chain` to verify timeline integrity, `anchor` to export a small proof of the current chain head, `verify-anchor` to check that proof later, and `stats` to see how many files and chunks RepoMori avoided rebuilding.

For automation, add `--anchor-out` to export a timeline anchor every run and `--anchor-verify` to validate it right away. `--allow-unverified-anchor` keeps the run non-fatal if you need to continue while investigating a mismatch.

## CI and Nightly Automation

Use a scheduled job to keep a repo timeline anchored on a cadence:

```powershell
python -m repomori memory . --out-dir .repomori-packs --anchor-out .repomori-packs/timeline-anchor.json --anchor-verify --json
```

RepoMori is also in-tree documented for this workflow with a ready-to-copy
`.github/workflows/memory-anchor.yml` in this repository.

## What To Read Next

- [MCP setup](mcp-setup.md)
- [Schema notes](schemas.md)
- [Agent protocol](agent-protocol.md)
- [Public safety scan](public-safety-scan.md)
- [Release check](release-check.md)
- [Incremental packs](incremental-packs.md)
- [License FAQ](license-faq.md)
