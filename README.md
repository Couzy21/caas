# CAAS — Chatbot as a Service

API-first Django 5.2 LTS MVP for businesses, developers and agencies. Includes Django admin, DRF APIViews, JWT, scoped server API keys, private knowledge ingestion, pgvector retrieval, Celery/Redis, usage safeguards, Paystack checkout, and an iframe website widget. No Next.js dashboard.

**Local demo is keyword retrieval and source excerpts. It uses no live AI and no real embeddings.** Production refuses to start without explicit provider configuration. Tests never make external AI calls or charges.

## Quick start: Docker (recommended)

Requires Docker Desktop / Docker Engine with Compose. Run from this directory. The checked-in `.env.example` contains no credentials. If `.env` already exists, preserve it and fill any missing settings instead of copying over it.

```sh
cp .env.example .env
python3 -c 'from pathlib import Path; import secrets; p=Path(".env"); p.write_text(p.read_text().replace("SECRET_KEY=\n", "SECRET_KEY=" + secrets.token_urlsafe(64) + "\n"))'
docker compose up -d db redis
docker compose build
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py seed_plans
docker compose run --rm web python manage.py seed_demo
docker compose run --rm web python manage.py createsuperuser
docker compose up -d web worker beat
```

`seed_demo` prints a randomly generated demo password and a public bot UUID. Save the password; it is never stored in plaintext. It queues a delivery FAQ for the worker. Once the worker runs, poll its source status or ask “What is the delivery fee?”. Rerunning the command preserves the existing demo. Reset with `docker compose run --rm web python manage.py changepassword demo`.

Open:

- Swagger UI: http://localhost:8000/api/v1/docs/
- OpenAPI: http://localhost:8000/api/v1/schema/ (also committed as `docs/openapi.yaml`)
- Admin: http://localhost:8000/admin/
- Widget example: `http://localhost:8000/widget-example/?bot=PUBLIC-BOT-UUID`
- Readiness: http://localhost:8000/api/v1/health/ready/

Compose binds HTTP, PostgreSQL (5434), and Redis (6381) to loopback. Uploads, database and Redis use Docker volumes. `docker compose stop` stops services and preserves data. Use strong database credentials outside local development.

## Native Python + Docker dependencies

Python 3.12 was used to install and pin `requirements.txt`. Other Python versions have not been verified. Compose overrides database and Redis URLs inside containers; `.env.example` points to their host ports for native development.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
docker compose up -d db redis
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_plans
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Run in separate terminals with the virtualenv activated:

```sh
.venv/bin/celery -A config worker --loglevel=WARNING --concurrency=2
.venv/bin/celery -A config beat --loglevel=WARNING --schedule=/tmp/caas-celerybeat
```

Run one beat instance per environment. It recovers stale usage reservations and republishes queued knowledge and payment work every minute. Do not run multiple renewal schedulers: **there are no automatic subscription renewals in this MVP**.

## No-Docker local demo

This deliberately uses SQLite and eager tasks. It cannot establish PostgreSQL locking or vector correctness; use Docker for those tests. Shell overrides below take precedence over `.env`:

