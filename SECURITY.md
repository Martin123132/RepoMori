# Security Policy

RepoMori is local-first, dependency-free tooling for building and reading
machine-readable repository packs. Security reports are welcome, but please do
not disclose vulnerabilities publicly before there has been time to review and
fix them.

## Supported Versions

| Version | Supported |
| --- | --- |
| `0.2.x` | Yes |
| Earlier versions | No |

Security fixes are currently expected to land on the latest `0.2.x` line.

## Reporting A Vulnerability

Do not open a public GitHub issue for vulnerabilities, exploit details, leaked
private source, generated packs containing private source, or anything that
could help someone reproduce a security issue before it is fixed.

Preferred reporting paths:

- Use GitHub private vulnerability reporting if it is enabled for this
  repository.
- If you already have a commercial or evaluation contact with TWO HANDS NETWORK
  LTD, use that private channel.
- Otherwise, open a minimal public issue that asks for a private security
  contact route, without including technical details.

Please include:

- affected RepoMori version or commit;
- operating system and Python version;
- exact command or workflow involved;
- whether private source, `.repomori` packs, handoff archives, or CI artifacts
  are affected;
- impact and reproduction notes, shared privately.

## Scope

In scope:

- unintended file disclosure through packs, handoffs, archives, reports, or CI
  artifacts;
- path traversal, unsafe extraction, or unsafe deletion behavior;
- integrity problems in snapshot chains, anchors, verification, release checks,
  or compatibility contracts;
- command behavior that unexpectedly uses network, provider APIs, credentials,
  or external services;
- secrets or private local paths written to public-facing artifacts.

Out of scope:

- reports that require modifying local files without user action;
- denial-of-service against very large intentionally supplied repositories;
- vulnerability claims based only on the fact that `.repomori` packs can contain
  source content;
- licensing or commercial-use disputes. See
  [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) instead.

## Security Design Summary

RepoMori does not encrypt packs and does not claim that generated artifacts are
safe to publish. A `.repomori` pack can contain compressed source bytes,
metadata, summaries, extracted symbols, file hashes, and source-backed context.
Treat packs, handoffs, capsules, release artifacts, and memory timelines as
sensitive when they are built from private repositories.

See [docs/security-model.md](docs/security-model.md) for the full security
model and operational guidance.
