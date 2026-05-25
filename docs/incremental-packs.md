# Incremental Packs

`build --base` creates a new `.repomori` pack while reusing unchanged file state
from an existing pack.

```powershell
python -m repomori build D:\Dev\RepoMori D:\Dev\RepoMori\packs\next.repomori --base D:\Dev\RepoMori\packs\latest.repomori --force --json
```

The builder hashes each current source file. When a file path and SHA-256 match
the base pack, RepoMori copies the existing file record, compressed chunks,
symbols, imports, and search index rows into the new pack. Added or changed
files are rebuilt normally.

Removed files from the base pack are not copied into the new pack.

The output remains schema `repomori.pack.v1`. The build summary adds incremental
fields:

- `incremental`
- `base_pack_path`
- `base_file_count`
- `reused_file_count`
- `rebuilt_file_count`
- `reused_chunk_count`

The new pack is still self-contained: `verify`, `query`, `context`, `capsule`,
`handoff`, and `get` work without the base pack.

Snapshot and memory workflows use incremental builds automatically. If
`latest.repomori` or an indexed previous snapshot exists, RepoMori uses it as
the base for the next snapshot:

```powershell
python -m repomori snapshot D:\Dev\RepoMori --out-dir D:\Dev\RepoMori\packs --json
python -m repomori memory D:\Dev\RepoMori --out-dir D:\Dev\RepoMori\packs --json
```

Use `--no-incremental` on `snapshot`, `memory`, or `init` when you want future
runs to rebuild every file instead.

Use `stats` to inspect cumulative reuse:

```powershell
python -m repomori stats D:\Dev\RepoMori\packs --format json
```
