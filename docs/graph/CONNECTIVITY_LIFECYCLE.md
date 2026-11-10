# Connectivity index: offline + corpus lifecycle

Source requirement (post-1c). Applies to graph connectivity index, future themes, and tab state.

## Hard constraints

- Fully local / offline. No downloads, CDN, cloud dictionaries, or internet for graph build, cleanup, or “learning” (stop-lists, frequencies, IDF, entity vocab, thresholds, themes).
- Derived stats come from the **current tenant corpus** at build time.
- Manual lists are allowed only for **language rules** (play stage directions, edition markers, genre words) in an editable config (`configs/graph_lexicon.json`). No hard-coded knowledge of the current corpus (Pushkin, Karamazov, `classic_`).

## Scenarios

1. **Full replace** — storage cleared, new document set loaded.
2. **Top-up** — new batch added to an existing corpus.

## Cache / invalidation

- Index cache key: `tenant + corpus_fingerprint + index_version + params_hash`.
- Changing any part → new index. Keep last N files; orphaned caches must not crash the API.
- On `reset-rag` / clear storage: invalidate index, per-doc artifacts, themes, saved node layouts.
- Empty corpus → empty graph, no errors.

## Incremental model

- Per-document artifacts (clean text/tokens, entities, mentions, TF, content hash) are cached and reused when content is unchanged.
- Global stats (df, IDF, phrase frequency, sentence-start stop candidates) are recomputed cheaply from all artifact caches.
- Deleted documents drop their artifacts and edges; work merges are rebuilt.
- Node ids stay stable (`document_id`, `work_key`, `author_key`). Unknown saved layout positions are ignored.

## Build UX

- Rebuild runs in a background thread; does not block the request beyond enqueue.
- A second rebuild while one is running does not start a parallel job.
- `GET /api/graph/index/stats` exposes build status + mode (`full` | `incremental`) and counts (added / changed / removed / cached) + step timings.

## Acceptance tests

1. Empty corpus → empty index, no error.
2. Full corpus replace leaves no old nodes/edges.
3. Top-up of two docs: unchanged from cache, new linked, df/IDF updated.
4. Delete document removes its edges.
5. Incremental result matches full rebuild on the same corpus (same edge set; weights within tolerance).
