# Data model (MVP) — PostgreSQL source of truth

Tenant-safe FKs: every child row carries `tenant_id` matching parent.

## Core tables

- `tenants(id, name, created_at)`
- `users(id, login UNIQUE, password_hash, is_active, created_at)`
- `tenant_memberships(tenant_id, user_id, role)` UNIQUE(tenant_id, user_id); roles: `admin` | `member`
- `access_groups(id, tenant_id, name)` UNIQUE(tenant_id, name)
- `group_memberships(tenant_id, group_id, user_id)`
- `documents(id, tenant_id, owner_id, domain[], status, active_version_id NULL, deleted_at, created_at, updated_at)`
- `document_access_groups(tenant_id, document_id, group_id)`
- `document_versions(id, tenant_id, document_id, version_number, sha256, original_name, mime_type, source_uri, storage_key, status, effective_from/to, parser_version, chunker_version, embedding_version, expected_chunk_count, indexed_chunk_count, created_at)` UNIQUE(document_id, version_number) — **no global UNIQUE(sha256)**
- `chunks(id, tenant_id, document_id, version_id, ordinal, text, section_path[], page_start/end, table_id, language, token_count, metadata_json, embedding_version)` UNIQUE(version_id, ordinal)
- `ingestion_jobs(id, tenant_id, document_id, version_id, stage, progress, attempts, max_attempts, lease_until, heartbeat_at, error_code, safe_error, started_at, finished_at)`
- `outbox_events(id, tenant_id, job_id, type, payload, created_at, published_at, attempts)`
- `query_audit(id, tenant_id, user_id, timestamp, filters, retrieved_chunk_ids, model_id/revision, duration_ms, outcome)` — no document bodies/secrets by default

## Version status

`pending` → `parsing` → `chunking` → `embedding` → `indexing` → `ready`  
Also: `needs_ocr`, `failed`, `cancelled`, `deleting`, `deleted`.

Publish only after non-empty text + expected chunks/vectors verified; set `ready` + `active_version_id` transactionally.

## ACL read rule

Active tenant membership AND (`admin` OR owner OR group intersection). Empty groups ⇒ owner/admin only.

## Qdrant payload (dense 1024, cosine)

`tenant_id`, `document_id`, `version_id`, `chunk_id`, `owner_id`, `access_groups`, `domain`, `document_type`, `language`, `effective_from/to`, `page`, `section_path`, `embedding_version`, `index_generation` + payload indexes for used filters.
