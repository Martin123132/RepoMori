# RepoMori

RepoMori turns a source repository into a compact, machine-readable `.repomori`
pack that AI agents and local tools can query without rereading the whole
codebase.

The first version is deliberately local and dependency-light:

- SQLite pack format.
- Compressed, deduplicated source chunks.
- File hashes and provenance metadata.
- Language, import, symbol, heading, and top-term indexes.
- Exact source recovery when the machine summary is not enough.

## Quick Start

```powershell
python -m repomori build C:\path\to\repo C:\path\to\repo.repomori --force
python -m repomori snapshot D:\Dev\RepoMori --out-dir D:\Dev\RepoMori\packs --handoff "continue this repo" --json
python -m repomori timeline D:\Dev\RepoMori\packs --format json
python -m repomori doctor D:\Dev\RepoMori\packs --json
python -m repomori prune D:\Dev\RepoMori\packs --keep 20 --json
python -m repomori prune D:\Dev\RepoMori\packs --keep 20 --apply --json
python -m repomori info C:\path\to\repo.repomori
python -m repomori query C:\path\to\repo.repomori storage
python -m repomori diagnose C:\path\to\repo.repomori "where is storage handled?" --json
python -m repomori brief C:\path\to\repo.repomori --out repo-brief.md
python -m repomori compare C:\path\to\old.repomori C:\path\to\new.repomori --out compare.md
python -m repomori context C:\path\to\repo.repomori "where is storage handled?" --out context.md
python -m repomori verify C:\path\to\repo.repomori
python -m repomori eval C:\path\to\repo.repomori --out eval.md
python -m repomori capsule C:\path\to\repo.repomori --out repo.capsule.json
python -m repomori handoff C:\path\to\repo.repomori "where is storage handled?" --out D:\handoffs\repo
python -m repomori handoff C:\path\to\new.repomori "continue this work" --base-pack C:\path\to\old.repomori --out D:\handoffs\next
python -m repomori check-handoff D:\handoffs\repo --json
python -m repomori bench D:\Dev\RepoMori --out D:\benchmarks\repomori
python -m repomori get C:\path\to\repo.repomori path\inside\repo.py --out restored.py
```

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
repomori build <repo> <pack>
repomori snapshot <repo> --out-dir <dir> [--handoff question] [--no-compare] [--json]
repomori timeline <snapshot-dir> [--format markdown|json] [--limit n] [--out file]
repomori doctor <snapshot-dir> [--verify-packs] [--json]
repomori prune <snapshot-dir> [--keep n] [--apply] [--json]
repomori info <pack>
repomori tree <pack>
repomori query <pack> <text>
repomori diagnose <pack> <question> [--json] [--max-files n] [--max-bytes n]
repomori brief <pack> [--format markdown|json] [--out file]
repomori compare <base-pack> <target-pack> [--format markdown|json] [--out file]
repomori context <pack> <question> [--format markdown|json] [--max-files n] [--max-bytes n] [--no-source] [--out file]
repomori verify <pack>
repomori eval <pack> [--question text] [--format markdown|json] [--out file]
repomori capsule <pack> [--max-files n] [--top-terms n] [--out file]
repomori handoff <pack> <question> --out <dir> [--base-pack pack] [--copy-pack] [--force] [--json]
repomori check-handoff <dir> [--json]
repomori bench <repo> --out <dir> [--force] [--json]
repomori get <pack> <path> [--out file]
```

`context` creates an offline, source-backed bundle for AI agents. It ranks
matching files, restores exact text from compressed chunks, adds line-numbered
snippets, and includes a source manifest with file hashes for verification.
Use `--max-bytes`, `--snippets-per-file`, and `--no-source` to control how much
exact source text goes into the context bundle.

`diagnose` explains why a question ranked files the way it did. It reports
query tokens and phrases, per-file score breakdowns, matched and missed terms,
ranking comparisons, snippet anchors, and tuning suggestions for better agent
context.

`brief` creates a question-free repository orientation report from one pack:
languages, likely entrypoints, key files, top terms, symbols, imports, headings,
and a source manifest for the files an agent should inspect first.

`compare` diffs two packs and reports added, removed, changed, and unchanged
file counts, language deltas, changed hashes and sizes, and symbol/import/heading
summary deltas so agents can continue from what changed instead of rereading
everything.

`snapshot` builds timestamped packs into an output directory, updates
`latest.repomori`, and automatically compares the new pack against the previous
latest pack when one exists. It also writes snapshot JSON/Markdown reports and
compare reports for machine-readable project memory over time, plus a
`snapshots.json` index that records the timeline of pack hashes and change
summaries. Use `--handoff` to create a handoff package for the new snapshot,
using the previous snapshot as `--base-pack` when available.

`timeline` reads `snapshots.json` and reports recent snapshots, pack hashes,
verification status, handoff locations, and aggregate added/removed/changed
counts.

`doctor` checks snapshot-directory health: `snapshots.json` parseability,
indexed pack existence and SHA-256 hashes, recorded snapshot/compare artifacts,
`latest.repomori`, and in-directory handoff packages. Add `--verify-packs` when
you want a full pack verification pass for each indexed snapshot.

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

`capsule` exports the pack's machine summary as dense JSON: compact file
records, symbol/import/heading graph data, vocabulary, and a verification
manifest without embedding raw source text.

`handoff` writes a directory for another agent with `manifest.json`,
`brief.md`, `brief.json`, `context.md`, `context.json`, `capsule.json`,
`eval.md`, `eval.json`, `verify.json`, and a short `README.md`. It verifies the
pack first and stops before writing context artifacts if verification fails. Use
`--base-pack` to include `compare.md` and `compare.json` so the receiving agent
can see what changed since an earlier pack.

`check-handoff` validates a handoff manifest, artifact sizes and SHA-256 hashes,
JSON artifacts, and any copied `.repomori` pack.

`bench` runs the full local proof loop for a repository: build, verify, brief,
eval, handoff, check-handoff, then writes `bench.json` and `bench.md`.

You can run the same commands without installing the package:

```powershell
python -m repomori --help
```
