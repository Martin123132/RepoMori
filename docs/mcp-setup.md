# RepoMori MCP Setup

RepoMori includes a dependency-free MCP stdio bridge. It exposes local repository memory tools without calling an AI model, network service, or provider API.

## Start The Server

```powershell
python -m repomori mcp --config D:\Dev\RepoMori\repomori.toml
```

MCP clients usually start this command for you. A typical client config looks like:

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

## Prepare A Repo Config

```powershell
python -m repomori init D:\Dev\RepoMori --out-dir D:\Dev\RepoMori\packs --force
python -m repomori memory --config D:\Dev\RepoMori\repomori.toml --json
```

The MCP tools use the latest snapshot pack from the configured timeline when a tool call does not provide `pack`.
`repomori_memory_run` also reuses that latest pack as the incremental base for
the next snapshot unless the tool call sets `incremental` to `false`.
Set `diff_context` to `true` on `repomori_memory_run` to write changed-files
context artifacts beside the new snapshot reports.

## Tools

- `repomori_help`
- `repomori_memory_run`
- `repomori_timeline_read`
- `repomori_stats_read`
- `repomori_doctor_run`
- `repomori_query_run`
- `repomori_context_build`
- `repomori_diff_context_build`
- `repomori_handoff_build`
- `repomori_capsule_build`
- `repomori_file_get`
- `repomori_schema_list`

## Smoke Test By Hand

```powershell
$request = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
$request | python -m repomori mcp --config D:\Dev\RepoMori\repomori.toml
```

You should receive one JSON-RPC response with `repomori.mcp.tools.v1` and the tool list.