```sh
export DEMO_MODE=true
export DATABASE_URL=sqlite:///demo.sqlite3
export CELERY_TASK_ALWAYS_EAGER=true
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

Unset these overrides before returning to PostgreSQL or production. Demo mode accepts a local fallback secret only when `SECRET_KEY` is absent; if `.env` contains an empty `SECRET_KEY`, generate one as shown above.

## Tests and validation

Run against the Compose PostgreSQL/pgvector database. The test user needs permission to create a test database, which the local Compose user has. **Never use a production database URL for tests.**

```sh
docker compose run --rm web python manage.py check
docker compose run --rm web python manage.py makemigrations --check --dry-run
docker compose run --rm web python manage.py test --settings=config.test_settings --verbosity=2
docker compose run --rm web python manage.py spectacular --file /tmp/openapi.yaml --validate --fail-on-warn
```

Fast SQLite suite (PostgreSQL-specific tests are explicitly skipped):

```sh
DEMO_MODE=true DATABASE_URL=sqlite:///demo.sqlite3 .venv/bin/python manage.py test --settings=config.test_settings
```

Automated tests use mocked hosted-provider/Paystack results, real Django APIs, real migrations, real PostgreSQL row locks, and real pgvector distance queries with synthetic vectors. They cover auth, roles, cross-tenant access, API keys, validation, ingestion retry/edit/delete, missing knowledge, provider failures, conservative crash recovery, quotas, concurrent reservations, idempotency, payment signatures and mismatches, repeated/out-of-order webhooks, renewal periods, and visitor permissions. External credentials are not required.

## Developer quickstart

See [examples/API.md](examples/API.md) for complete curl requests and [docs/openapi.yaml](docs/openapi.yaml) for the schema. Run `python examples/smoke.py` against the local server for a demo end-to-end journey, including registration, ingestion polling, JWT chat, API-key chat, usage, visitor chat and tenant isolation.

Normal responses have `message` and `data`. Paginated lists have `message`, `count`, `next`, `previous`, `results`. Validation/auth errors have `message` and `errors`. Chat requires `Idempotency-Key`; a replay returns the saved answer without a new provider call. Same key with a changed validated payload returns 409; a pending request returns 409. Failed/uncertain requests are terminal for that key and return 503 on replay. Use a new key only when you intentionally want a new provider attempt.

One organisation per user is an intentional MVP constraint. Registration creates its owner membership. Team invitation and role management are performed by a trusted platform superuser in Django admin. Customers cannot set their own organisation or role through API payloads.

| Routes under `/api/v1/` | Authentication / roles |
| --- | --- |
| `auth/register/`, `auth/login/`, `auth/refresh/`, `plans/`, `health/live/`, `health/ready/`, `schema/`, `docs/` | Public; auth endpoints rate limited |
| `me/`, `organisation/`, `bots/`, `bots/{id}/`, `bots/{id}/sources/`, `bots/{id}/sources/{id}/`, `usage/` | JWT for same organisation; bot/source writes require owner or admin |
| `keys/`, `keys/{id}/rotate/`, `keys/{id}/revoke/` | JWT owner or admin; secret shown once |
| `bots/{id}/chat/` | JWT member or scoped API key with `chat:write`; optional bot binding enforced |
| `conversations/`, `conversations/{id}/` | JWT sees organisation conversations; key requires `conversations:read` and sees only its own |
| `billing/checkout/` | JWT owner or admin |
| `billing/paystack/webhook/` | Raw-body Paystack signature, no JWT |
| `widget/{id}/session/` | Public bot ID plus configured exact website origin |
| `widget/{id}/chat/` | Short-lived visitor token, website origin, idempotency key; no management access |

Lists default to 20 rows; use `?page=2&page_size=50` (max 100). Conversation details return the latest 100 messages. Chat continuation additionally verifies bot, tenant and exact principal; another user in the same organisation cannot append to your conversation. Bot-scoped keys cannot access other bots. Keys are server credentials; never put them in widget JavaScript.

## Knowledge and provider configuration

FAQs are text containing a question and answer. Plain text is accepted as JSON. PDFs are multipart uploads and must contain selectable text. Unsupported, unreadable, encrypted, scanned/empty PDFs are rejected. Limits: 5 MiB upload, 100 pages, 100,000 extracted characters, 150 chunks, 100 sources and 20 MiB total stored source bytes per organisation. Raw upload plus UTF-8 extracted text count toward storage. Edit/delete removes stale chunks immediately. Source states: queued, processing, ready, failed. Edit a failed source to retry explicitly. File bodies live in a private volume with no public media route.

PDF extraction validates uploads in the web request; chunking and embedding run in Celery. Worker leases and source revisions prevent duplicate/stale chunk commits. Queue publication uses a persistent queued database state and a periodic recovery scan. Provider POST retries are zero: a timeout may follow an accepted billable call. A crashed/ambiguous embedding attempt is marked failed instead of automatically issuing another paid request; explicit source edits can retry.

Live mode uses `POST /v1/embeddings` with 1536 dimensions and `POST /v1/responses`, no tools, `store=false`, a 25-second provider timeout and zero automatic POST retries. Choose compatible server-side `CHAT_MODEL` and `EMBEDDING_MODEL`. Changing embedding model requires editing/reprocessing existing sources. pgvector exact cosine search is tenant/bot scoped; an approximate vector index is deferred until the dataset warrants it. The fixed relevance threshold should be evaluated on your business documents.

Chat input is at most 2,000 characters, history at most six messages truncated to 600 characters each, evidence at most five 800-character chunks, and output at most 400 tokens. Retrieved text and conversation history are explicitly treated as untrusted data. Citations identify retrieved evidence; they are not a guarantee that every generated statement is grounded. Missing relevant knowledge returns an insufficiency message without a billable answer. Live generated answers with evidence count toward successful answers; the local demo simulates these answer counts without provider costs.

## Plans, usage, and spending

Editable provisional assumptions, **not validated profitable prices**:

| Plan | NGN price per 30 days | Bots | Successful answers |
| --- | ---: | ---: | ---: |
| Trial | Free, once at registration | 1 | 50 |
| Starter | ₦15,000 | 1 | 500 |
| Business | ₦30,000 | 3 | 1,500 |

Prices are integer kobo. `seed_plans` only creates missing rows; it never overwrites admin edits. Entitlement limits and payment terms are snapshotted. A successful purchase ends an active trial immediately. Further purchases queue 30 days after existing paid-through access, including plan changes. There is no automatic recurring billing or cancellation debit; simply do not renew. Paid-through access remains valid until expiry. Data/billing management remains accessible afterwards.

PostgreSQL organisation-row locks serialize bot creation, reservation, cost checks and payment grants. Persistent usage records reserve answer slots and estimated provider spending before calls. Success finalises actual reported tokens and configured costs. Failure releases the answer slot but retains the entire cost ceiling if a provider could have accepted a call. Expired reservations recover after 180 seconds, release the answer slot, become terminal, and retain uncertain provider cost. Late responses cannot commit another answer. Idempotency survives bot/conversation deletion; records are retained for billing audit.

`REQUEST_COST_MICROUSD` and per-period `spend_limit_microusd` are hard admission limits measured in USD micro-dollars ($1 = 1,000,000). Defaults are launch safeguards, **not current provider price claims**. Set input/output/embedding per-token values to conservative upper bounds for your selected models, not rounded-down averages. Chat reserves a conservative 50,000-input-token envelope plus output/query embedding bounds; ingestion reserves UTF-8 byte count as a token upper bound. Large documents may hit the per-request spending limit before their size limit. Tune carefully. Provider errors/crashes retain this upper bound, so spending safeguards may stop service before answer quotas. Inspect usage and audit uncertain attempts before changing limits. Set independent provider project budgets, alerts, and rate limits as a second layer.

## Paystack setup and verification still required

Set `PAYSTACK_SECRET_KEY` to a test key initially, and configure a webhook signing secret (`PAYSTACK_WEBHOOK_SECRET`, `whsec_…`). Set the webhook URL to `https://YOUR-HOST/api/v1/billing/paystack/webhook/`. The backend determines the NGN amount and records organisation, plan, email, reference and immutable terms before initialising checkout.

