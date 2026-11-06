#!/usr/bin/env bash
# Download a small open-document test pack into D:\RAG5060\inbox\open_corpus
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
DEST="${1:-/mnt/d/RAG5060/inbox/open_corpus}"
mkdir -p "$DEST"
cd "$DEST"

echo "=== Project Gutenberg public-domain TXT (CC0 / public domain) ==="
# Small set of well-known public-domain books (plain text)
declare -A BOOKS=(
  [1342]="pride-and-prejudice.txt"
  [11]="alice-in-wonderland.txt"
  [84]="frankenstein.txt"
  [1661]="sherlock-holmes.txt"
  [2701]="moby-dick.txt"
)
for id in "${!BOOKS[@]}"; do
  out="${BOOKS[$id]}"
  url="https://www.gutenberg.org/files/${id}/${id}-0.txt"
  echo "GET $url -> $out"
  curl -fL --retry 4 --retry-delay 2 -o "$out" "$url" || curl -fL --retry 4 -o "$out" "https://www.gutenberg.org/cache/epub/${id}/pg${id}.txt"
  # keep only first ~120KB to stay under upload limits / speed up ingest for smoke
  # (full books remain available if needed; truncated copy for MVP test)
  python3 - <<PY
from pathlib import Path
p=Path("$out")
data=p.read_bytes()
# strip UTF-8 BOM if present
if data.startswith(b"\xef\xbb\xbf"):
    data=data[3:]
# keep ~80k chars of body for faster local smoke
text=data.decode("utf-8","replace")
# drop Gutenberg header/footer lightly
start=text.find("*** START")
end=text.find("*** END")
if start!=-1:
    text=text[start:]
if end!=-1:
    text=text[:end]
text=text[:80000]
p.write_text(text, encoding="utf-8")
print(p.name, "bytes", p.stat().st_size)
PY
done

echo "=== HF legal PDFs sample (CC0) if reachable ==="
HF_API='https://huggingface.co/api/datasets/AingeruBeOr/RAG_legal_comparison_PDFs/tree/main/data'
if curl -fsSL -m 20 "$HF_API" -o /tmp/hf-rag-pdfs.json; then
  python3 - <<'PY'
import json,urllib.request,pathlib
files=json.load(open("/tmp/hf-rag-pdfs.json"))
pdfs=[f for f in files if f.get("path","").lower().endswith(".pdf")]
print("pdfs_available", len(pdfs))
out=pathlib.Path(".")
# download up to 5 smallest PDFs under 8MB
pdfs=sorted(pdfs, key=lambda x: x.get("size") or 10**12)[:8]
n=0
for f in pdfs:
    size=f.get("size") or 0
    if size>8_000_000: continue
    name=pathlib.Path(f["path"]).name
    url=f"https://huggingface.co/datasets/AingeruBeOr/RAG_legal_comparison_PDFs/resolve/main/{f['path']}"
    print("GET", name, size)
    try:
        urllib.request.urlretrieve(url, out/name)
        n+=1
    except Exception as e:
        print("fail", name, e)
    if n>=5: break
print("downloaded_pdfs", n)
PY
else
  echo "HF list unavailable — continuing with Gutenberg only"
fi

echo "=== inventory ==="
ls -lh "$DEST"
echo "DEST=$DEST"
