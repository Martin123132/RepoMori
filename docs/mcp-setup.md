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
Call `repomori_brief_build` when an agent needs one concise start point for the
latest timeline state.
Call `repomori_chain_verify` to check that the snapshot timeline hash chain has
not been edited, reordered, or corrupted.
Call `repomori_anchor_build` when you want a compact proof record for the
current chain head that can be copied outside the snapshot directory.
Call `repomori_anchor_verify` to check that exported proof later and compare it
with the configured current timeline.

## Tools

- `repomori_help`
- `repomori_memory_run`
- `repomori_brief_build`
- `repomori_chain_verify`
- `repomori_anchor_build`
- `repomori_anchor_verify`
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
