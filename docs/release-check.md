# Release Check

`release-check` is the local pre-push and public-release gate. It stays
offline, dependency-free, and model-free.

```powershell
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --json
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --drift-log D:\Dev\RepoMori\.repomori-baseline-drift.jsonl --json
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --drift-policy D:\Dev\RepoMori\.repomori-drift-policy.json --json
```

It runs:

- schema catalog sanity checks
- strict public-safety scan, using the baseline when supplied
- `python -m unittest discover -s tests`
- quickstart `demo` smoke in a temporary sibling directory

The report uses schema `repomori.release_check.v1` and includes a pass/fail
status, settings, summary, and nested check reports.

`release-check` inherits the same baseline match behavior as `scan`: strict
`code + path + severity + line + match`, then semi-strict when line numbers drift,
then conservative message fallback only when unique. In JSON output this shows up
as `summary.baseline_match_counts` in the scan block.

`--fail-on` controls only scan severity thresholds; drift telemetry is informative
only. We keep strict/non-strict matching behavior and fail policy unchanged.
When present, `--drift-policy` adds optional policy evaluation to
`checks.scan.drift_policy` and `checks.scan.drift_policy.status` without
changing scan severity blocking.

`release-check` treats generated snapshot artifacts as findings (repo-level
`packs/` directories, `.repomori` pack files, and oversize files) on purpose.
Run from a clean tree or pass outputs through hidden `.repomori-*` locations
(for example `.repomori-packs`, `.repomori-release-check`, `.repomori-health`)
before running release checks.
GitHub Actions now runs a preflight workspace check for top-level RepoMori artifacts
before the heavier checks, so root-level `packs/` and top-level `.repomori` files
fail early with explicit guidance.

Release-check reports an explicit `checks.scan.drift_warnings` section:

- `strict_count`, `semi_strict_count`, `fallback_count`, `ignored_total`
- `non_strict_count`, `non_strict_ratio`
- `downgraded_from_line_match`, `downgraded_from_message_match`
- `warnings`, `status`, and `schema_version`

Use this section, or the persisted telemetry log via `--drift-log`, to monitor
baseline movement:

- `strict_count` keeps the high-confidence baseline lock
- `semi_strict_count` shows line-drift-tolerant ignores
- `fallback_count` shows message-only fallback ignores
- `non_strict_ratio` tracks total drift from strict line-based matching

Persist drift telemetry to trend it over time:

```powershell
python -m repomori release-check D:\Dev\RepoMori `
  --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json `
  --drift-log D:\Temp\repomori-drift.jsonl `
  --json

python -m repomori drift-summary D:\Temp\repomori-drift.jsonl --limit 20 --json
```

Use a non-blocking drift policy for operational guardrails:

```powershell
python -m repomori release-check D:\Dev\RepoMori `
  --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json `
  --drift-policy D:\Dev\RepoMori\.repomori-drift-policy.json `
  --drift-log D:\Temp\repomori-drift.jsonl `
  --json
```

```json
{
  "non_strict_ratio": { "warn-at": 0.2, "investigate-at": 0.35, "fail-at": 0.95 },
  "semi_strict_delta": { "warn-at": 2, "fail-at": 8 },
  "fallback_delta": { "warn-at": 1, "fail-at": 4 }
}
```

`drift-summary` reads the JSONL telemetry, reports semi_strict/fallback deltas
across the newest rows, and flags runs that carried drift warnings.

## Fast Variants

Skip the slower pieces when iterating on scan or schema work:

```powershell
python -m repomori release-check D:\Dev\RepoMori --skip-tests --skip-demo --json
python -m repomori release-check D:\Dev\RepoMori --skip-demo --json
```

Keep demo artifacts only when debugging a demo failure:

```powershell
python -m repomori release-check D:\Dev\RepoMori --demo-out D:\Temp\repomori-release-demo --keep-demo --json
```

GitHub Actions runs the full release check on Python 3.12 while the separate
test matrix still covers Python 3.10, 3.11, and 3.12.

For reproducible artifact locations, pass `--artifacts-dir` explicitly:

```powershell
python -m repomori release-check D:\Dev\RepoMori `
  --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json `
  --artifacts-dir D:\Dev\RepoMori\.repomori-release-check `
  --drift-log D:\Dev\RepoMori\.repomori-release-check\baseline-drift.jsonl `
  --json
```

## See also

- [Baseline drift watchlist](baseline-drift-watchlist.md)
