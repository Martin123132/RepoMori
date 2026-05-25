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
python -m repomori init D:\Dev\YourRepo --out-dir D:\Dev\YourRepo\packs
python -m repomori memory --config D:\Dev\YourRepo\repomori.toml --json
python -m repomori context D:\Dev\YourRepo\packs\latest.repomori "where is storage handled?" --out D:\Temp\context.md
```

## Recommended Local Workflow

Use `memory` at the end of a work session:

```powershell
python -m repomori memory --config D:\Dev\YourRepo\repomori.toml --prune-apply --json
```

This builds a fresh snapshot, creates a handoff package unless disabled, checks snapshot health, safely prunes old generated artifacts when requested, and returns the recent timeline.

## What To Read Next

- [MCP setup](mcp-setup.md)
- [Schema notes](schemas.md)
- [Agent protocol](agent-protocol.md)
- [License FAQ](license-faq.md)
