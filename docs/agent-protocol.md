# RepoMori Agent Protocol

`python -m repomori agent` runs a dependency-free JSON-lines bridge on stdio.
It is local-only: no network calls, no model calls, and no API keys.

Start the bridge:

```powershell
python -m repomori agent --config D:\Dev\RepoMori\repomori.toml
```

Send one JSON object per line:

```json
{"id":1,"method":"agent.help"}
{"id":2,"method":"memory.run","params":{"keep":20,"anchor_out":"D:\\Temp\\repomori-anchor.json","anchor_freshness":"safe"}}
{"id":3,"method":"brief.build","params":{"timeline_limit":5}}
{"id":4,"method":"chain.verify"}
{"id":5,"method":"anchor.build"}
{"id":6,"method":"anchor.verify","params":{"anchor":"D:\\Dev\\RepoMori\\timeline-anchor.json"}}
{"id":7,"method":"query.run","params":{"text":"sqlite Store","limit":3}}
{"id":8,"method":"inspect.build","params":{"max_files":8,"verify":true}}
{"id":9,"method":"inspect_diff.build","params":{"max_files":8}}
{"id":10,"method":"context.build","params":{"question":"where is storage handled?","max_files":3}}
{"id":11,"method":"file.get","params":{"path":"repomori/codec.py"}}
```

Each response is one JSON line:

```json
{"schema_version":"repomori.agent.response.v1","jsonrpc":"2.0","id":1,"ok":true,"result":{}}
```

Errors use the same envelope with `ok:false` and an `error` object.

## Methods

- `agent.help`: returns protocol and method metadata.
- `ping`: returns a simple status payload.
- `memory.run`: runs the configured memory cycle, using incremental snapshot reuse by default.
  Pass `diff_context:true` to write changed-files context for the new snapshot.
  Pass `anchor_out` to write a timeline anchor and set `anchor_freshness` to
  `strict`, `safe`, or `legacy` for inline validation. `strict` fails on mismatch,
  while `safe` and `legacy` continue with warnings.
- `brief.build`: builds a concise agent start brief from the configured snapshot timeline.
- `chain.verify`: verifies the configured snapshot timeline hash chain.
- `anchor.build`: exports a small proof record for the current snapshot chain head.
- `anchor.verify`: verifies an exported anchor proof and optionally compares it with the current snapshot timeline.
- `timeline.read`: reads the configured snapshot timeline.
- `stats.read`: reads incremental reuse and storage statistics for the snapshot timeline.
- `doctor.run`: checks snapshot directory health.
- `inspect.build`: inspects a pack's metadata, storage, indexes, vocabulary, and optional verification status.
- `inspect_diff.build`: inspects structural differences between two packs, or previous-to-latest from a configured snapshot directory.
- `query.run`: runs pack query; uses latest configured pack if `pack` is omitted.
- `context.build`: builds a source-backed context bundle.
- `diff_context.build`: builds source-backed changed-files context between two packs.
- `handoff.build`: writes a handoff package directory.
- `capsule.build`: exports a capsule payload.
- `file.get`: retrieves exact file bytes as text when decodable plus base64.
- `compat.check`: checks pack, optional handoff, schema, agent, and MCP compatibility.
- `schema.list`: lists schemas, agent methods, and MCP tool names.

## Pack Resolution

Methods that operate on a pack accept `params.pack`. If omitted, RepoMori reads
the configured snapshot timeline and uses the latest indexed pack.
`inspect_diff.build` and `diff_context.build` accept `params.base_pack` and
`params.target_pack`; if they are omitted, they use the previous and latest
snapshots from the configured timeline.

## MCP Stdio Bridge

`python -m repomori mcp` exposes the same local methods as MCP tools without
adding the official MCP SDK as a dependency.

Start the MCP bridge:

```powershell
python -m repomori mcp --config D:\Dev\RepoMori\repomori.toml
```

Minimal client configuration:

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

Supported MCP requests are `initialize`, `notifications/initialized`, `ping`,
`tools/list`, and `tools/call`. Tool results include readable `content` and
machine-readable `structuredContent`.

## MCP Tools

- `repomori_help`: returns protocol and method metadata.
- `repomori_memory_run`: runs the configured memory cycle.
- `repomori_brief_build`: builds an agent start brief from the snapshot timeline.
- `repomori_chain_verify`: verifies the snapshot timeline hash chain.
- `repomori_anchor_build`: exports a proof record for the current snapshot chain head.
- `repomori_anchor_verify`: verifies an exported snapshot anchor proof.
- `repomori_timeline_read`: reads the configured snapshot timeline.
- `repomori_stats_read`: reads incremental reuse and storage statistics.
- `repomori_doctor_run`: checks snapshot directory health.
- `repomori_pack_inspect`: inspects a pack's contents, storage, indexes, and verification status.
- `repomori_pack_inspect_diff`: inspects structural storage, language, vocabulary, and file changes between two packs.
- `repomori_query_run`: searches a pack or latest configured snapshot pack.
- `repomori_context_build`: builds a source-backed context bundle.
- `repomori_diff_context_build`: builds source-backed changed-files context.
- `repomori_handoff_build`: writes a handoff package directory.
- `repomori_capsule_build`: exports a capsule payload.
- `repomori_file_get`: retrieves exact file bytes.
- `repomori_compat_check`: checks pack, optional handoff, schema, agent, and MCP compatibility.
- `repomori_schema_list`: lists schemas, agent methods, and MCP tool names.