Current signature support includes timestamped `Paystack-Signature` (HMAC-SHA512 of timestamp + `.` + raw body, five-minute tolerance, multiple signature entries) and explicitly opt-in legacy `x-paystack-signature` (HMAC-SHA512 of raw body with the API secret). A present invalid modern signature never falls back to legacy. Signature checks use constant-time comparisons. For legacy-only accounts enable `PAYSTACK_ALLOW_LEGACY_SIGNATURE=true`; switch it off once modern signing is active.

Webhook receipt persists the reference/digest/state, acknowledges, and queues a server-side verification GET. Access requires matching success status, reference, integer amount, currency, customer email, organisation metadata and plan. Redirects never grant access. Duplicate events and reordered success events cannot grant twice or reset quotas. Unknown references are acknowledged and ignored. Failed verifications are recorded for reconciliation; they do not grant access.

Before launching, use Paystack **test mode** to verify: checkout opens and completes; the real signature reaches your endpoint; the worker verifies matching amount/currency/customer/metadata; access starts once; resend produces no extra period; a missed event is repaired by reconciliation; failed/abandoned checkout grants nothing. No live payment or hosted-AI integration has been verified by the automated suite.

```sh
docker compose run --rm web python manage.py reconcile_payments --reference caas_YOUR_REFERENCE
docker compose run --rm web python manage.py reconcile_payments
docker compose run --rm web python manage.py recover_work
```

Reconciliation scans up to 100 unfulfilled purchases per invocation, oldest first. Use `--reference` for a specific missed payment. It verifies existing transactions and never initiates a charge. Run it periodically under your scheduler, monitor failures, and use admin for audit. Refunds/disputes and automatic renewal/dunning are deferred; handle those manually before expanding billing scope.

