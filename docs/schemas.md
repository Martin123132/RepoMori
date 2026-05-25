# RepoMori Schemas

RepoMori JSON payloads include a `schema_version` field. Schema versions are
stable contract names for agents and local tools; new incompatible shapes should
use a new schema version.

List supported schemas:

```powershell
python -m repomori schema --json
python -m repomori schema repomori.memory.v1 --json
```

## Core Schemas

- `repomori.pack.v1`: pack metadata stored in `.repomori` SQLite metadata.
- `repomori.context.v1`: source-backed context bundle from `context.build`.
- `repomori.capsule.v1`: dense machine-readable capsule without raw source.
- `repomori.handoff.v1`: handoff directory manifest.
- `repomori.memory.v1`: full memory-cycle report.
- `repomori.doctor.v1`: snapshot directory health report.
- `repomori.prune.v1`: safe prune dry-run or applied result.
- `repomori.timeline.v1`: snapshot timeline report.
- `repomori.snapshot.v1`: single snapshot build report.
- `repomori.config.v1`: `repomori.toml` profile config.
- `repomori.schema.catalog.v1`: schema registry output.

## Agent Schemas

- `repomori.agent.response.v1`: JSON-lines response envelope.
- `repomori.agent.help.v1`: bridge method listing.
- `repomori.agent.query.v1`: wrapper around query results.
- `repomori.agent.file.v1`: exact file payload with text and base64 bytes.
- `repomori.mcp.tools.v1`: documented MCP tool listing contract.

## MCP Bridge

`python -m repomori mcp --config D:\Dev\RepoMori\repomori.toml` exposes the
agent methods as MCP stdio tools. MCP responses use the MCP JSON-RPC envelope;
RepoMori payloads appear inside `structuredContent` when a tool is called.

## Compatibility Defaults

- Existing pack format remains `repomori.pack.v1`.
- Schema docs describe field presence and intended meaning, not a full JSON
  Schema validator.
- Exact source recovery still depends on pack verification and file hashes.
