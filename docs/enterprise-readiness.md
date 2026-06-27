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
- Release package checksums, provenance, minimal SPDX SBOM artifacts, and local
  `verify-release` validation.
- Policy-aware `verify-release --policy` checks for required evidence, schemas,
  signatures, and warning/error thresholds.
- Release evidence bundles for reviewer/procurement handoff.
- Snapshot-memory backup and restore guidance with a read-only `restore-check`
  verification command.
- Optional GPG detached signatures for release integrity artifacts.
- Draft-first GitHub Release publishing workflow with verifier-gated assets.
- JSON schema catalog for supported reports and bridge contracts.
- Agent JSON-lines bridge and dependency-free MCP stdio bridge.
- Source-available license posture with commercial use reserved.
- Deterministic license policy checks for personal/non-commercial use,
  written commercial licensing, and COO commercial contact wording.
- CI coverage across Python 3.10, 3.11, and 3.12.

## Governance

RepoMori is free for personal and non-commercial use. Commercial use requires a
separate written commercial license, and commercial licensing discussions should
go through the COO of TWO HANDS NETWORK LTD.

- License: [LICENSE.md](../LICENSE.md)
- Commercial use: [commercial-use.md](commercial-use.md)
- Contributions: [CONTRIBUTING.md](../CONTRIBUTING.md)
- Support: [../SUPPORT.md](../SUPPORT.md)
- Security reporting: [../SECURITY.md](../SECURITY.md)
- Release validation: [releases/0.2.0-validation.md](releases/0.2.0-validation.md)
- Next maintenance notes: [releases/0.2.1.md](releases/0.2.1.md)
- Release integrity: [release-integrity.md](release-integrity.md)
- Release evidence: [release-evidence.md](release-evidence.md)
- Release policy: [release-policy.md](release-policy.md)
- Release policy selection: [release-policy-selection.md](release-policy-selection.md)
- Release policy matrix: [release-policy-matrix.md](release-policy-matrix.md)
- Release signing: [release-signing.md](release-signing.md)
- Release publishing: [release-publishing.md](release-publishing.md)
- Snapshot backup and restore: [snapshot-backup-restore.md](snapshot-backup-restore.md)

## Operational Checklist

Before using RepoMori in a company setting:

- confirm the intended use is covered by a written commercial license;
- contact the COO of TWO HANDS NETWORK LTD to discuss commercial licensing;
- run from a pinned release tag or reviewed commit;
- run `verify-release` before using a downloaded release artifact bundle;
- run `verify-release --policy` when a release must meet written internal
  evidence requirements;
- verify release signatures and the public key fingerprint when signing is
  enabled;
- publish new releases through the draft-first `publish-release` workflow when
  possible;
- verify release checksums and provenance manually when independent review is
  required;
- keep generated `.repomori` packs and handoffs in private storage;
- use hidden `.repomori-*` output directories for automation artifacts;
- run `release-check` before publishing repository changes;
- run `memory`, `doctor`, `chain`, and `release-health` for recurring project
  state snapshots;
- run `restore-check` after restoring a snapshot directory and before using it
  as audit memory;
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

- Add signed-release adoption evidence after GitHub signing secrets are
  configured.
- Add external timestamping or transparency-log publication.
- Add stricter CI drift and artifact retention policies.
- Add private commercial contact and support routing outside public issues.
