# Public Safety Scan

RepoMori includes a local scanner for public-release and pack-readiness checks.
It does not call a network service, an AI model, or a secret-scanning API.

```powershell
python -m repomori scan D:\Dev\RepoMori --public-release --json
python -m repomori scan D:\Dev\RepoMori --fail-on high
python -m repomori scan D:\Dev\RepoMori --fail-on medium --json
```

## What It Checks

- likely secrets: API keys, private keys, GitHub tokens, AWS access keys, and
  secret-like assignments
- risky secret filenames such as `.env`, `.npmrc`, `.pypirc`, and private SSH
  key names
- generated RepoMori artifacts such as `.repomori` packs, `packs`,
  `handoffs`, and benchmark folders
- dependency/build noise such as `node_modules`, `.venv`, `dist`, `build`, and
  caches
- huge files and binary-heavy folders
- local path traces such as `C:\Users\...`, OneDrive paths, and `D:\Temp\...`
- missing license files, `Private` license metadata, and public-release
  guardrail files when `--public-release` is supplied

## Exit Codes

By default, `scan` exits nonzero only when it finds `high` severity findings:

```powershell
python -m repomori scan D:\Dev\RepoMori --fail-on high
```

Use a stricter threshold in CI or before making a repository public:

```powershell
python -m repomori scan D:\Dev\RepoMori --public-release --fail-on medium --json
```

The JSON report uses schema `repomori.scan.v1` and includes the repository path,
settings, summary counts, public-release checklist details, and every finding
with severity, code, path, optional line number, and redacted match text.
