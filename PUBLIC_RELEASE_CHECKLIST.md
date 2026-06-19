# Public Release Checklist

Use this before changing the GitHub repository visibility to public.

## Legal And License

- Confirm `LICENSE.md`, `NOTICE.md`, `COMMERCIAL-LICENSE.md`, and `CONTRIBUTING.md` match the intended permissions.
- Confirm the copyright holder name is correct: `TWO HANDS NETWORK LTD`.
- Confirm internal records or written assignment show that TWO HANDS NETWORK LTD owns or is licensed to enforce the RepoMori IP.
- Do not describe RepoMori as "open source"; use "source-available" and "free for personal and non-commercial use".
- Consider a lawyer review before public release if commercial enforcement matters.

## Repository Audit

- Run a secret scan for API keys, tokens, private keys, passwords, and personal data.
- Run `python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --fail-on low --json`.
- Run `python -m repomori release-health D:\Dev\RepoMori --snapshot-dir D:\Dev\RepoMori\.repomori-packs --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --json`.
- Confirm the release package workflow has uploaded a wheel, source archive, manifest, checksums, provenance, SBOM, release verification, and release-check artifacts.
- Run `python -m repomori verify-release D:\Dev\RepoMori\.repomori-release-candidate --json` after downloading or generating the release package.
- Check git history for secrets or private files, not just the current checkout.
- Remove generated `.repomori` packs, handoff folders, benchmark outputs, and snapshot directories unless intentionally published.
- Confirm examples use safe D-drive paths and no private customer/project names.
- Confirm no private notes, personal logs, or unreleased ideas are included accidentally.

## GitHub Settings

- Set repository description to mention "source-available" rather than "open source".
- Enable branch protection for `main`.
- Require pull requests for outside contributions.
- Add topics carefully; avoid tags that imply permissive open source if that is not intended.
- Consider disabling packages/releases until the license posture is settled.

## First Public README Check

- The first screen should show what RepoMori does.
- The license section should be easy to find.
- Commercial-use boundaries should be clear without sounding hostile to personal users.
- The contribution terms should be linked before asking for outside patches.
