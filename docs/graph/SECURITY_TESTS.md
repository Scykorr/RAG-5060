# Security checks — Graph

| Case | Result |
| --- | --- |
| No cookie → graph API | 401 |
| Edge/evidence only if both docs in tenant & not deleted | enforced in `GraphService` |
| Rejected edges hidden from non-admin | yes |
| Review/jobs/manual/export | admin only (`require_admin`) |
| Uniform miss for foreign ids | 404 via GRAPH_*_NOT_FOUND_OR_FORBIDDEN |
| Snippets not in neighborhood payload | yes — separate `/evidence` |
| XSS: UI uses textContent / esc() | graph.js |
| access_groups | **not in DB** — same tenant visibility as documents list (documented gap) |
