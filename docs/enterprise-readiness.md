# Enterprise Readiness

RepoMori `0.2.x` is ready for source-available evaluation as a local,
machine-readable repository state tool. It is not yet a fully managed enterprise
product.

## Current Strengths

- Local-only core commands with no model provider configuration.
- Dependency-free Python package for the main runtime path.
- Public-release safety scan and baseline drift telemetry.
- Release-health aggregate checks covering release-check, doctor, chain,
  timeline, compatibility, and contract stability.
- Tamper-evident snapshot timelines and anchor verification.
- Release package checksums, provenance, and minimal SPDX SBOM artifacts.
- JSON schema catalog for supported reports and bridge contracts.
- Agent JSON-lines bridge and dependency-free MCP stdio bridge.
- Source-available license posture with commercial use reserved.
- CI coverage across Python 3.10, 3.11, and 3.12.

## Governance

- License: [LICENSE.md](../LICENSE.md)
- Commercial use: [commercial-use.md](commercial-use.md)
- Contributions: [CONTRIBUTING.md](../CONTRIBUTING.md)
- Support: [../SUPPORT.md](../SUPPORT.md)
- Security reporting: [../SECURITY.md](../SECURITY.md)
- Release validation: [releases/0.2.0-validation.md](releases/0.2.0-validation.md)
- Release integrity: [release-integrity.md](release-integrity.md)

## Operational Checklist

Before using RepoMori in a company setting:

- confirm the intended use is covered by a written commercial license;
- run from a pinned release tag or reviewed commit;
- verify release checksums and provenance before using a downloaded artifact;
- keep generated `.repomori` packs and handoffs in private storage;
- use hidden `.repomori-*` output directories for automation artifacts;
- run `release-check` before publishing repository changes;
- run `memory`, `doctor`, `chain`, and `release-health` for recurring project
  state snapshots;
- keep snapshot anchors or logs outside the mutable snapshot directory when
  stronger audit trails matter;
- avoid uploading source-bearing artifacts to public issue trackers or chat
  systems.

## Known Limits

- RepoMori does not encrypt packs or handoffs.
- RepoMori does not provide identity, signing, or non-repudiation for snapshot
  authors.
- RepoMori does not replace legal review, procurement review, or source-code
  security review.
- Public-release scans are guardrails, not a full data-loss-prevention system.
- Commercial support, warranties, redistribution, and service-level commitments
  require separate written terms.

## Recommended Enterprise Next Steps

- Add signed checksums and signed provenance.
- Add external timestamping or transparency-log publication.
- Add policy files for stricter CI drift and artifact retention.
- Add documented backup and restore guidance for snapshot timelines.
- Add private commercial contact and support routing outside public issues.
