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
{"id":2,"method":"memory.run","params":{"keep":20}}
{"id":3,"method":"query.run","params":{"text":"sqlite Store","limit":3}}
{"id":4,"method":"context.build","params":{"question":"where is storage handled?","max_files":3}}
{"id":5,"method":"file.get","params":{"path":"repomori/codec.py"}}
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
- `timeline.read`: reads the configured snapshot timeline.
- `stats.read`: reads incremental reuse and storage statistics for the snapshot timeline.
- `doctor.run`: checks snapshot directory health.
- `query.run`: runs pack query; uses latest configured pack if `pack` is omitted.
- `context.build`: builds a source-backed context bundle.
- `handoff.build`: writes a handoff package directory.
- `capsule.build`: exports a capsule payload.
- `file.get`: retrieves exact file bytes as text when decodable plus base64.
- `schema.list`: lists schemas, agent methods, and MCP tool names.

## Pack Resolution

Methods that operate on a pack accept `params.pack`. If omitted, RepoMori reads
the configured snapshot timeline and uses the latest indexed pack.

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
- `repomori_timeline_read`: reads the configured snapshot timeline.
- `repomori_stats_read`: reads incremental reuse and storage statistics.
- `repomori_doctor_run`: checks snapshot directory health.
- `repomori_query_run`: searches a pack or latest configured snapshot pack.
- `repomori_context_build`: builds a source-backed context bundle.
- `repomori_handoff_build`: writes a handoff package directory.
- `repomori_capsule_build`: exports a capsule payload.
- `repomori_file_get`: retrieves exact file bytes.
- `repomori_schema_list`: lists schemas, agent methods, and MCP tool names.
