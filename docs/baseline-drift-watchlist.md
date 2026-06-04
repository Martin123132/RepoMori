# Baseline Drift Watchlist

When drift counters rise over time, it is usually movement, not failure.
`release-check` keeps this non-blocking, but teams should still review trend rows
regularly.

## Recommended telemetry source

Use a JSONL drift log (default example path):

- `D:\Dev\RepoMori\.repomori-baseline-drift.jsonl`

Write rows with:

```powershell
python -m repomori release-check D:\Dev\RepoMori `
  --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json `
  --drift-log D:\Dev\RepoMori\.repomori-baseline-drift.jsonl `
  --json
```

## Weekly/CI summary view

```powershell
python -m repomori drift-summary D:\Dev\RepoMori\.repomori-baseline-drift.jsonl --limit 20 --json
```

The command returns `repomori.baseline_drift_summary.v1` fields:

- `warn_count`: number of summarized runs that had warn-level drift
- `trend.semi_strict_delta`: recent change in semi-strict ignores
- `trend.fallback_delta`: recent change in fallback ignores
- `trend.non_strict_delta`: recent change in total non-strict ignores
- `max_non_strict_ratio`: highest non-strict ratio in window
- `avg_non_strict_ratio`: mean non-strict ratio in window

## Watch list

Watch and investigate when any of these show persistent growth:

1. `trend.non_strict_delta > 0` for multiple consecutive windows.
2. `warn_count == window_size` (every run is warn-level).
3. `max_non_strict_ratio` moves above historical norms.
4. `avg_non_strict_ratio` exceeds a small baseline target (for example `> 0.20`).

If this happens repeatedly, check:

- are files moving around a lot between runs?
- should the baseline be regenerated from a clean source of truth?
- can some ignores be tightened to strict path+line+match entries?
- is your working directory path changing unexpectedly (`D:\Temp` vs `C:\Temp` etc.)?

## Rotation action

Rotate/rewrite baseline when watchlist conditions are steady:

```powershell
python -m repomori scan D:\Dev\RepoMori --public-release --write-baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --json
python -m repomori release-check D:\Dev\RepoMori --baseline D:\Dev\RepoMori\.repomori-scan-baseline.json --fail-on low --json
```

After rewrite, keep watchlist for one extra cycle and confirm deltas stabilize.
