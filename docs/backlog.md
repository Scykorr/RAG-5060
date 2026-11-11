# Backlog (F-IDs × stages)

Statuses: `planned` → `ready` → `in_progress` → `review` → `verified` | `blocked`

| task_id | F-ID | Stage | Goal | Status | Notes |
| --- | --- | --- | --- | --- | --- |
| T-S0-01 | F01.1 | S0 | Hardware report + disk placement | verified | artifacts/20261030-s0-inventory |
| T-S0-02 | F01.1 | S0 | ADR Engine/WSL/Valkey/UI | verified | docs/adr/0001 |
| T-S0-03 | F02/F03/F04 | S0 | API/data schema draft + THIRD_PARTY | verified | |
| T-S0-04 | — | S0 | Repo tree, AGENTS, scripts stubs | verified | |
| T-S1-01 | F01.2 | S1 | Docker Engine + Compose in Ubuntu | verified | Engine 29.9.2 |
| T-S1-02 | F01.3 | S1 | NVIDIA Container Toolkit + GPU smoke | verified | |
| T-S1-03 | F01.4 | S1 | Compose core services | verified | ollama 0.12.6 |
| T-S1-04 | F01.5 | S1 | API health + web shell | verified | |
| T-S1b-01 | F11 | S1b | Admin dashboard/system API+UI | verified | artifacts/…-s1b |
| T-S1b-02 | F13 | S1b | Model Hub tag/GGUF/URL | verified | activate+smoke OK; GGUF/URL jobs present |
| T-S1b-03 | F11.3 | S1b | Storage management UI | verified | |
| T-S1b-04 | F12 | S1b | Launcher Win+Linux | verified | dist/*.exe + linux; status OK |
| T-S2-01 | F02–F06 | S2 | Vertical slice login→upload→chat | verified | artifacts/…-s2 |
| T-S3-01 | F03.6/F09 | S3 | Restart/retry/delete/ACL/backup | verified | artifacts/…-s3 MVP |
| T-S4-01 | F04/F07 | S4 | Pilot 100–500 docs + eval set | planned | |
| T-S3b-01 | F14 | S3b | Corpus quality loop + doc search + incremental reload | verified | artifacts/…-quality + …-quality-fix |

| T-GRAPH-IDX-01 | F04.18 | S3c | text_entities extract from chat (no behaviour change) | verified | step1 connectivity index |
| T-GRAPH-IDX-02 | F04.18 | S3c | graph_index layers A/B + unit tests | verified | with C/D/E pure fns |
| T-GRAPH-IDX-03 | F04.18 | S3c | graph_index layers C/D + unit tests | verified | same module |
| T-GRAPH-IDX-04 | F04.18 | S3c | cache wire-up, API /index/*, report script | verified | report artifacts/20261110-120700-graph-index |
| T-GRAPH-IDX-1B | F04.18 | S3c | Clean index: work_key, refers/entity/lex fixes | verified | report artifacts/20261110-123456-graph-index |
| T-GRAPH-IDX-1C | F04.18 | S3c | Index cleanup 1c: text clean, mentions_author, strong refers, entities, lex, work merge | verified | report artifacts/20261110-133444-graph-index |
| T-GRAPH-IDX-1D | F04.18 | S3c | Offline lifecycle: per-doc cache, incremental≈full, background rebuild, reset invalidate | verified | S54/A19; unit 32 passed |
| T-GRAPH-IDX-1D-A | F04.18b | S3c | Quality 1d: strict refers/lex/ents/authors/dupes + report; lifecycle verify | verified | S55/A20; report artifacts/20261111-050958; INDEX_VERSION=9 |
| T-GRAPH-IDX-1D-C | F04.18c | S3c | Lifecycle clarifications: lexicon in params_hash, atomic+pointer, file lock, stale/auto_rebuild, tenant iso, test6 | verified | S54/A19; pytest test_graph_index_lifecycle 15 + suite 64 |
| T-GRAPH-IDX-1E | F04.18d | S3c | 1e: duplicates/contains, work-lex, co_cited, entities, refers, overrides, noop | in_progress | E1–E8 code+unit; report pending |

## Ready next

Theme clustering / Obsidian-like graph tab; F14 smoke / S4 pilot.
