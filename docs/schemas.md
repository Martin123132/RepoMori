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
- `repomori.diff_context.v1`: source-backed changed-files context from `diff_context.build`.
- `repomori.brief.v1`: question-free orientation brief from one pack.
- `repomori.agent_brief.v1`: snapshot-directory start brief for another agent.
- `repomori.capsule.v1`: dense machine-readable capsule without raw source.
- `repomori.handoff.v1`: handoff directory manifest.
- `repomori.memory.v1`: full memory-cycle report.
- `repomori.doctor.v1`: snapshot directory health report.
- `repomori.prune.v1`: safe prune dry-run or applied result.
- `repomori.snapshot_chain.v1`: snapshot timeline hash-chain verification report.
- `repomori.snapshot_anchor.v1`: external proof record for the current snapshot chain head.
- `repomori.snapshot_anchor.verify.v1`: verification report for an exported snapshot anchor proof.
- `repomori.timeline.v1`: snapshot timeline report.
- `repomori.stats.v1`: snapshot incremental reuse and storage statistics.
- `repomori.snapshot.v1`: single snapshot build report.
- `repomori.config.v1`: `repomori.toml` profile config.
- `repomori.schema.catalog.v1`: schema registry output.
- `repomori.demo.v1`: local quickstart demo report.
- `repomori.scan.v1`: local public-safety repository scan report.
- `repomori.scan.baseline.v1`: acknowledged public-safety scan findings.
- `repomori.release_check.v1`: local release readiness report.

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
