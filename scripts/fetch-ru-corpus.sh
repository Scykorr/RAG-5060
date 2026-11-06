#!/usr/bin/env bash
# Download Russian public-domain literature and pack 100–500 TXT docs into inbox.
set -euo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
DEST="${1:-/mnt/d/RAG5060/inbox/ru_corpus}"
WORKDIR="${2:-/mnt/d/RAG5060/scratch/ru_corpus_src}"
TARGET_MIN="${TARGET_MIN:-120}"
TARGET_MAX="${TARGET_MAX:-280}"
CHUNK_CHARS="${CHUNK_CHARS:-7000}"

mkdir -p "$DEST" "$WORKDIR"
cd "$WORKDIR"

echo "=== download Russian-Ebooks (public domain / lib.ru texts) ==="
ZIP1=Russian-Ebooks.zip
if [[ ! -f "$ZIP1" ]]; then
  curl -fL --retry 4 --retry-delay 2 \
    -o "$ZIP1" \
    "https://github.com/ian-nai/Russian-Ebooks/archive/refs/heads/master.zip"
fi
rm -rf Russian-Ebooks-master
python3 - <<'PY'
import zipfile
from pathlib import Path
z=Path("Russian-Ebooks.zip")
with zipfile.ZipFile(z) as zf:
    zf.extractall(".")
print("extracted", z, "entries", len(zf.namelist()))
PY

echo "=== download russian_literature books (public domain) ==="
ZIP2=russian_literature.zip
if [[ ! -f "$ZIP2" ]]; then
  curl -fL --retry 4 --retry-delay 2 \
    -o "$ZIP2" \
    "https://github.com/victorvwier/russian_literature/archive/refs/heads/main.zip"
fi
rm -rf russian_literature-main
python3 - <<'PY'
import zipfile
from pathlib import Path
z=Path("russian_literature.zip")
with zipfile.ZipFile(z) as zf:
    zf.extractall(".")
print("extracted", z, "entries", len(zf.namelist()))
PY

python3 - <<PY
from pathlib import Path
import re, json, hashlib

dest = Path("$DEST")
dest.mkdir(parents=True, exist_ok=True)
# wipe previous pack (keep destination clean for this corpus)
for old in dest.glob("*.txt"):
    old.unlink()
for old in dest.glob("MANIFEST.json"):
    old.unlink()

sources = []
roots = [
    Path("Russian-Ebooks-master"),
    Path("russian_literature-main"),
]
for root in roots:
    if not root.exists():
        continue
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        # Russian-Ebooks uses .txt; russian_literature often has extensionless files.
        suf = p.suffix.lower()
        if suf and suf not in {".txt", ".text"}:
            continue
        if p.name.lower() in {"readme.md", "readme", "downloader.py", "license", "license.txt"}:
            continue
        parts = {x.lower() for x in p.parts}
        if "english" in parts:
            continue
        # Skip tiny stubs
        try:
            if p.stat().st_size < 400:
                continue
        except OSError:
            continue
        sources.append(p)

print("source_files", len(sources))
assert sources, "no Russian source texts found"

cyr = re.compile(r"[А-Яа-яЁё]")
chunk_chars = int("$CHUNK_CHARS")
target_min = int("$TARGET_MIN")
target_max = int("$TARGET_MAX")

def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1251", "koi8-r", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            text = None
    if text is None:
        text = raw.decode("utf-8", "replace")
    # light cleanup of lib.ru / ebook noise
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def is_russian(text: str) -> bool:
    sample = text[:8000]
    n = len(cyr.findall(sample))
    return n >= 80

def split_chunks(text: str, size: int) -> list[str]:
    # Prefer paragraph boundaries
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        paras = [text]
    chunks, buf = [], ""
    for p in paras:
        if not buf:
            buf = p
        elif len(buf) + 2 + len(p) <= size:
            buf = buf + "\n\n" + p
        else:
            chunks.append(buf)
            buf = p
            if len(chunks) >= target_max:
                break
    if buf and len(chunks) < target_max:
        chunks.append(buf)
    # hard-split oversized leftovers
    out = []
    for ch in chunks:
        if len(ch) <= size * 2:
            out.append(ch)
        else:
            for i in range(0, len(ch), size):
                out.append(ch[i : i + size])
                if len(out) >= target_max:
                    break
        if len(out) >= target_max:
            break
    return out

