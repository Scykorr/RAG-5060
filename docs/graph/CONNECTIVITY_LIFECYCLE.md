# Connectivity index: offline + corpus lifecycle

Source requirement (post-1c / 1d clarifications). Applies to graph connectivity index, future themes, and tab state.

## Hard constraints

- Fully local / offline. No downloads, CDN, cloud dictionaries, or internet for graph build, cleanup, or “learning” (stop-lists, frequencies, IDF, entity vocab, thresholds, themes).
- Derived stats come from the **current tenant corpus** at build time.
- Manual lists are allowed only for **language rules** (play stage directions, edition markers, genre words) in an editable config (`configs/graph_lexicon.json`). No hard-coded knowledge of the current corpus (Pushkin, Karamazov, `classic_`).

## Scenarios

1. **Full replace** — storage cleared, new document set loaded.
2. **Top-up** — new batch added to an existing corpus.

## Cache / invalidation

- Index cache key: `tenant + corpus_fingerprint + index_version + params_hash`.
- **`params_hash` includes the SHA1 of `configs/graph_lexicon.json` content** (edit lexicon → new hash → new index path).
- Changing any part → new index. Keep last N files; orphaned caches must not crash the API.
- On `reset-rag` / clear storage: invalidate index, per-doc artifacts, themes, saved node layouts, build status, **current pointer**, and **lock** — **for that tenant only**.
- Empty corpus → empty graph, no errors.

## Atomic publish + last-good pointer

- Index and artifacts are written **atomically** (`*.tmp` + rename).
- Successful build updates `data_root()/graph/current/<tenant>.json` (`index_file`, fingerprint, `stale=false`).
- On build failure the pointer is **not** advanced; API keeps serving the previous intact index (with `stale=true` if corpus already changed).

## Build lock (single-flight)

- One build at a time **per tenant** via lock file `data_root()/graph/locks/<tenant>.lock` (pid + `started_at`), plus in-process mutex.
- Timeout (default 30 min) and dead-pid cleanup remove stale locks.
- A second rebuild while one is running does not start a parallel job (`already_running`).

## Stale after ingestion

- After ingestion / folder import settles: connectivity index is marked **`stale=true`** in current pointer + build status (previous index still readable).
- Rebuild: `POST /api/graph/index/rebuild` (manual), or automatically when `GRAPH_AUTO_REBUILD=true` / `graph.auto_rebuild` (`Settings.graph_auto_rebuild`).
- `GET /api/graph/index/stats` exposes `stale`, build status, mode, lifecycle counts, timings.

## Incremental model

- Per-document artifacts (clean text/tokens, entities, mentions, TF, content hash) are cached and reused when content is unchanged.
- Global stats (df, IDF, phrase frequency, sentence-start stop candidates) are recomputed cheaply from all artifact caches.
- Deleted documents drop their artifacts and edges; work merges are rebuilt.
- Node ids stay stable (`document_id`, `work_key`, `author_key`). Unknown saved layout positions are ignored.
- Top-up of a **second edition** or **new volume** of a known work updates work glue (`work_key` / `part_of`), `canonical`/`duplicate_of`, and `author_source` (e.g. `work_merge`).

## Build UX

- Rebuild runs in a background thread; does not block the request beyond enqueue.
- All caches, locks, and cleanup are scoped by **`tenant_id`**.

## Acceptance tests

1. Empty corpus → empty index, no error.
2. Full corpus replace leaves no old nodes/edges.
3. Top-up of two docs: unchanged from cache, new linked, df/IDF updated.
4. Delete document removes its edges.
5. Incremental result matches full rebuild on the same corpus (same edge set; weights within tolerance).
6. Top-up of second edition / new volume updates work glue, canonical/duplicate, and author_source.
7. `params_hash` changes when `graph_lexicon.json` changes.
8. Failed build keeps previous pointer/index; API serves it with `stale=true`.
9. Lock file prevents parallel rebuild; dead lock is cleared.
10. Invalidate tenant A does not touch tenant B caches/locks/pointer.
