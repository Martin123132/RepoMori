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
- `repomori.inspect.v1`: pack inspector report with storage, file, vocabulary, manifest, and verification summary.
- `repomori.compare.v1`: pack comparison report for added, removed, changed, and unchanged files.
- `repomori.inspect_diff.v1`: structural inspector diff report for storage, language, vocabulary, and changed-file manifests.
- `repomori.verify.v1`: pack integrity verification report.
- `repomori.context.v1`: source-backed context bundle from `context.build`.
- `repomori.diff_context.v1`: source-backed changed-files context from `diff_context.build`.
- `repomori.brief.v1`: question-free orientation brief from one pack.
- `repomori.agent_brief.v1`: snapshot-directory start brief for another agent.
- `repomori.capsule.v1`: dense machine-readable capsule without raw source.
- `repomori.eval.v1`: source-backed pack evaluation report.
- `repomori.context_eval.v1`: fixture-backed context quality evaluation report.
- `repomori.handoff.v1`: handoff directory manifest.
- `repomori.handoff_score.v1`: deterministic handoff usefulness score report.
- `repomori.handoff_triage.v1`: prioritized checklist generated from a handoff score.
- `repomori.handoff_quality.v1`: safe/ci/strict handoff quality gate report.
- `repomori.handoff_improvement.v1`: before/after handoff improvement run report.
- `repomori.handoff_archive.v1`: portable handoff zip archive report.
- `repomori.handoff_health.v1`: operational handoff check/score/triage/quality wrapper with optional improvement and archive details.
- `repomori.handoff_health_record.v1`: one JSONL handoff-health trend row.
- `repomori.handoff_health_summary.v1`: summarized handoff-health trend report.
- `repomori.memory.v1`: full memory-cycle report.
- `repomori.doctor.v1`: snapshot directory health report.
- `repomori.prune.v1`: safe prune dry-run or applied result.
- `repomori.snapshot_chain.v1`: snapshot timeline hash-chain verification report.
- `repomori.snapshot_anchor.v1`: external proof record for the current snapshot chain head.
- `repomori.snapshot_anchor.verify.v1`: verification report for an exported snapshot anchor proof.
- `repomori.restore_check.v1`: read-only restore verification bundle for a snapshot directory.
- `repomori.timeline.v1`: snapshot timeline report.
- `repomori.timeline_search.v1`: query report across indexed snapshot packs.
- `repomori.stats.v1`: snapshot incremental reuse and storage statistics.
- `repomori.snapshot.v1`: single snapshot build report.
- `repomori.config.v1`: `repomori.toml` profile config.
- `repomori.schema.catalog.v1`: schema registry output.
- `repomori.demo.v1`: local quickstart demo report.
- `repomori.scan.v1`: local public-safety repository scan report.
- `repomori.scan.baseline.v1`: acknowledged public-safety scan findings.
- `repomori.release_check.v1`: local release readiness report.
- `repomori.release_candidate.v1`: release package workflow artifact manifest.
- `repomori.release_provenance.v1`: release artifact provenance with hashes and workflow metadata.
- `repomori.release_verify.v1`: local verification report for release package checksums, provenance, SBOM, and artifacts.
- `repomori.release_policy.v1`: optional release verification policy consumed by `verify-release --policy`.
- `repomori.release_evidence.v1`: reviewer-facing release evidence bundle with verification, release-check, artifact, and signature status.
- `repomori.health.v1`: release-health aggregate bundle.
- `repomori.compat.v1`: pack, handoff, schema, agent, and MCP compatibility report.
- `repomori.contract_check.v1`: contract fixture diff report for schema, agent, MCP, and artifact names.
- `repomori.cli_commands.v1`: generated CLI command inventory.
- `repomori.baseline_drift_report.v1`: per-run baseline drift telemetry.
- `repomori.baseline_drift_record.v1`: one JSONL baseline drift log row.
- `repomori.baseline_drift_summary.v1`: summarized drift-log trend report.

## Agent Schemas

- `repomori.agent.response.v1`: JSON-lines response envelope.
- `repomori.agent.help.v1`: bridge method listing.
- `repomori.agent.query.v1`: wrapper around query results.
- `repomori.agent.file.v1`: exact file payload with text and base64 bytes.
- `repomori.mcp.tools.v1`: documented MCP tool listing contract.

## Compatibility Check

`python -m repomori compat D:\Dev\RepoMori\.repomori-packs\latest.repomori --handoff D:\handoffs\repo --json`
emits `repomori.compat.v1`. The check is local-only and validates pack schema,
optional full pack verification, handoff artifact integrity and JSON schema
versions, schema catalog entries, agent bridge methods, and MCP tool names.

## MCP Bridge

`python -m repomori mcp --config D:\Dev\RepoMori\repomori.toml` exposes the
agent methods as MCP stdio tools. MCP responses use the MCP JSON-RPC envelope;
RepoMori payloads appear inside `structuredContent` when a tool is called.

## Compatibility Defaults

- Existing pack format remains `repomori.pack.v1`.
- Schema docs describe field presence and intended meaning, not a full JSON
  Schema validator.
- Exact source recovery still depends on pack verification and file hashes.
