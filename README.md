# RepoMori

[![tests](https://github.com/Martin123132/RepoMori/actions/workflows/tests.yml/badge.svg)](https://github.com/Martin123132/RepoMori/actions/workflows/tests.yml)
[![memory-anchor](https://github.com/Martin123132/RepoMori/actions/workflows/memory-anchor.yml/badge.svg)](https://github.com/Martin123132/RepoMori/actions/workflows/memory-anchor.yml)
[![release](https://img.shields.io/github/v/release/Martin123132/RepoMori?include_prereleases=false)](https://github.com/Martin123132/RepoMori/releases/tag/v0.2.0)
[![python](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-PolyForm%20Noncommercial-blue)](LICENSE.md)
[![source available](https://img.shields.io/badge/source--available-non--commercial-informational)](NOTICE.md)

RepoMori turns a source repository into a compact, machine-readable `.repomori`
pack that AI agents and local tools can query without rereading the whole
codebase.

The first version is deliberately local and dependency-light:

- SQLite pack format.
- Compressed, deduplicated source chunks.
- File hashes and provenance metadata.
- Language, import, symbol, heading, and top-term indexes.
- Exact source recovery when the machine summary is not enough.

Release record: [`v0.2.0`](https://github.com/Martin123132/RepoMori/releases/tag/v0.2.0).
See [docs/releases/0.2.0-validation.md](docs/releases/0.2.0-validation.md)
for the post-release install validation record.

## License

RepoMori is source-available software, not open-source software. It is free for
personal and non-commercial use under the PolyForm Noncommercial License 1.0.0.

Commercial use requires a separate written license. That includes bundling
RepoMori into paid products, hosted services, managed services, enterprise
developer tools, commercial AI coding or agent products, and commercial AI
training/evaluation pipelines.

See [LICENSE.md](LICENSE.md), [NOTICE.md](NOTICE.md),
[COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md), and
[CONTRIBUTING.md](CONTRIBUTING.md).

Enterprise and security review starting points:
[SECURITY.md](SECURITY.md), [SUPPORT.md](SUPPORT.md),
[docs/commercial-use.md](docs/commercial-use.md),
[docs/security-model.md](docs/security-model.md), and
[docs/enterprise-readiness.md](docs/enterprise-readiness.md).

RepoMori was created by Martin Ollett and is owned/licensed by
TWO HANDS NETWORK LTD.

## Install From Release

Install the latest validated release wheel directly from GitHub:

```powershell
python -m venv D:\Dev\repomori-venv
D:\Dev\repomori-venv\Scripts\python -m pip install `
  https://github.com/Martin123132/RepoMori/releases/download/v0.2.0/repomori-0.2.0-py3-none-any.whl
D:\Dev\repomori-venv\Scripts\python -m repomori --help
D:\Dev\repomori-venv\Scripts\python -m repomori demo --out D:\Dev\repomori-demo --force --json
```

This install path is for personal and non-commercial use under the project
license. Commercial use needs written permission from TWO HANDS NETWORK LTD.

## Try It In 60 Seconds

```powershell
python -m repomori demo --out D:\Temp\repomori-demo --force --json
```

That creates a tiny demo repository, builds and verifies a `.repomori` pack,
writes pack inspection and source-backed context, runs a memory cycle, and checks the MCP tool
bridge. See [docs/quickstart.md](docs/quickstart.md) for the guided path.

## Install From Checkout

```powershell
cd D:\Dev\RepoMori
python -m pip install .
repomori --help
repomori demo --out D:\Temp\repomori-demo --force --json
```

Generated outputs should stay under `D:\Temp` or hidden `.repomori-*` folders
inside the repo, so public release checks do not trip on visible artifact
directories. `pip install .` may leave `build/` and `repomori.egg-info/` in
the checkout; remove those generated folders before running `release-check`.

## Local Validation

Use the focused unit test command for code changes:

```powershell
python -m unittest discover -s tests
```

For release-health or documentation-only edits, start with the faster local gate,
then run the full gate before tagging or publishing:

```powershell
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --skip-demo --json
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --fail-on low --json
```

## Quick Start

```powershell
python -m repomori demo --out D:\Temp\repomori-demo --force --json
python -m repomori scan D:\Dev\RepoMori --public-release --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --json
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --drift-log D:\Temp\repomori-drift.jsonl --json
python -m repomori release-health D:\Dev\RepoMori --snapshot-dir D:\Dev\RepoMori\.repomori-packs --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --drift-log D:\Temp\repomori-drift.jsonl --json
python -m repomori drift-summary D:\Temp\repomori-drift.jsonl --limit 20 --json
python -m repomori handoff-health-summary D:\handoffs\handoff-health.jsonl --limit 20 --json
python -m repomori build C:\path\to\repo C:\path\to\repo.repomori --force
python -m repomori init D:\Dev\RepoMori --out-dir D:\Dev\RepoMori\.repomori-packs
python -m repomori memory --config D:\Dev\RepoMori\repomori.toml --json
python -m repomori memory D:\Dev\RepoMori --out-dir D:\Dev\RepoMori\.repomori-packs --prune-apply --json
python -m repomori build D:\Dev\RepoMori D:\Dev\RepoMori\.repomori-packs\next.repomori --base D:\Dev\RepoMori\.repomori-packs\latest.repomori --force --json
python -m repomori inspect D:\Dev\RepoMori\.repomori-packs\latest.repomori --verify --out D:\Dev\RepoMori\pack-inspect.md
python -m repomori agent --config D:\Dev\RepoMori\repomori.toml
python -m repomori mcp --config D:\Dev\RepoMori\repomori.toml
python -m repomori schema --json
python -m repomori compat D:\Dev\RepoMori\.repomori-packs\latest.repomori --handoff D:\handoffs\repo --json
python -m repomori contract-check --fixture D:\Dev\RepoMori\tests\fixtures\compat-contracts.json --json
python -m repomori brief D:\Dev\RepoMori\.repomori-packs --out D:\Dev\RepoMori\agent-brief.md
python -m repomori snapshot D:\Dev\RepoMori --out-dir D:\Dev\RepoMori\.repomori-packs --handoff "continue this repo" --json
python -m repomori chain D:\Dev\RepoMori\.repomori-packs --json
python -m repomori memory D:\Dev\RepoMori --out-dir D:\Dev\RepoMori\.repomori-packs --anchor-out D:\Dev\RepoMori\.repomori-packs\timeline-anchor.json --anchor-freshness safe --anchor-verify --json
python -m repomori anchor D:\Dev\RepoMori\.repomori-packs --out D:\Dev\RepoMori\.repomori-packs\timeline-anchor.json --json
python -m repomori verify-anchor D:\Dev\RepoMori\.repomori-packs\timeline-anchor.json D:\Dev\RepoMori\.repomori-packs --json
python -m repomori timeline D:\Dev\RepoMori\.repomori-packs --format json
python -m repomori doctor D:\Dev\RepoMori\.repomori-packs --json
python -m repomori prune D:\Dev\RepoMori\.repomori-packs --keep 20 --json
python -m repomori prune D:\Dev\RepoMori\.repomori-packs --keep 20 --apply --json
python -m repomori info C:\path\to\repo.repomori
python -m repomori query C:\path\to\repo.repomori storage
python -m repomori diagnose C:\path\to\repo.repomori "where is storage handled?" --json
python -m repomori brief C:\path\to\repo.repomori --out repo-brief.md
python -m repomori brief D:\Dev\RepoMori\.repomori-packs --format json
python -m repomori compare C:\path\to\old.repomori C:\path\to\new.repomori --out compare.md
python -m repomori context C:\path\to\repo.repomori "where is storage handled?" --out context.md
python -m repomori verify C:\path\to\repo.repomori
python -m repomori eval C:\path\to\repo.repomori --out eval.md
python -m repomori capsule C:\path\to\repo.repomori --out repo.capsule.json
python -m repomori handoff C:\path\to\repo.repomori "where is storage handled?" --out D:\handoffs\repo
python -m repomori handoff C:\path\to\new.repomori "continue this work" --base-pack C:\path\to\old.repomori --out D:\handoffs\next
python -m repomori check-handoff D:\handoffs\repo --json
python -m repomori score-handoff D:\handoffs\repo --json
python -m repomori handoff-triage D:\handoffs\repo --out D:\handoffs\repo\triage.md
python -m repomori bench D:\Dev\RepoMori --out D:\benchmarks\repomori
python -m repomori get C:\path\to\repo.repomori path\inside\repo.py --out restored.py
```

`anchor` and `verify-anchor` expect an existing snapshot directory; run
`memory` first (or use `memory --anchor-out ... --anchor-verify`) before calling them.

## Why

Raw repos are expensive for AI agents to reread. RepoMori keeps the exact source
recoverable, but also stores a smaller machine-facing state:

```text
repo -> .repomori -> query paths/symbols/imports/summaries -> retrieve exact chunks
```

This is not a security format. It is a cognition and context format: cut out
what the machine does not need first, keep hashes and source recovery for when
exactness matters.

## Commands

```text
repomori build <repo> <pack> [--base pack] [--force] [--json]
repomori demo --out <dir> [--force] [--json]
repomori scan <repo> [--public-release] [--baseline file] [--ignore-code code] [--write-baseline file] [--fail-on high] [--json]
repomori release-check [repo] [--baseline file] [--fail-on low] [--drift-policy file] [--artifacts-dir dir] [--skip-tests] [--skip-demo] [--drift-log file] [--json]
repomori release-health [repo] [--snapshot-dir dir] [--baseline file] [--fail-on low] [--drift-policy file] [--drift-summary-limit n] [--timeline-limit n] [--doctor-verify-packs] [--compat-handoff dir] [--compat-verify-pack] [--contract-fixture file] [--artifacts-dir dir] [--skip-tests] [--skip-demo] [--json]
repomori verify-release <release-package-dir> [--format markdown|json] [--out file] [--json]
repomori release-evidence <release-package-dir> [--repo repo] [--release-check file] [--release-health file] [--out-dir dir] [--format markdown|json] [--out file] [--json]
repomori drift-summary <log> [--limit n] [--json]
repomori handoff-health-summary <log> [--limit n] [--format markdown|json] [--out file] [--json]
repomori init <repo> --out-dir <dir> [--config file] [--profile name] [--force] [--no-incremental] [--json]
repomori memory [repo] [--out-dir dir] [--config file] [--profile name] [--no-handoff] [--handoff-quality-profile safe|ci|strict] [--anchor-out file] [--anchor-verify] [--allow-unverified-anchor] [--anchor-log file] [--no-incremental] [--diff-context] [--keep n] [--prune-apply] [--json]
repomori agent [--config file] [--profile name]
repomori mcp [--config file] [--profile name]
repomori schema [schema-version] [--json]
repomori commands [--format markdown|json] [--out file] [--json]
repomori compat [pack] [--snapshot-dir dir] [--handoff dir] [--verify-pack] [--format markdown|json] [--out file] [--json]
repomori contract-check [--fixture file] [--format markdown|json] [--out file] [--json]
repomori snapshot <repo> --out-dir <dir> [--handoff question] [--no-incremental] [--no-compare] [--json]
repomori chain <snapshot-dir> [--format markdown|json] [--out file] [--json]
repomori anchor <snapshot-dir> [--format json|markdown] [--out file] [--json]
repomori verify-anchor <anchor.json> [snapshot-dir] [--no-current] [--format markdown|json] [--out file] [--json]
repomori timeline <snapshot-dir> [--format markdown|json] [--limit n] [--out file]
repomori timeline-search <snapshot-dir> <text> [--limit n] [--per-snapshot-limit n] [--format markdown|json] [--out file] [--json]
repomori stats <snapshot-dir> [--format markdown|json] [--limit n] [--out file]
repomori doctor <snapshot-dir> [--verify-packs] [--json]
repomori prune <snapshot-dir> [--keep n] [--apply] [--json]
repomori info <pack>
repomori inspect <pack> [--format markdown|json] [--verify] [--out file]
repomori inspect-diff <base-pack> <target-pack> [--format markdown|json] [--verify] [--out file]
repomori tree <pack>
repomori query <pack> <text>
repomori diagnose <pack> <question> [--json] [--max-files n] [--max-bytes n]
repomori brief <pack-or-snapshot-dir> [--format markdown|json] [--out file]
repomori compare <base-pack> <target-pack> [--format markdown|json] [--out file]
repomori context <pack> <question> [--format markdown|json] [--max-files n] [--max-bytes n] [--no-source] [--out file]
repomori diff-context <base-pack> <target-pack> [question] [--format markdown|json] [--max-files n] [--max-bytes n] [--no-source] [--out file]
repomori verify <pack>
repomori eval <pack> [--question text] [--format markdown|json] [--out file]
repomori context-eval <pack> --cases cases.json [--format markdown|json] [--out file]
repomori capsule <pack> [--max-files n] [--top-terms n] [--out file]
repomori handoff <pack> <question> --out <dir> [--base-pack pack] [--copy-pack] [--force] [--json]
repomori check-handoff <dir> [--json]
repomori score-handoff <dir> [--format markdown|json] [--out file] [--json]
repomori handoff-triage <score-or-handoff> [--limit n] [--format markdown|json] [--out file] [--json]
repomori handoff-quality <score-or-handoff> [--profile safe|ci|strict] [--target-score n] [--format markdown|json] [--out file] [--json]
repomori improve-handoff <pack> <question> --out <dir> [--target-score n] [--quality-profile safe|ci|strict] [--max-attempts n] [--force] [--json]
repomori archive-handoff <dir> [--out handoff.zip] [--force] [--json]
repomori handoff-health <dir> [--profile safe|ci|strict] [--improve-pack pack] [--archive] [--artifacts-dir dir] [--health-log file] [--json]
repomori bench <repo> --out <dir> [--force] [--json]
repomori get <pack> <path> [--out file]
```

See [docs/cli-reference.md](docs/cli-reference.md) for the generated full
command reference.

`anchor` and `verify-anchor` expect an existing snapshot directory. If this is your first
run for a repository, start with `memory` (for example with `--anchor-out ... --anchor-verify`)
so the snapshot timeline exists before exporting or verifying an anchor.

`inspect` builds a richer pack report than `info`: pack identity, pack hash,
storage and compression details, language counts, key/largest files, vocabulary,
source manifest entries, and optional full verification via `--verify`.

`inspect-diff` compares two packs as machine-facing state: storage deltas,
language movement, vocabulary shifts, added/removed/changed file manifests, and
optional verification for both packs.

`context` creates an offline, source-backed bundle for AI agents. It ranks
matching files, restores exact text from compressed chunks, adds line-numbered
snippets, and includes a source manifest with file hashes for verification.
Use `--max-bytes`, `--snippets-per-file`, and `--no-source` to control how much
exact source text goes into the context bundle.

`demo` creates a complete local quickstart under an output directory. It writes
a tiny demo repo, builds `demo.repomori`, verifies it, creates context, runs a
memory cycle, checks the MCP bridge, and writes `inspect.md`, `inspect.json`,
`demo.json`, plus a local `README.md` with follow-up commands.

`scan` checks a repository before packing or publishing. It looks for likely
secrets, private-key files, generated `.repomori` packs, handoff and benchmark
artifacts, dependency/build noise, huge files, binary-heavy folders, local path
traces, and license/public-release guardrail gaps. It is local-only and
dependency-free. Use `--fail-on high` for secret-style failures only, or make it
stricter with `--fail-on medium` or `--fail-on low`.

Baseline matching is drift-aware: strict `code + path + severity + line + match`,
then semi-strict `code + path + severity + match` when the line moved, then a
conservative fallback `code + path + severity + message` when unique.
Use `--baseline` for exact known findings and `--ignore-code` only for broad
local policy choices.

`release-check` is the local pre-push/public-release gate. It runs schema
catalog sanity checks, strict `scan`, `python -m unittest discover -s tests`,
and a quickstart `demo` smoke, then returns one `repomori.release_check.v1`
report. Add `--drift-log` to persist baseline-match drift telemetry and use
`drift-summary <log> --json` to review trend deltas in CI or nightly scripts.
`release-check` is intentionally strict about generated snapshot artifacts (such as
local `packs/` or `bench*` directories and `.repomori` files). Keep generated
snapshot output in hidden paths (like `.repomori-packs`) and run from a clean
working tree or baseline intentional artifacts explicitly.
`--drift-policy` is optional and non-blocking by default: it can flag warn or
investigation conditions without changing the existing `--fail-on` behavior.
Use `--artifacts-dir` when you want report/telemetry in a predictable folder.
After intentional doc or example movement, refresh `.repomori-scan-baseline.json`
with `scan --public-release --write-baseline` and confirm release-check drift
returns to strict-only matches.

`release-health` runs the release-check bundle plus snapshot health, timeline tail,
chain verification, drift summary, compatibility checks, and contract diffs in one report
(`repomori.health.v1`). Use it as your regular local loop for repeatable health
checks:

```powershell
python -m repomori release-health D:\Dev\RepoMori --snapshot-dir D:\Dev\RepoMori\.repomori-packs --drift-log D:\Temp\repomori-drift.jsonl --json
```

`verify-release` checks a release package directory after the release-candidate
workflow produces integrity artifacts. It validates `release-candidate.json`,
`checksums.txt`, `release-provenance.json`, `sbom.spdx.json`, wheel/source
artifacts, byte sizes, and SHA-256 hashes:

```powershell
python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --json
```

The release-candidate workflow writes `release-verify.json` and
`release-verify.md` automatically for reviewers.
`release-evidence` combines verification, release-check status, signatures,
artifact hashes, and workflow metadata into `repomori.release_evidence.v1`:

```powershell
python -m repomori release-evidence D:\Dev\RepoMori\.repomori-release-candidate `
  --repo D:\Dev\RepoMori `
  --release-check D:\Dev\RepoMori\.repomori-release-check\release-check.json `
  --out-dir D:\Dev\RepoMori\.repomori-release-candidate `
  --json
```

Point `release-evidence` at the exact package root that contains
`release-candidate.json`; parent artifact folders are accepted only when they
contain a single nested release package.

When GPG signing secrets are configured, release workflows also emit detached
`.asc` signatures for integrity artifacts. See
[docs/release-signing.md](docs/release-signing.md) for public-key distribution
and rotation.

`build --base` creates an incremental pack. It hashes current files, reuses
unchanged file records, compressed chunks, symbols, imports, and search index
rows from the base pack, and rebuilds only added or changed files. Removed files
from the base are simply left out of the new pack.
`snapshot` and `memory` use this same reuse path automatically when a previous
latest pack exists.

`diagnose` explains why a question ranked files the way it did. It reports
query tokens, expanded alias/variant terms, phrases, per-file score breakdowns,
matched and missed terms, ranking comparisons, snippet anchors, and tuning
suggestions for better agent context. Query ranking understands code-shaped
terms such as `StoreClient` or `connect_database`, gives lower-weight credit to
common aliases like storage/store and connection/connect, and prefers snippets
anchored on matching symbols, headings, or imports.

`brief` creates a question-free repository orientation report from one pack:
languages, likely entrypoints, key files, top terms, symbols, imports, headings,
and a source manifest for the files an agent should inspect first. When given a
snapshot directory, it writes an agent start brief from latest memory: doctor
status, timeline and reuse summaries, latest handoff, latest inspect-diff,
latest diff-context, key artifacts, repo orientation, and recommended next
commands.

`compare` diffs two packs and reports added, removed, changed, and unchanged
file counts, language deltas, changed hashes and sizes, and symbol/import/heading
summary deltas so agents can continue from what changed instead of rereading
everything.

`diff-context` turns a pack comparison into source-backed agent context. It
selects added, changed, and removed files, retrieves exact snippets from the
target pack for added/changed files and the base pack for removed files, anchors
changed-file snippets around text diffs when possible, and writes a verification
manifest.

`memory` is the recommended repeatable workflow for the end of a work session.
It builds a snapshot, creates a default handoff package, runs snapshot doctor,
plans or applies prune, and returns the recent timeline in one offline report.
Use `--anchor-out` to write a timeline anchor proof beside this memory snapshot.
Use `--anchor-verify` in CI or other automation to verify the exported anchor
immediately against current timeline head; pair it with `--allow-unverified-anchor`
to keep sessions running while still recording the mismatch.
The repo includes `.github/workflows/memory-anchor.yml` as a ready-to-run anchor
automation example for scheduled sessions.
Snapshots reuse unchanged file state from the previous latest pack by default;
use `--no-incremental` when you want a clean rebuild. Prune remains a dry run
unless `--prune-apply` is supplied. Use `init` to write a local `repomori.toml`
with D-drive-safe defaults, then run `memory` with `--config` or from a
directory beneath that config. Add `--diff-context` to write JSON and Markdown
changed-files context beside the snapshot reports when a previous snapshot
exists. Use `--anchor-log` to append one JSONL row per memory run for audit
workflows.

`init` writes a dependency-free TOML config with named profiles. A profile stores
the repo path, snapshot output directory, handoff question, retention count,
prune mode, doctor verification mode, timeline limit, chunk size, incremental
reuse mode, diff-context mode, and compare settings. It also supports anchor
automation settings (`anchor_out`, `anchor_verify`, `allow_unverified_anchor`,
and `anchor_log`). Explicit `memory` flags override config values.

`agent` runs a dependency-free JSON-lines bridge on stdio so other agents can
query RepoMori without guessing shell commands. Send one JSON object per line:

```json
{"id":1,"method":"agent.help"}
{"id":2,"method":"query.run","params":{"text":"sqlite Store","limit":3}}
{"id":3,"method":"context.build","params":{"question":"where is storage handled?","max_files":3}}
{"id":4,"method":"anchor.build"}
{"id":5,"method":"file.get","params":{"path":"repomori/codec.py"}}
```

Responses are JSON lines with `schema_version`, `jsonrpc`, `id`, `ok`, and
either `result` or `error`. Supported methods are `memory.run`, `timeline.read`,
`stats.read`, `doctor.run`, `inspect.build`, `inspect_diff.build`, `query.run`, `context.build`,
`brief.build`, `chain.verify`, `anchor.build`, `anchor.verify`, `diff_context.build`, `handoff.build`,
`capsule.build`, `file.get`, `compat.check`, and `schema.list`. Methods use the configured latest snapshot pack when `pack` is
not supplied. `inspect_diff.build` and `diff_context.build` can also infer
previous-to-latest from the configured snapshot directory.

`schema` lists RepoMori's supported JSON contracts and agent methods. `compat`
checks that a pack, optional handoff directory, schema catalog, agent bridge, and
MCP bridge still line up with the current RepoMori contracts:

```powershell
python -m repomori compat D:\Dev\RepoMori\.repomori-packs\latest.repomori --handoff D:\handoffs\repo --verify-pack --json
python -m repomori compat --snapshot-dir D:\Dev\RepoMori\.repomori-packs --format markdown --out D:\Dev\RepoMori\compat.md
python -m repomori contract-check --fixture D:\Dev\RepoMori\tests\fixtures\compat-contracts.json --format markdown --out D:\Dev\RepoMori\contract-diff.md
```

See `docs/schemas.md` and `docs/agent-protocol.md` for the compact protocol
notes.

`mcp` runs a dependency-free MCP stdio bridge over the same local agent methods.
It supports `initialize`, `notifications/initialized`, `ping`, `tools/list`,
and `tools/call`, returning tool output as readable text plus structured JSON.
Example local client config:

```json
{
  "mcpServers": {
    "repomori": {
      "command": "python",
      "args": [
        "-m",
        "repomori",
        "mcp",
        "--config",
        "D:\\Dev\\RepoMori\\repomori.toml"
      ]
    }
  }
}
```

The MCP tool names are `repomori_help`, `repomori_memory_run`,
`repomori_brief_build`, `repomori_chain_verify`, `repomori_anchor_build`, `repomori_anchor_verify`, `repomori_timeline_read`,
`repomori_stats_read`, `repomori_doctor_run`, `repomori_pack_inspect`, `repomori_pack_inspect_diff`, `repomori_query_run`,
`repomori_context_build`, `repomori_diff_context_build`, `repomori_handoff_build`,
`repomori_capsule_build`, `repomori_file_get`, `repomori_compat_check`, and
`repomori_schema_list`.

`snapshot` builds timestamped packs into an output directory, updates
`latest.repomori`, and automatically compares the new pack against the previous
latest pack when one exists. It also reuses unchanged file records and chunks
from that previous pack by default, then writes snapshot JSON/Markdown reports
and compare/inspect-diff reports for machine-readable project memory over time, plus a
`snapshots.json` index that records the timeline of pack hashes, incremental
reuse counts, and change summaries. Use `--no-incremental` to rebuild every file
and `--handoff` to create a handoff package for the new snapshot, using the
previous snapshot as `--base-pack` when available.

`timeline` reads `snapshots.json` and reports recent snapshots, pack hashes,
verification status, handoff locations, and aggregate added/removed/changed
counts, plus incremental reuse totals and snapshot-chain status.

`chain` verifies the tamper-evident snapshot timeline hash chain. It checks each
indexed snapshot entry hash, previous-chain pointer, and index head hash. Legacy
unchained timelines warn instead of failing; new snapshot and memory runs write
chain metadata automatically.

`anchor` exports a small JSON or Markdown proof record for the current snapshot
timeline head. It includes the chain head hash, latest snapshot pack hash,
verification status, and an `anchor_hash` over the proof payload so you can copy
the record outside the snapshot directory.

`verify-anchor` checks an exported anchor proof. It recomputes the proof's
`anchor_hash`, then compares the recorded chain head and latest pack hash against
the current snapshot directory unless `--no-current` is supplied.

`stats` reads `snapshots.json` and reports incremental savings over time:
incremental versus full snapshot counts, reused and rebuilt file totals, reused
chunks, reuse percentage, storage totals, latest snapshot stats, and top reuse
snapshots.

`doctor` checks snapshot-directory health: `snapshots.json` parseability,
indexed pack existence and SHA-256 hashes, recorded snapshot/compare artifacts,
`latest.repomori`, snapshot-chain integrity, and in-directory handoff packages.
Add `--verify-packs` when you want a full pack verification pass for each
indexed snapshot.

`prune` plans safe cleanup of old generated snapshot artifacts. It is a dry run
unless `--apply` is supplied. It keeps `latest.repomori`, `snapshots.json`, the
latest indexed snapshot, and the newest `--keep` snapshots, then only removes
generated packs, reports, compare reports, and in-directory handoff folders
inside the snapshot directory. External handoff paths are reported as skipped.

`verify` checks that stored chunks decompress, chunk hashes match, and restored
files still match their recorded sizes and SHA-256 hashes.

`eval` runs representative questions through the context builder and reports
selected files, snippet counts, source bytes, coverage, weak signals, and
suggested ranking or extraction improvements.

`context-eval` runs fixture-backed context quality cases. A case can require
expected paths, snippet text, matched terms, maximum rank, minimum top score, and
minimum snippet count. It exits nonzero when a case fails, so teams can use it as
a small local quality gate for agent context.

`capsule` exports the pack's machine summary as dense JSON: compact file
records, symbol/import/heading graph data, vocabulary, and a verification
manifest without embedding raw source text.

`handoff` writes a directory for another agent with `manifest.json`,
`brief.md`, `brief.json`, `context.md`, `context.json`, `capsule.json`,
`eval.md`, `eval.json`, `verify.json`, and a short `README.md`. It verifies the
pack first and stops before writing context artifacts if verification fails. Use
`--base-pack` to include `compare.md`, `compare.json`, `inspect-diff.md`, and
`inspect-diff.json` so the receiving agent can see both file-level and
machine-state changes since an earlier pack.

`check-handoff` validates a handoff manifest, artifact sizes and SHA-256 hashes,
JSON artifacts, and any copied `.repomori` pack.

`score-handoff` scores a handoff's operational usefulness: validation state,
artifact coverage, source-backed context snippets, capsule/brief machine state,
eval strength, and base-pack delta artifacts when present.
Snapshot, memory, and benchmark-generated handoffs also write
`handoff-score.json` and `handoff-score.md` sidecars automatically and surface
the score status in their summaries.

`handoff-triage` reads either a handoff directory or `handoff-score.json` and
turns weak score checks into a short prioritized repair checklist.
When generated handoffs have non-pass triage, RepoMori also writes
`handoff-triage.json` and `handoff-triage.md` next to the score sidecars so the
next agent gets a direct fix list.

`handoff-quality` applies `safe`, `ci`, or `strict` quality profiles to a score.
`memory --handoff-quality-profile strict` can intentionally fail a weak generated
handoff, while the default memory behavior remains unchanged.

`improve-handoff` rebuilds a handoff with progressively richer local settings
until it reaches the target score or exhausts attempts, then writes before/after
score, triage, and quality reports. `archive-handoff` writes a portable zip for
a verified handoff directory.

`handoff-health` is the operational wrapper for CI or a receiving agent. It runs
check, score, triage, and quality together, can improve a non-pass handoff from a
source pack, can archive the active handoff, and can write `handoff-health.json`
and `handoff-health.md` for review. Add `--health-log` to append one compact
JSONL trend row per run, then use `handoff-health-summary` to review the last N
rows:

```powershell
python -m repomori handoff-health D:\handoffs\repo --profile ci --artifacts-dir D:\handoffs\repo-health --health-log D:\handoffs\handoff-health.jsonl --json
python -m repomori handoff-health D:\handoffs\repo --profile strict --improve-pack D:\Dev\RepoMori\.repomori-packs\latest.repomori --question "continue this repo" --archive --health-log D:\handoffs\handoff-health.jsonl --json
python -m repomori handoff-health-summary D:\handoffs\handoff-health.jsonl --limit 20 --json
```

`bench` runs the full local proof loop for a repository: build, verify, brief,
eval, handoff, check-handoff, then writes `bench.json` and `bench.md`.

## Docs

- [Quickstart](docs/quickstart.md)
- [CLI reference](docs/cli-reference.md)
- [MCP setup](docs/mcp-setup.md)
- [Agent protocol](docs/agent-protocol.md)
- [Schemas](docs/schemas.md)
- [Compatibility runbook](docs/compatibility.md)
- [Public safety scan](docs/public-safety-scan.md)
- [Release check](docs/release-check.md)
- [Release health](docs/release-health.md)
- [Release integrity](docs/release-integrity.md)
- [Release evidence](docs/release-evidence.md)
- [Release signing](docs/release-signing.md)
- [Release candidate process](docs/release-candidate.md)
- [Release publishing](docs/release-publishing.md)
- [0.2.0 release notes](docs/releases/0.2.0.md)
- [0.2.0 validation](docs/releases/0.2.0-validation.md)
- [0.2.0rc1 validation](docs/releases/0.2.0rc1-validation.md)
- [0.2.0 final promotion](docs/releases/0.2.0-final-promotion.md)
- [Baseline drift watchlist](docs/baseline-drift-watchlist.md)
- [Reusable anchor workflow](docs/memory-anchor-reusable.md)
- [Incremental packs](docs/incremental-packs.md)
- [License FAQ](docs/license-faq.md)
- [Commercial use](docs/commercial-use.md)
- [Security model](docs/security-model.md)
- [Enterprise readiness](docs/enterprise-readiness.md)
- [Security policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Public release checklist](PUBLIC_RELEASE_CHECKLIST.md)

You can run the same commands without installing the package:

```powershell
python -m repomori --help
```
