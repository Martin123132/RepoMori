# First Tester Path

RepoMori is source-available software for personal and non-commercial testing
under the PolyForm Noncommercial License 1.0.0. Commercial use requires a
separate written license from TWO HANDS NETWORK LTD.
To discuss commercial licensing, contact the COO of TWO HANDS NETWORK LTD.

Use `D:\...` paths for local test outputs. Generated packs, reports, rehearsal
artifacts, and logs should go under hidden `.repomori-*` folders or another
D-drive scratch location.

Use [First Tester Feedback Checklist](tester-feedback.md) to report back the
expected statuses, generated files, confusing output, and licensing/storage
assumptions without sharing secrets or private source.

## Option A: Install The Release

```powershell
python -m venv D:\Dev\repomori-test-venv
D:\Dev\repomori-test-venv\Scripts\python -m pip install `
  https://github.com/Martin123132/RepoMori/releases/download/v0.2.0/repomori-0.2.0-py3-none-any.whl
D:\Dev\repomori-test-venv\Scripts\python -m repomori --help
```

## Option B: Use A Checkout

```powershell
cd D:\Dev\RepoMori
python -m pip install .
python -m repomori --help
```

Use the checkout route when validating unreleased maintenance-candidate work.
The published release install remains the cleanest outside-in test once the
candidate is packaged.

## Smoke Test

```powershell
python -m repomori demo --out D:\Dev\.repomori-first-test --force --json
python -m repomori query D:\Dev\.repomori-first-test\demo.repomori "sqlite connect Store" --json
python -m repomori context D:\Dev\.repomori-first-test\demo.repomori "sqlite connect Store" --out D:\Dev\.repomori-first-test\context.md
python -m repomori handoff D:\Dev\.repomori-first-test\demo.repomori "continue this demo" --out D:\Dev\.repomori-first-test\handoff --force --json
```

## Try Your Own Repository

```powershell
python -m repomori build D:\Dev\YourRepo D:\Dev\YourRepo\.repomori-packs\first.repomori --force --json
python -m repomori query D:\Dev\YourRepo\.repomori-packs\first.repomori "where is storage handled?" --json
python -m repomori context D:\Dev\YourRepo\.repomori-packs\first.repomori "where is storage handled?" --out D:\Dev\YourRepo\.repomori-packs\context.md
python -m repomori handoff D:\Dev\YourRepo\.repomori-packs\first.repomori "continue this repo" --out D:\Dev\YourRepo\.repomori-handoff --force --json
```

## Reviewer Rehearsal

```powershell
python -m repomori release-rehearsal --out D:\Dev\RepoMori\.repomori-release-rehearsal --force --json
```

The rehearsal uses sanitized fixture data and does not tag, publish, upload
release assets, call an AI model, or contact a network service. Review
`D:\Dev\RepoMori\.repomori-release-rehearsal\release-rehearsal.md` for the
privacy guard, storage-path policy, and release evidence summary.