manifest = []
written = 0

def priority(p: Path) -> tuple:
    parts_l = [x.lower() for x in p.parts]
    path_s = str(p).lower()
    # Prefer classic prose/plays from Russian-Ebooks over tiny poems
    score = 0
    if "russian-ebooks" in path_s or "russian-ebooks-master" in path_s:
        score += 1000
    if any(x in path_s for x in ("chekhov", "dostoevsky", "tolstoy", "pushkin", "gogol", "turgenev", "gorky", "lermontov")):
        score += 500
    if "russian" in parts_l:
        score += 200
    # Deprioritize very short lyric stubs
    sz = p.stat().st_size
    if sz < 2000:
        score -= 300
    elif sz > 50000:
        score += 100
    return (-score, -sz, str(p))

must_keys = (
    "вишн",
    "три сестр",
    "дядя ван",
    "чайка",
    "медведь",
    "иванов",
    "предложение",
    "юбилей",
    "свадб",
)
must = []
rest = []
for s in sources:
    name_l = s.name.lower()
    if any(k in name_l for k in must_keys):
        must.append(s)
    else:
        rest.append(s)
sources = must + sorted(rest, key=priority)
print("must_sources", [str(s) for s in must])
print("top_sources", [str(s) for s in sources[:12]])

def emit(src: Path, max_pieces: int) -> None:
    global written
    if written >= target_max:
        return
    try:
        text = read_text(src)
    except Exception as e:
        print("skip_read", src, e)
        return
    if not is_russian(text):
        return
    remain = target_max - written
    pieces = split_chunks(text, chunk_chars)[: max(1, min(max_pieces, remain))]
    stem = re.sub(r"[^\w\-а-яА-ЯёЁ]+", "_", src.stem, flags=re.UNICODE).strip("_")[:48] or "doc"
    author = src.parent.name
    if author.lower() in {"russian", "books"} and src.parent.parent is not None:
        author = src.parent.parent.name
    for i, piece in enumerate(pieces, start=1):
        if written >= target_max:
            break
        if len(cyr.findall(piece)) < 40:
            continue
        body = (
            f"Произведение: {src.stem}\n"
            f"Автор: {author}\n"
            f"Источник: {src.as_posix()}\n"
            f"Фрагмент: {i}/{len(pieces)}\n\n"
            f"{piece.strip()}\n"
        )
        body = body[:12000]
        digest = hashlib.sha1(body.encode("utf-8")).hexdigest()[:10]
        name = f"{written+1:04d}_{stem}_{i:02d}_{digest}.txt"
        out = dest / name
        out.write_text(body, encoding="utf-8")
        manifest.append(
            {
                "file": name,
                "source": src.as_posix(),
                "chars": len(body),
                "bytes": out.stat().st_size,
            }
        )
        written += 1

for src in sources:
    if written >= target_max:
        break
    name_l = src.name.lower()
    is_must = any(k in name_l for k in must_keys)
    fsz = src.stat().st_size
    if is_must:
        max_pieces = 12
    elif fsz > 80000:
        max_pieces = 10
    elif fsz > 20000:
        max_pieces = 8
    elif fsz > 4000:
        max_pieces = 4
    else:
        max_pieces = 1
    emit(src, max_pieces)

print("written", written, "dest", dest)
assert written >= target_min, f"need >={target_min} Russian docs, got {written}"
(dest / "MANIFEST.json").write_text(
    json.dumps({"count": written, "items": manifest}, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print("OK", written)
PY

echo "=== inventory ==="
ls "$DEST" | wc -l
du -sh "$DEST"
echo "DEST=$DEST"
