# First Tester Feedback Checklist

RepoMori is source-available software for personal and non-commercial testing
under the PolyForm Noncommercial License 1.0.0. Commercial use requires a
separate written license from TWO HANDS NETWORK LTD. To discuss commercial
licensing, contact the COO of TWO HANDS NETWORK LTD.

Use this after [First Tester Path](first-tester.md). Keep generated packs,
handoffs, logs, rehearsal output, and screenshots on `D:\...`, preferably in
hidden `.repomori-*` folders.

## What To Run

```powershell
python -m repomori demo --out D:\Dev\.repomori-first-test --force --json
python -m repomori query D:\Dev\.repomori-first-test\demo.repomori "sqlite connect Store" --json
python -m repomori context D:\Dev\.repomori-first-test\demo.repomori "sqlite connect Store" --out D:\Dev\.repomori-first-test\context.md
python -m repomori handoff D:\Dev\.repomori-first-test\demo.repomori "continue this demo" --out D:\Dev\.repomori-first-test\handoff --force --json
python -m repomori release-rehearsal --out D:\Dev\RepoMori\.repomori-release-rehearsal --force --json
```

## Report Back

- Install path tested: release wheel or local checkout.
- Python version and operating system.
- `demo` status and whether `demo.repomori` was created.
- Top `query` result for `sqlite connect Store`.
- Whether `context.md` includes source snippets with line numbers.
- Whether the handoff directory includes `manifest.json`, `context.md`,
  `context.json`, `capsule.json`, `eval.md`, `eval.json`, `verify.json`, and
  `README.md`.
- `release-rehearsal` status, reviewer artifact privacy status, and storage
  path policy status.
- Any confusing command output, missing file, slow command, or unclear wording.

## Do Not Send

- Secrets, credentials, API keys, private URLs, customer data, or private repo
  contents.
- Raw proprietary source dumps.
- Boot-drive generated-output examples.
- Commercial-use assumptions unless there is a written commercial license.

## Expected Green Path

- `demo` returns `status: "pass"`.
- `query` ranks `app.py` first.
- `context.md` is written under the D-drive test folder.
- `handoff` returns `status: "complete"`.
- `release-rehearsal` returns `status: "pass"` with reviewer privacy and
  storage-path checks passing.
