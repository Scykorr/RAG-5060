"""Local corpus category / filename-prefix registry (offline, no cloud).

On import the system decides automatically:
- reuse an existing pack prefix (same category), or
- create a new pack prefix for a new theme.

Stored in AppSetting key ``corpus_packs`` as JSON. Does not touch docs/SKILLS|ERRORS|ABILITIES.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from apps.api.models import AppSetting, Document, DocumentVersion

SETTING_KEY = "corpus_packs"
PREFIX_SETTING = "retrieval_filename_prefix"

# Offline keyword fingerprints for known themes (extend locally as needed).
THEME_HINTS: dict[str, list[str]] = {
    "classic": [
        "толстой",
        "достоевск",
        "пушкин",
        "чехов",
        "тургенев",
        "гоголь",
        "лермонтов",
        "анна каренина",
        "война и мир",
        "преступление и наказание",
        "евгений онегин",
        "классическ",
        "русская литература",
    ],
    "wildlife": [
        "заповедник",
        "медведь",
        "тайга",
        "флора",
        "фауна",
        "животн",
        "птиц",
        "охот",
        "природ",
        "эколог",
        "wildlife",
        "дикая природа",
    ],
}

# Subdir / pack-name aliases → pack id
ALIAS_TO_PACK: dict[str, str] = {
    "classic": "classic",
    "classic_ru": "classic",
    "classics": "classic",
    "klassika": "classic",
    "литература": "classic",
    "классика": "classic",
    "wildlife": "wildlife",
    "wildlife_ru": "wildlife",
    "nature": "wildlife",
    "priroda": "wildlife",
    "природа": "wildlife",
    "дикая_природа": "wildlife",
    "дикая-природа": "wildlife",
}

_CYR_MAP = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def _norm(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", t).strip()


def slugify(name: str) -> str:
    raw = (name or "").strip().lower().replace("ё", "е")
    raw = unicodedata.normalize("NFKC", raw)
    out = []
    for ch in raw:
        if ch in _CYR_MAP:
            out.append(_CYR_MAP[ch])
        elif "a" <= ch <= "z" or "0" <= ch <= "9":
            out.append(ch)
        elif ch in "-_ ":
            out.append("_")
    slug = re.sub(r"_+", "_", "".join(out)).strip("_")
    return slug[:40] or "pack"


def default_prefix(pack_id: str) -> str:
    pid = slugify(pack_id) or "pack"
    return f"{pid}_"


def _load_registry(db: Session) -> dict[str, Any]:
    row = db.get(AppSetting, SETTING_KEY)
    if not row or not row.value:
        return {"packs": {}}
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return {"packs": {}}
    if not isinstance(data, dict):
        return {"packs": {}}
    packs = data.get("packs") or {}
    if not isinstance(packs, dict):
        packs = {}
    return {"packs": packs}


def _save_registry(db: Session, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    row = db.get(AppSetting, SETTING_KEY)
    if row:
        row.value = payload
    else:
        db.add(AppSetting(key=SETTING_KEY, value=payload))


def list_packs(db: Session) -> dict[str, Any]:
    reg = _load_registry(db)
    return {"packs": reg.get("packs") or {}}


def pack_options(db: Session) -> list[dict[str, Any]]:
    """Existing categories for UI dropdown (with human description)."""
    reg = _load_registry(db)
    packs = reg.get("packs") or {}
    # Seed known themes so UI is useful even before first import.
    for pid, hints in THEME_HINTS.items():
        if pid not in packs:
            packs[pid] = {
                "prefix": default_prefix(pid),
                "label": {"classic": "Русская классическая литература", "wildlife": "Дикая природа России"}.get(
                    pid, pid
                ),
                "aliases": [k for k, v in ALIAS_TO_PACK.items() if v == pid][:6],
                "keywords": list(hints),
                "seed": True,
            }
    out: list[dict[str, Any]] = []
    for pid, meta in sorted(packs.items()):
        meta = meta or {}
        hints = list(meta.get("keywords") or THEME_HINTS.get(pid, []))[:6]
        aliases = list(meta.get("aliases") or [])
        label = meta.get("label") or pid
        desc_parts = [str(label)]
        if aliases:
            desc_parts.append("папки: " + ", ".join(aliases[:4]))
        if hints:
            desc_parts.append("метки: " + ", ".join(str(h) for h in hints[:4]))
        out.append(
            {
                "pack_id": pid,
                "prefix": meta.get("prefix") or default_prefix(pid),
                "label": label,
                "description": " · ".join(desc_parts),
                "seed": bool(meta.get("seed")),
            }
        )
    return out


def _score_text_against_hints(text: str, pack_id: str, pack_meta: dict[str, Any] | None = None) -> int:
    n = _norm(text)
    if not n:
        return 0
    keys = list(THEME_HINTS.get(pack_id, []))
    if pack_meta:
        keys.extend(pack_meta.get("keywords") or [])
        keys.append(pack_meta.get("label") or "")
        keys.append(pack_id)
    score = 0
    for k in keys:
        kk = _norm(str(k))
        if len(kk) >= 4 and kk in n:
            score += 2 if len(kk) >= 8 else 1
    return score


def _sample_folder_text(files: list[Path], *, max_files: int = 12, max_bytes: int = 8000) -> str:
    chunks: list[str] = []
    for p in files[:max_files]:
        chunks.append(p.stem)
        if p.suffix.lower() == ".txt":
            try:
                raw = p.read_bytes()[:max_bytes]
                chunks.append(raw.decode("utf-8", errors="ignore"))
            except OSError:
                continue
    return "\n".join(chunks)


def _filename_prefix_votes(files: list[Path], packs: dict[str, Any]) -> tuple[str | None, str | None]:
    """If many files already start with a known pack prefix → reuse it."""
    if not files or not packs:
        return None, None
    counts: dict[str, int] = {}
    for pid, meta in packs.items():
        prefix = (meta or {}).get("prefix") or default_prefix(pid)
        n = sum(1 for f in files if f.name.startswith(prefix))
        if n:
            counts[pid] = n
    if not counts:
        # also detect bare classic_/wildlife_ style from filenames
        for f in files:
            m = re.match(r"^([a-z][a-z0-9]{1,24})_", f.name.lower())
            if m:
                pid = m.group(1)
                counts[pid] = counts.get(pid, 0) + 1
    if not counts:
        return None, None
    best = max(counts, key=counts.get)
    if counts[best] >= max(1, len(files) // 3):
        return best, "filename_prefix"
    return None, None


def suggest_pack(
    db: Session,
    *,
    subdir: str | None,
    files: list[Path],
) -> dict[str, Any]:
    """Read-only auto-suggestion (no registry write). User confirms in UI."""
    reg = _load_registry(db)
    packs: dict[str, Any] = dict(reg.get("packs") or {})
    options = pack_options(db)

    sub = (subdir or "").replace("\\", "/").strip("/")
    top = sub.split("/")[0] if sub else ""
    top_l = top.lower()
    slug = slugify(top) if top else "inbox"
    proposed_new = {
        "pack_id": slug,
        "prefix": default_prefix(slug),
        "label": top or slug,
    }

    def _sug(
        *,
        mode: str,
        reason: str,
        pack_id: str,
        prefix: str,
        label: str,
        score: int | None = None,
    ) -> dict[str, Any]:
        return {
            "suggested_mode": mode,  # existing | new
            "reason": reason,
            "pack_id": pack_id,
            "prefix": prefix,
            "label": label,
            "score": score,
            "proposed_new": proposed_new,
            "options": options,
            "file_count": len(files),
            "subdir": subdir or "",
        }

    hit, reason = _filename_prefix_votes(files, packs)
    if hit:
        meta = packs.get(hit) or {}
        return _sug(
            mode="existing",
            reason=reason or "filename_prefix",
            pack_id=hit,
            prefix=meta.get("prefix") or default_prefix(hit),
            label=meta.get("label") or hit,
        )

    alias_key = ALIAS_TO_PACK.get(top_l) or ALIAS_TO_PACK.get(slug)
    if alias_key:
        meta = packs.get(alias_key) or {}
        exists = alias_key in packs or alias_key in THEME_HINTS
        return _sug(
            mode="existing" if exists else "new",
            reason="subdir_alias",
            pack_id=alias_key,
            prefix=meta.get("prefix") or default_prefix(alias_key),
            label=meta.get("label")
            or {"classic": "Русская классическая литература", "wildlife": "Дикая природа России"}.get(
                alias_key, alias_key
            ),
        )

    sample = _sample_folder_text(files)
    scores: list[tuple[int, str]] = []
    candidates = set(packs.keys()) | set(THEME_HINTS.keys())
    for pid in candidates:
        sc = _score_text_against_hints(sample, pid, packs.get(pid))
        if slug and (slug == pid or slug.startswith(pid) or pid.startswith(slug)):
            sc += 5
        if sc:
            scores.append((sc, pid))
    scores.sort(reverse=True)
    if scores and scores[0][0] >= 4:
        pack_id = scores[0][1]
        meta = packs.get(pack_id) or {}
        return _sug(
            mode="existing",
            reason="content_match",
            pack_id=pack_id,
            prefix=meta.get("prefix") or default_prefix(pack_id),
            label=meta.get("label") or pack_id,
            score=scores[0][0],
        )

    if slug in packs:
        meta = packs[slug]
        return _sug(
            mode="existing",
            reason="existing_pack_id",
            pack_id=slug,
            prefix=meta.get("prefix") or default_prefix(slug),
            label=meta.get("label") or slug,
        )

    return _sug(
        mode="new",
        reason="new_subdir",
        pack_id=proposed_new["pack_id"],
        prefix=proposed_new["prefix"],
        label=proposed_new["label"],
    )


def confirm_pack_choice(
    db: Session,
    *,
    mode: str,
    pack_id: str | None = None,
    label: str | None = None,
    subdir: str | None = None,
) -> dict[str, Any]:
    """Persist user-confirmed category (existing or new) and return pack dict for import."""
    mode = (mode or "").strip().lower()
    if mode not in {"existing", "new"}:
        raise ValueError("mode must be existing|new")
    reg = _load_registry(db)
    packs: dict[str, Any] = dict(reg.get("packs") or {})
    top = ((subdir or "").replace("\\", "/").strip("/").split("/") or [""])[0]

    if mode == "existing":
        pid = slugify(pack_id or "")
        if not pid:
            raise ValueError("pack_id required for existing")
        if pid not in packs:
            packs[pid] = {
                "prefix": default_prefix(pid),
                "label": label
                or {"classic": "Русская классическая литература", "wildlife": "Дикая природа России"}.get(pid, pid),
                "aliases": [top] if top else [],
                "keywords": list(THEME_HINTS.get(pid, [])),
            }
        else:
            if label:
                packs[pid]["label"] = label
            if top:
                aliases = list(packs[pid].get("aliases") or [])
                if top not in aliases:
                    aliases.append(top)
                    packs[pid]["aliases"] = aliases
        reg["packs"] = packs
        _save_registry(db, reg)
        meta = packs[pid]
        return {
            "decision": "reuse",
            "reason": "user_existing",
            "pack_id": pid,
            "prefix": meta["prefix"],
            "label": meta.get("label") or pid,
            "created": False,
        }

    # new
    pid = slugify(pack_id or top or "pack")
    if not pid:
        raise ValueError("cannot derive pack_id for new category")
    if pid in packs:
        # User asked new but id exists → reuse that prefix (safe)
        meta = packs[pid]
        return {
            "decision": "reuse",
            "reason": "new_id_already_exists",
            "pack_id": pid,
            "prefix": meta["prefix"],
            "label": meta.get("label") or pid,
            "created": False,
        }
    prefix = default_prefix(pid)
    packs[pid] = {
        "prefix": prefix,
        "label": (label or top or pid).strip() or pid,
        "aliases": [top] if top else [],
        "keywords": [],
    }
    reg["packs"] = packs
    _save_registry(db, reg)
    return {
        "decision": "new",
        "reason": "user_new",
        "pack_id": pid,
        "prefix": prefix,
        "label": packs[pid]["label"],
        "created": True,
    }


# Back-compat alias used by older callers
def resolve_pack(db: Session, *, subdir: str | None, files: list[Path]) -> dict[str, Any]:
    sug = suggest_pack(db, subdir=subdir, files=files)
    return confirm_pack_choice(
        db,
        mode=sug["suggested_mode"],
        pack_id=sug["pack_id"],
        label=sug.get("label"),
        subdir=subdir,
    )


def apply_prefix_to_filename(name: str, prefix: str) -> str:
    base = Path(name).name
    if base.startswith("_") or base.lower().startswith("manifest"):
        return base
    if prefix and base.startswith(prefix):
        return base
    # avoid double-stamping another pack prefix when reusing? keep original if already pack_
    if re.match(r"^[a-z][a-z0-9]{1,24}_", base.lower()) and not base.startswith(prefix):
        # remapping categories is rare; strip old short prefix only if we explicitly reuse
        rest = re.sub(r"^[a-z][a-z0-9]{1,24}_", "", base, count=1, flags=re.I)
        return f"{prefix}{rest}" if rest else f"{prefix}{base}"
    return f"{prefix}{base}"


def sync_active_retrieval_prefixes(db: Session) -> str:
    """Set retrieval_filename_prefix to all prefixes present in active docs (+ registry)."""
    reg = _load_registry(db)
    prefixes: set[str] = set()
    for meta in (reg.get("packs") or {}).values():
        p = (meta or {}).get("prefix")
        if p:
            prefixes.add(p)
    rows = (
        db.query(DocumentVersion.original_name)
        .join(Document, Document.id == DocumentVersion.document_id)
        .filter(Document.deleted_at.is_(None), Document.status != "deleted")
        .limit(5000)
        .all()
    )
    for (name,) in rows:
        m = re.match(r"^([a-z][a-z0-9]{1,24}_)", (name or "").lower())
        if m:
            prefixes.add(m.group(1))
    # keep only prefixes that still have docs when possible
    active: list[str] = []
    for p in sorted(prefixes):
        if any((name or "").startswith(p) for (name,) in rows):
            active.append(p)
    value = ",".join(active) if active else ",".join(sorted(prefixes))
    row = db.get(AppSetting, PREFIX_SETTING)
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=PREFIX_SETTING, value=value))
    return value
