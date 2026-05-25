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
