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
python -m repomori context C:\path\to\repo.repomori "where is storage handled?" --out context.md
python -m repomori verify C:\path\to\repo.repomori
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
repomori context <pack> <question> [--format markdown|json] [--max-files n] [--max-bytes n] [--no-source] [--out file]
repomori verify <pack>
repomori get <pack> <path> [--out file]
```

`context` creates an offline, source-backed bundle for AI agents. It ranks
matching files, restores exact text from compressed chunks, adds line-numbered
snippets, and includes a source manifest with file hashes for verification.
Use `--max-bytes`, `--snippets-per-file`, and `--no-source` to control how much
exact source text goes into the context bundle.

`verify` checks that stored chunks decompress, chunk hashes match, and restored
files still match their recorded sizes and SHA-256 hashes.

You can run the same commands without installing the package:

```powershell
python -m repomori --help
```
