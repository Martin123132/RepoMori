# Release Check

`release-check` is the local pre-push and public-release gate. It stays
offline, dependency-free, and model-free.

```powershell
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --json
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
- `warnings`, and `status`

Use this section to monitor baseline movement. A higher `non_strict_ratio` suggests
files have moved and may need the baseline refreshed.

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
