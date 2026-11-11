# CAAS implementation status

## Implemented

- Django 5.2 LTS, APIView-based DRF, JWT access/rotating refresh tokens, UUID business IDs, tenant-scoped lookups, owner/admin/member roles and Django admin.
- Bot CRUD and atomic plan limits. Management remains accessible after expiry. Bot use above a downgraded limit is blocked, retaining data.
- FAQ/plain-text/text-PDF sources, validation and private storage, bounded extraction/chunking, asynchronous Celery processing, revision/lease checks, stale-chunk deletion and queued-work recovery.
- Hosted OpenAI Responses/embeddings adapter, real pgvector exact cosine retrieval, evidence references, bounded context/output and safe failures. Local demo is explicitly keyword retrieval/source excerpts with no AI or embeddings.
- Once-shown hashed API keys, scopes, optional bot binding, expiry, last-used timestamps, listing, rotation and revocation.
- Principal-owned chat continuation, organisation conversation review, pagination and bounded transcript details.
- Persistent usage periods, atomic answer/cost reservations, provider-attempt accounting, conservative ambiguous-cost retention, reservation expiry and chat idempotency.
- Editable trial/Starter/Business assumptions; server-priced NGN Paystack checkout; timestamped and opt-in legacy signatures; durable webhook receipt; server verification; idempotent grants; manual-renewal 30-day periods; reconciliation command.
- Minimal iframe widget, public bot identifiers, expiring visitor sessions, origin checks/CSP, text-only rendering and shared quotas/cost safeguards.
- Non-root Docker image, Compose PostgreSQL/pgvector and Redis, seed/recovery commands, environment example, OpenAPI/Swagger, curl examples and runnable local HTTP smoke journey.

## External setup required

Supply your OpenAI key and compatible chat/1536-dimension embedding models; verify current model costs and configure conservative per-token upper bounds. Supply Paystack test credentials/webhook signing secret and complete the README test-mode checklist. No live hosted-AI request, real Paystack checkout, charge or webhook delivery has been claimed as verified.

Before public traffic, configure TLS/trusted ingress, exact origins/hosts, private database/Redis, persistent private uploads, backups, atomic edge rate limits, monitoring, privacy/retention policy and business terms. Application throttles are best effort and do not prove resistance to distributed abuse. Readiness verifies infrastructure dependencies rather than worker/provider health.

## Intentional limits

- One organisation per user. Membership provisioning/roles use trusted platform admin; no self-service team invitations.
- Manual-renewal purchases only. No automatic subscription debit, recurring cancellation job, dunning, refunds or dispute automation. Existing paid access is preserved; plan changes begin after the current paid-through period.
- No fake email endpoints: email verification and password recovery are deferred.
- No website crawler, OCR, streaming, frontend dashboard, marketplace proxy identity integration or marketplace approval/payout claim.
- Model changes require knowledge reprocessing. Vector retrieval uses exact search and a fixed relevance threshold; evaluate answer quality on representative customer questions before launch.
- PDF extraction runs in the upload request; chunking/embeddings run in Celery. Complex PDFs may need a separate resource-isolated extraction process at scale.
- Provider POST retry count is deliberately zero. A timeout/crash is ambiguous; its upper-bound cost is retained and the idempotency key is terminal. Explicit new requests/source edits may initiate new paid attempts.
- Reconciliation handles up to 100 unfulfilled purchases per run, oldest first; specific-reference reconciliation bypasses that batch selection. Failed webhook verifications need reconciliation/operator attention.
- Conversation details show the latest 100 messages. Full export, retention schedules, orphan-upload sweeping after process crashes and tenant deletion workflows are deferred. Billing/idempotency records survive bot/conversation deletion.
- Widget sessions last 15 minutes; reload starts a new visitor identity and cannot continue a prior session. Parent sites must send Referer origins. Non-browser clients can spoof origins; public widget traffic consumes the account's shared allowance.
- USD cost estimates depend on your configured model price bounds. NGN prices are unvalidated product assumptions and do not guarantee margins.

## Verification

See the final verification entries below. Automated provider/payment tests use mocks; PostgreSQL tests use local infrastructure and synthetic vectors. No external charges, public deployment, marketplace publication or remote push occurred.

Verified locally:

- Docker image built successfully with pinned Python dependencies; `pip check` reported no broken requirements.
- Migrations applied successfully to SQLite and PostgreSQL 16 with pgvector. `makemigrations --check --dry-run`: no changes.
- 42 automated tests passed against PostgreSQL, with no skips. This includes two real concurrent row-lock tests and a real pgvector query with synthetic embeddings.
- SQLite demo suite: 42 tests, 39 passed and three PostgreSQL-only tests explicitly skipped.
- Django system checks passed. Production `check --deploy --fail-level WARNING` passed with placeholder provider settings and no provider calls.
- Production startup with missing provider configuration failed closed as intended.
- OpenAPI generated and validated with `--validate --fail-on-warn`, with no warnings.
- Real HTTP smoke journey passed against gunicorn + PostgreSQL + Redis + an actual asynchronous Celery worker: registration, source processing, demo answers/citations, idempotency, scoped keys, cross-tenant denial, usage and visitor chat.
- Widget JavaScript passed Node syntax checks; iframe HTML, Referer/CSP and visitor APIs were tested. Visual browser interaction could not run because the computer-use environment had no available browser.

The local Compose web/worker/beat/database/Redis services were left running for review. Demo Swagger is at http://localhost:8000/api/v1/docs/. Stop them with `docker compose stop` when finished. No public deployment or remote push was performed.
