# RepoMori Quickstart

RepoMori creates compact, source-backed repository memory for local agents and tools.

## Try It In 60 Seconds

```powershell
python -m repomori demo --out D:\Temp\repomori-demo --force --json
```

That command creates a tiny demo repository, builds `demo.repomori`, verifies the pack, writes inspection and context reports, runs a memory cycle, and checks the MCP tool bridge.

Inspect the output:

```powershell
python -m repomori query D:\Temp\repomori-demo\demo.repomori "sqlite connect Store" --json
python -m repomori inspect D:\Temp\repomori-demo\demo.repomori --verify --out D:\Temp\repomori-demo\inspect.md
python -m repomori context D:\Temp\repomori-demo\demo.repomori "sqlite connect Store" --out D:\Temp\repomori-demo\context.md
python -m repomori timeline D:\Temp\repomori-demo\.repomori-packs --format json
```

## Use Your Own Repository

```powershell
python -m repomori scan D:\Dev\YourRepo --public-release --json
python -m repomori scan D:\Dev\YourRepo --public-release --write-baseline D:\Dev\YourRepo\.repomori-scan-baseline.json --json
python -m repomori init D:\Dev\YourRepo --out-dir D:\Dev\YourRepo\.repomori-packs
python -m repomori memory --config D:\Dev\YourRepo\repomori.toml --json
python -m repomori memory --config D:\Dev\YourRepo\repomori.toml --anchor-out D:\Dev\YourRepo\.repomori-packs\timeline-anchor.json --anchor-freshness safe --anchor-verify --json
python -m repomori anchor D:\Dev\YourRepo\.repomori-packs --out D:\Dev\YourRepo\.repomori-packs\timeline-anchor.json --json
python -m repomori verify-anchor D:\Dev\YourRepo\.repomori-packs\timeline-anchor.json D:\Dev\YourRepo\.repomori-packs --json
python -m repomori brief D:\Dev\YourRepo\.repomori-packs --out D:\Dev\YourRepo\agent-brief.md
python -m repomori chain D:\Dev\YourRepo\.repomori-packs --json
python -m repomori stats D:\Dev\YourRepo\.repomori-packs --format json
python -m repomori build D:\Dev\YourRepo D:\Dev\YourRepo\.repomori-packs\next.repomori --base D:\Dev\YourRepo\.repomori-packs\latest.repomori --force --json
python -m repomori inspect D:\Dev\YourRepo\.repomori-packs\latest.repomori --verify --out D:\Dev\YourRepo\pack-inspect.md
python -m repomori inspect-diff D:\Dev\YourRepo\.repomori-packs\previous.repomori D:\Dev\YourRepo\.repomori-packs\latest.repomori --out D:\Dev\YourRepo\inspect-diff.md
python -m repomori diff-context D:\Dev\YourRepo\.repomori-packs\previous.repomori D:\Dev\YourRepo\.repomori-packs\latest.repomori "what changed?" --out D:\Dev\YourRepo\diff-context.md
python -m repomori score-handoff D:\handoffs\repo --json
python -m repomori handoff-triage D:\handoffs\repo --out D:\handoffs\repo\triage.md
python -m repomori release-check D:\Dev\YourRepo --baseline D:\Dev\YourRepo\.repomori-scan-baseline.json --json
python -m repomori release-check D:\Dev\YourRepo --baseline D:\Dev\YourRepo\.repomori-scan-baseline.json --drift-log D:\Dev\YourRepo\.repomori-baseline-drift.jsonl --json
python -m repomori release-health D:\Dev\YourRepo --snapshot-dir D:\Dev\YourRepo\.repomori-packs --baseline D:\Dev\YourRepo\.repomori-scan-baseline.json --json
python -m repomori drift-summary D:\Dev\YourRepo\.repomori-baseline-drift.jsonl --limit 20 --json
python -m repomori context D:\Dev\YourRepo\.repomori-packs\latest.repomori "where is storage handled?" --out D:\Temp\context.md
```

