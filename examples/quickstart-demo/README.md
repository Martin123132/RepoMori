# RepoMori Quickstart Demo

This folder contains the same tiny repository shape that `python -m repomori demo` generates.

Run the generated version:

```powershell
python -m repomori demo --out D:\Temp\repomori-demo --force --json
```

Or pack this checked-in example directly:

```powershell
python -m repomori build examples\quickstart-demo\demo-repo D:\Temp\quickstart-demo.repomori --force --json
python -m repomori context D:\Temp\quickstart-demo.repomori "sqlite connect Store" --out D:\Temp\quickstart-context.md
```
