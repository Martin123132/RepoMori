# Release Check

`release-check` is the local pre-push and public-release gate. It stays
offline, dependency-free, and model-free.

```powershell
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --json
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --drift-log D:\Dev\RepoMori\.repomori-baseline-drift.jsonl --json
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
