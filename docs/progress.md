# Progress

## 2026-11-06 — plan gap-fill (Admin UI Launcher)

Closed remaining items from `.cursor/plans/admin_ui_launcher_1e36b3e7.plan.md`:
- System panel + `/api/admin/logs` (safe job tail), compose/offline/hints on `/api/admin/system`
- Storage tombstone cleanup in UI
- Model Hub: URL progress, size limit, optional sha256, cancel job
- Launcher preflight disk/GPU + `RAG_DATA_ROOT`; `scripts/export-offline-images.sh`
- ROADMAP F11–F13 checkboxes marked done (static SPA accepted; React optional)

**Verify** (`artifacts/20261106-082053-post-plan`) — **passed** (`FAIL=0`)
- rebuilt `api` image; stack healthy on `:8080`
- admin system/logs/storage/dispatcher; activate `qwen3:8b` smoke OK
- UI System panel + tombstone/sha fields present
- S2: upload → ready → chat «12500 / НДС 20%» + citations

## 2026-11-06 — F14 Corpus Quality Loop

## 2026-11-06 — Query intent (произвольные RU-запросы)

- `apps/api/services/query_intent.py` + routing в `chat.py`: catalog / author_overview / summarize / extractive|cloze
- Smoke: «сколько Чехова…» → catalog, 77 названий; AGENTS.md секция «Chat: произвольные RU-запросы»
- Канон: S33–S34, A12; зеркала `Обучение/docs/*`

## earlier — quality loop

Implemented offline quality loop + doc search + incremental reload:
- `/api/documents?q=` + corpus counts; Storage corpus metrics
- `/api/admin/quality/*` (level, runs, eval import/synth, auto-improve)
- fingerprint stale on upload/import; quality on full corpus (prefix cleared during eval)
- Admin tab «Качество»; ROADMAP F14

**Verify** (`artifacts/20261106-111729-quality`) — API/UI smoke **FAIL=0** (doc search, A→B reload, level offline).  
**Score fix** (`artifacts/20261106-112220-quality-fix`) — quality run **ok / strong / score=1.0** after clearing pack prefix during eval.

## 2026-11-03 — S1b → S2 → S3 verified

**S1b** (`artifacts/20261103-101124-s1b`) — verified
- Admin `/api/admin/system` + `/storage`; UI static `/` `/app.js` `/styles.css`
- Model Hub: `qwen3:8b` activated (smoke `OK`); launcher binaries + `status`
- Stack healthy on `127.0.0.1:8080`

**S2** (`artifacts/20261103-101308-s2`) — verified
- login → upload `sample.txt` → job `ready` → chat with citation
- Answer: invoice 12500 RUB / VAT 20% `[1]`; storage originals present

**S3** (`artifacts/20261103-102457-s3`) — verified (MVP slice)
- Tombstone delete + 404; deleted doc not listed; unknown UUID 404
- `POST /api/jobs/{id}/retry` (attempts++)
- API container restart + health restore
- `scripts/backup-mvp.sh` → `D:\RAG5060\backups\<stamp>\` (pg dump + ragdata + manifest)
- Retrieval stopwords → negative question `not_found`

**Constraint**
- C: ≥50 GB; heavy data on D: / WSL VHDX; `OLLAMA_NO_CLOUD=1`
