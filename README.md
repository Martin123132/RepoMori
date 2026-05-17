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
python -m repomori info C:\path\to\repo.repomori
python -m repomori query C:\path\to\repo.repomori storage
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
repomori info <pack>
repomori tree <pack>
repomori query <pack> <text>
repomori get <pack> <path> [--out file]
```

You can run the same commands without installing the package:

```powershell
python -m repomori --help
```