`scan` is optional but recommended before publishing or building public packs.
It stays offline and reports likely secrets, generated artifacts, build noise,
large files, local path traces, and license guardrail gaps. Write a baseline
only for intentional findings you want future scans to acknowledge.
`release-check` combines schema sanity, strict scan, unit tests, and demo smoke.
`release-health` wraps `release-check` with doctor + chain + timeline + drift
summary for local health snapshots after one or more memory runs.
Use `build --base` when you already have a recent pack and want to reuse
unchanged file state. `memory` and `snapshot` do that automatically against the
latest pack unless you pass `--no-incremental`. Use `inspect-diff` when an
agent needs structural storage/language/vocabulary movement, and `diff-context`
when it needs source-backed changed-file snippets.
Handoff packages created with `--base-pack`, `snapshot --handoff`, or `memory`
include both compare and inspect-diff artifacts so a receiving agent can start
from the latest file-level and machine-state deltas.
Generated snapshot, memory, and benchmark handoffs also include
`handoff-score.json` and `handoff-score.md` sidecars.

## Recommended Local Workflow

Use `memory` at the end of a work session:

```powershell
python -m repomori memory --config D:\Dev\YourRepo\repomori.toml --diff-context --prune-apply --json
```

This builds a fresh incremental snapshot, creates a handoff package unless disabled, writes changed-files context when a previous snapshot exists, checks snapshot health, safely prunes old generated artifacts when requested, and returns the recent timeline. Use `brief` on the pack directory to create one agent-readable start file with the latest inspect-diff and diff-context summaries, `chain` to verify timeline integrity, `anchor` to export a small proof of the current chain head, `verify-anchor` to check that proof later, and `stats` to see how many files and chunks RepoMori avoided rebuilding.

`anchor` and `verify-anchor` expect an existing timeline in `<out-dir>`; if you have not
run `memory` yet, start with `python -m repomori memory --config ...` (you can include
`--anchor-out` there too).

For automation, add `--anchor-out` to export a timeline anchor every run and choose
an anchor freshness profile:

- `safe` (default): continue and keep the mismatch as a warning (`--anchor-freshness safe`).
- `strict`: fail if the anchor indicates mismatch with the current timeline.
- `legacy`: compare only against the exported proof hash (`--anchor-freshness legacy`).

Use `--anchor-freshness strict` only if you want CI-style hard failure when anchor
verification mismatches. `safe` is the default for backward-compatible local
automation.

## CI and Nightly Automation

Use a scheduled job to keep a repo timeline anchored on a cadence:

```powershell
python -m repomori memory . --out-dir .repomori-packs --anchor-out .repomori-packs/timeline-anchor.json --anchor-freshness safe --json
```

RepoMori is also in-tree documented for this workflow with a ready-to-copy
`.github/workflows/memory-anchor.yml` in this repository.

The workflow supports three manual modes:
`safe`, `strict`, and `legacy`.

You can also call the reusable workflow from other repos:

```yaml
name: repomori-anchor

on:
  schedule:
    - cron: "0 2 * * *"

jobs:
  repomori-anchor:
    uses: Martin123132/RepoMori/.github/workflows/memory-anchor-reusable.yml@main
    with:
      repo: .
      out_dir: .repomori-packs
      anchor_mode: safe
```

Use `anchor_mode` as `strict`, `safe`, or `legacy`, and pass alternate
`repo` / `out_dir` values when this repository is not rooted at `.`.
See [Reusable workflow guide](memory-anchor-reusable.md) for a complete template.

## What To Read Next

- [MCP setup](mcp-setup.md)
- [Schema notes](schemas.md)
- [Agent protocol](agent-protocol.md)
- [Public safety scan](public-safety-scan.md)
- [Release check](release-check.md)
- [Baseline drift watchlist](baseline-drift-watchlist.md)
- [Incremental packs](incremental-packs.md)
- [License FAQ](license-faq.md)
