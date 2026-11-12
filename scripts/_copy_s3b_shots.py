from pathlib import Path
import shutil
src_dir = Path.home() / "AppData/Local/Temp/cursor/screenshots"
# WSL path fallback
cands = [
    Path("/mnt/c/Users/Tony Fedos/AppData/Local/Temp/cursor/screenshots"),
    Path("/mnt/c/Users/TONYFE~1/AppData/Local/Temp/cursor/screenshots"),
    src_dir,
]
src = next((p for p in cands if p.exists()), None)
dest = Path("/mnt/d/RAG 5060/artifacts/20261112-graph-s3b/screenshots")
dest.mkdir(parents=True, exist_ok=True)
print("src", src)
if not src:
    raise SystemExit(1)
files = sorted(src.glob("page-2026-11-12*.png"), key=lambda p: p.stat().st_mtime)
for i, f in enumerate(files[-6:], 1):
    out = dest / f"{i:02d}-{f.name}"
    shutil.copy2(f, out)
    print("copied", out.name, out.stat().st_size)
