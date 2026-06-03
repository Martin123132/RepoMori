# Public Safety Scan

RepoMori includes a local scanner for public-release and pack-readiness checks.
It does not call a network service, an AI model, or a secret-scanning API.

```powershell
python -m repomori scan D:\Dev\RepoMori --public-release --json
python -m repomori scan D:\Dev\RepoMori --fail-on high
python -m repomori scan D:\Dev\RepoMori --fail-on medium --json
python -m repomori scan D:\Dev\RepoMori --write-baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --json
python -m repomori scan D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --fail-on low --json
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

## Baselines And Ignores

Use a baseline for intentional findings that should stay visible but not fail a
strict scan:

```powershell
python -m repomori scan D:\Dev\RepoMori --public-release --write-baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --json
python -m repomori scan D:\Dev\RepoMori --public-release --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --fail-on low --json
```

The baseline file uses schema `repomori.scan.baseline.v1`. Matching is now
drift-tolerant:

- strict: `code + path + severity + line + match`
- semi-strict: `code + path + severity + match` (line may shift)
- fallback: `code + path + severity + message` only when that combination is
  uniquely safe

The report also includes `baseline_match_counts` in `summary` so you can see how
many ignores used strict, semi-strict, and fallback matching. Use `--ignore-code`
for broader policy decisions, for example when a repository intentionally keeps
large binary fixtures:

```powershell
python -m repomori scan D:\Dev\RepoMori --ignore-code binary_file --json
```

RepoMori's GitHub Actions workflow runs the stricter `release-check` command on
every push and pull request:

```powershell
python -m repomori release-check . --baseline .repomori-scan-baseline.json --fail-on low --json
```