## Widget installation

Add the exact website origin to `Bot.allowed_origins`, such as `https://example.com` (no trailing slash, path or wildcard). Production accepts HTTPS origins only.

```html
<script src="https://YOUR-CAAS-HOST/static/widget/loader.js"
        data-bot="PUBLIC-BOT-UUID" defer></script>
```

The loader opens an iframe and contains no customer API key. Its HTML endpoint checks the parent Referer origin and emits `frame-ancestors` CSP; the iframe creates a 15-minute, bot-scoped visitor session. Its chat endpoint checks the session, origin and current bot configuration. The iframe makes same-origin API requests, renders text with `textContent`, and uses no `postMessage`. Your parent site must allow the CAAS host in its script/frame CSP and send at least the origin in its Referer header. Sites using `no-referrer` cannot bootstrap the widget. Reload to start a new session after expiry; transcripts from prior visitor sessions cannot be continued by a new session.

**Origins can be spoofed by non-browser clients.** Public widgets expose a public capability and consume the same organisation quota and budget as server chats. Redis-backed application throttles are a best-effort additional layer; configure atomic edge limits, per-IP abuse controls and monitoring at your reverse proxy before public traffic. The widget supports network retry of the same pending request; a terminal provider failure is replayed for that key, and reloading starts a new visitor session.

## Production deployment

No deployment, paid resources, marketplace publishing or GitHub push is part of this repository's setup.

1. Build the non-root Docker image. Supply a random 50+ character `SECRET_KEY`, `DEMO_MODE=false`, `DEBUG=false`, exact `ALLOWED_HOSTS`, HTTPS `PUBLIC_BASE_URL`, PostgreSQL URL, private Redis URL, provider models/key and billing credentials. Set conservative token cost bounds for your models.
2. Provision PostgreSQL with pgvector. The initial migration enables `vector`; if the runtime database role cannot create extensions, a database administrator must enable it first. Separate deployment/migration and runtime privileges in production.
3. Use a persistent private upload volume writable by UID 10001. Do not expose it through nginx or object-store public ACLs. Back up both database and uploads and test restoration. Redis must be private and authenticated in production.
4. Run `python manage.py migrate --noinput`, `python manage.py seed_plans`, `python manage.py createsuperuser`, `python manage.py collectstatic --noinput`, and `python manage.py check --deploy`. Do not run `seed_demo` in production.
5. Run gunicorn, Celery worker and one beat process. Terminate TLS at your ingress. Configure trusted proxy headers in settings only if your proxy strips and sets them reliably; otherwise Django may redirect TLS-terminated traffic in a loop. Restrict request bodies to 6 MiB, enforce edge rate limits, and disable access logs that include headers/query strings. The application log formatter excludes free-form request/exception content and emits only event/level/logger.
6. Readiness checks the database, required plan seed and pgvector extension, plus Redis in production. It does not prove worker liveness or provider availability; monitor Celery heartbeats, queue age, failed sources, uncertain usage, billing failures and reconciliation separately.
7. Run the Paystack test-mode checklist and a small explicitly authorised hosted-provider validation with synthetic business data before taking customers. Retention/deletion policies, privacy/terms, provider data-processing agreements and operational support still need your business decisions.

The Swagger page uses the drf-spectacular default hosted Swagger assets; host these locally if your deployment blocks external script assets. JWT auth is stateless; admin uses Django sessions/CSRF. Email verification/password recovery are explicitly deferred; no fake email endpoints exist. Use a real email provider and tested flow before adding them.

## Marketplace readiness

The backend is hosted by you. Developer integrations use per-organisation scoped keys and the same quota/billing service. OpenAPI and examples are prepared for a future listing; no marketplace approval, Nigerian seller payout eligibility or hosting is assumed. Client-supplied marketplace identity headers are ignored. Trusted proxy authentication and verified subscriber mapping are deferred.

## Official documentation consulted

- [Django supported releases](https://www.djangoproject.com/download/)
- [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create.md)
- [OpenAI embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create)
- [Paystack transaction API](https://paystack.com/docs/api/transaction/)
- [Paystack webhook signatures and retries](https://paystack.com/docs/payments/webhooks/)

See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) for verification evidence and known limitations.
