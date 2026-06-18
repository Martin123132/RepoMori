# Security Model

RepoMori turns repository data into machine-readable local artifacts. Its core
security promise is operational restraint: no model calls, no provider
configuration, and no network dependency for pack, context, memory, release,
agent, or MCP workflows.

RepoMori is not an encryption product, sandbox, access-control system, malware
scanner, or data-loss-prevention system.

## Local-Only Boundary

Core commands are designed to run offline:

- `build`, `query`, `get`, `context`, `capsule`, `handoff`;
- `snapshot`, `memory`, `doctor`, `prune`, `timeline`, `chain`, `anchor`;
- `scan`, `release-check`, `release-health`, `compat`, `contract-check`;
- `agent` JSON-lines bridge and dependency-free MCP stdio bridge.

These commands do not require OpenAI, Claude, hosted model providers, API keys,
or network services. Some optional developer workflows, such as downloading a
GitHub release asset for validation, are outside that core runtime boundary.

## Artifact Sensitivity

Generated artifacts may contain or reveal source information:

- `.repomori` packs can contain compressed exact source bytes, file metadata,
  hashes, symbols, imports, headings, summaries, and chunk indexes;
- context bundles and handoff directories can contain exact source snippets;
- capsules and eval reports can contain summaries, terms, paths, hashes, and
  repository structure;
- snapshot timelines can contain pack paths, hashes, comparisons, and generated
  report names;
- release-check artifacts can contain scan findings and local path traces.

Treat these artifacts like source code when they are built from private source.
Do not upload them to public issues, public CI artifacts, chat tools, or
third-party services unless the source repository is safe to disclose.

## Tamper Evidence

RepoMori supports snapshot hash chains and timeline anchors. They are designed
to detect local timeline editing, reordering, incorrect truncation, and
corruption.

They do prove:

- the current snapshot index is internally consistent;
- retained entries link to expected previous hashes;
- an exported anchor matched a chain head at the time it was generated.

They do not prove:

- who created a snapshot;
- that a hostile local administrator could not replace both timeline and
  anchor;
- that source content is secret;
- that a pack is safe to share.

Signed releases, external timestamping, and stronger provenance can be layered
on later without changing the local-only default.

## Deletion And Pruning Safety

`prune` is dry-run by default and should only manage generated snapshot
artifacts inside the selected snapshot directory. Applied pruning must never
delete `latest.repomori` or `snapshots.json`, and external handoff paths should
be skipped rather than deleted.

Use hidden `.repomori-*` directories for generated CI artifacts to avoid mixing
source files and generated operational state.

## Public Release Scan

`scan`, `release-check`, and `release-health` help detect public-release
hazards such as:

- visible generated artifact directories;
- private path traces;
- missing license and notice files;
- baseline drift;
- unexpected test or demo failures.

These checks are guardrails, not proof that a repository contains no sensitive
content. Review release artifacts manually before publication.

## Reporting Security Issues

Follow [../SECURITY.md](../SECURITY.md). Do not disclose vulnerability details
in public issues before a fix or mitigation is available.
