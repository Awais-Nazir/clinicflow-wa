# ClinicFlow WA

ClinicFlow WA is a multi-tenant WhatsApp assistant for clinics. The project will
be built incrementally, beginning with a reliable webhook-to-worker pipeline and
growing into grounded FAQ answers, booking, voice notes, human handoff, and
multi-clinic isolation.

## Current milestone

ClinicFlow supports local FAQ, booking, voice notes, staff handoff, and multiple
fictional clinics. The local evaluations build on Phase 0's delivery pipeline.

See [the Phase 1 plan](docs/phase-1.md) for the small-step build sequence and
[the completed Phase 0 plan](docs/phase-0.md) for the deployed foundation.

## Technology direction

- Python 3.12
- FastAPI
- PostgreSQL and Alembic
- A separate web process and worker process
- Heroku for the first deployment

Dependencies are managed with uv and locked in `uv.lock`.

## Local development

Create or synchronize the project environment:

```powershell
uv sync
```

Run the development server:

```powershell
uv run uvicorn clinicflow.main:app --reload
```

Then open `http://127.0.0.1:8000/health`. The expected response is:

```json
{"status": "ok"}
```

Run the automated tests:

```powershell
uv run pytest
```

Run the Phase 1 FAQ regression suite:

```powershell
uv run python -m clinicflow.evals
```

By default it uses a deterministic, zero-cost fixture model while exercising
the real tenant-scoped tools and numeric guardrail. Its 25 versioned cases form
the pipeline baseline. Pass `--provider ollama` to measure the real local model.

For local model evals, start Ollama using its desktop app or run `ollama serve`
in a separate terminal. Use an isolated local database with the migrations
applied. The eval runner seeds fictional clinic data into the database it uses.

```powershell
ollama pull qwen3:4b-instruct-2507-q4_K_M
uv run python -m clinicflow.evals --provider ollama
```

Ollama defaults to `http://127.0.0.1:11434`. Set `OLLAMA_BASE_URL` when it is
running on another port, and use `OLLAMA_MODEL` to test another local model.
Add `--case hydra-price-en` to run one case while developing.

For Ollama behind Cloudflare Access, use the published HTTPS hostname as
`OLLAMA_BASE_URL` and set `OLLAMA_CF_ACCESS_CLIENT_ID` and
`OLLAMA_CF_ACCESS_CLIENT_SECRET` in the worker's config vars. With both
credentials configured, the worker authenticates each Ollama chat request.

## Local PostgreSQL

Start the Compose-managed PostgreSQL 18 container:

```powershell
docker compose up -d postgres
$env:DATABASE_URL = "postgresql+psycopg://clinicflow:clinicflow@127.0.0.1:5433/clinicflow"
uv run alembic upgrade head
```

The database is bound to `127.0.0.1:5433` and persists in the
`clinicflow-postgres-data` Docker volume. The Compose service uses a pinned
PostgreSQL 18 image with pgvector included; Alembic enables the `vector`
extension. `docker compose down` stops the container without deleting its data.

Seed the fictional Phase 1 clinic after applying migrations:

```powershell
$env:WHATSAPP_PHONE_NUMBER_ID = "your-meta-phone-number-id"
uv run python -m clinicflow.seed_demo
```

The seed is idempotent: running it again updates the same demo clinic and
service records rather than creating duplicates.

## Webhook verification

Meta verifies a webhook URL with a GET request before it sends message events.
Set a local verification token before trying the endpoint:

```powershell
$env:META_VERIFY_TOKEN = "choose-a-long-random-value"
```

The application accepts the verification request only when `hub.mode` is
`subscribe` and `hub.verify_token` matches that environment variable. It then
returns `hub.challenge` as plain text.

For POST event notifications, the application reads the original request bytes
and validates `X-Hub-Signature-256` with `META_APP_SECRET` before parsing JSON.
Unsigned or incorrectly signed requests receive `401 Unauthorized`.

After authentication, text message events are reduced to the message ID, sender
ID, receiving phone-number ID, timestamp, and text needed by the future inbox.
Delivery/read statuses and unsupported message types are acknowledged with
`200 OK` but produce no work.

Supported messages are inserted into `inbox_messages`. Its unique Meta message
ID constraint and PostgreSQL `ON CONFLICT DO NOTHING` make repeated webhook
deliveries idempotent.

## Worker

Run the worker in a separate terminal with the same `DATABASE_URL`:

```powershell
uv run python -m clinicflow.worker
```

Set `WORKER_REPLY_MODE=faq` to enable local FAQ and booking replies with Ollama
running. Run `uv run python -m clinicflow.seed_booking` with the migrated demo
database and its `WHATSAPP_PHONE_NUMBER_ID` to create the demo provider schedules.
Then try `Thursday evening Hydra facial book karna hai`. The worker asks for a
slot and name and reads the details back before confirmation. Reply buttons
and the exact text `confirm` act on the latest pending draft.

### Voice notes

`uv sync --extra voice` installs the local Whisper adapter. Run a local
benchmark to cache the selected model, then set `VOICE_TRANSCRIBER=whisper` and
`WHISPER_MODEL` to the same model name in the worker. The worker can also take
a path to existing converted weights.

```powershell
uv run --extra voice python -m clinicflow.asr_evals local-notes.json --model tiny --model small --download
```

Keep the manifest and recordings private. The manifest is a JSON list of
`{"audio": "note.ogg", "reference": "what was actually said"}` entries, with
paths relative to the manifest. The command reports word error rate, latency,
and failures for each model. Select a model using your own Urdu and Roman Urdu
recordings. A synthetic OGG/Opus smoke test has verified local decoding and
transcription. The booking flow reads back the proposed details and requires
a tap or typed confirmation.

### Staff inbox

Set `STAFF_DEV_TOKEN` to a random token of at least 32 characters and
`STAFF_DEV_TENANT_SLUG` to the demo clinic. Use a Bearer header with
`GET /staff/handoffs` and `GET /staff/handoffs/{id}`. Staff can post
`{"request_id": "<uuid>", "text": "A staff reply"}` to
`/staff/handoffs/{id}/reply` and resume the bot with
`POST /staff/handoffs/{id}/resume`. Each case includes recent conversation
context. `HANDOFF_PAUSE_SECONDS=0` keeps manual resume as the default.

For a Coexistence-enabled number, [Meta's staff message echo](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/smb_message_echoes/)
automatically pauses the bot. The dev inbox is the local fallback for test
numbers. Live Coexistence requires the business app onboarding flow and a
connected business number.

### Regression gate and quotas

Create a disposable `clinicflow_test` database in the local PostgreSQL
container. Point `CLINICFLOW_TEST_DATABASE_URL` to that database and run:

```powershell
uv run python -m clinicflow.regression
```

The command applies migrations, runs the workflow tests and scores 60 FAQ
cases. Pass `--provider ollama` for a slower model check. The FAQ CLI's
`--judge-style` option reports language and tone separately from factual
checks.

The worker counts messages per customer and tokens per tenant on the clinic's
local day. It reserves a request's worst-case token use during inference and
settles against observed usage afterward. On either limit it sends one notice
and sends the conversation to the staff inbox. Authorized staff can inspect
tool decisions and timing at `GET /staff/conversations/{id}/traces`.

Install the optional `observability` extra and set `SENTRY_DSN` to enable
exception reporting. The outbound error payload keeps exception types and code
locations and removes conversation data, request bodies, and error text.

### Onboard a second clinic

Use `examples/second-clinic.json` as the manifest for fictional Mehr Clinic.
The importer validates the timezone, public facts, provider service mappings,
and WhatsApp number ownership. Its settings include clinic tone and the
language used when a voice note cannot be read.

```powershell
uv run python -m clinicflow.onboarding examples/second-clinic.json --offline
```

To run the bundled two-clinic smoke test, migrate a dedicated local smoke
database, set `CLINICFLOW_TEST_DATABASE_URL` to it, and run:

```powershell
docker compose -f compose.whaloc.yaml up -d
uv run python -m clinicflow.whaloc_smoke
```

It submits signed Whaloc webhook fixtures to the real app, sends the resulting
replies back through Whaloc's Graph endpoint, and verifies distinct conversations
for the same fictional customer. The emulator binds only to loopback and
uses synthetic credentials.

For a real number, use the registered Meta phone-number ID in the manifest.
Set `CLINICFLOW_NEW_TENANT_TOKEN` for the import and keep
`CLINICFLOW_TOKEN_KEY` in your deployment secrets. Optionally set
`CLINICFLOW_NEW_STAFF_TOKEN` to issue a tenant-scoped staff Bearer token.
Run onboarding without `--offline` to encrypt and store the WhatsApp token.
Existing single-number deployments can continue to use the global
`WHATSAPP_ACCESS_TOKEN` when `WHATSAPP_PHONE_NUMBER_ID` identifies that number.
The token fallback applies only to that configured number.

The local acceptance checks cover the original and expanded FAQ suites,
booking confirmation and slot concurrency, voice transcription and readback,
staff handoff, usage caps, and two-clinic isolation. A live rollout needs a
worker that can reach the chosen model runtime, a registered Meta number for
each live clinic, and Urdu and Roman Urdu recordings for the voice benchmark.

The worker claims pending inbox rows with PostgreSQL `FOR UPDATE SKIP LOCKED`,
creates one durable `outbox_messages` row per inbound message, and marks the
inbox row processed in the same transaction. Multiple workers can therefore
poll safely without preparing the same reply twice. It then sends pending
outbox rows through Meta's versioned Graph API and records the returned
WhatsApp message ID before considering the row sent.

The worker requires `WHATSAPP_ACCESS_TOKEN`. `META_GRAPH_API_VERSION` defaults
to the version documented in `.env.example`; keeping it explicit makes API
upgrades deliberate.

Temporary failures that are known to be safe to retry use capped exponential
backoff. A timeout while reading Meta's response—or a worker restart during an
active send—has an ambiguous delivery result, so the row becomes `uncertain`
instead of being automatically resent and potentially producing a duplicate.
The retry limit, backoff, and stale-send window are configurable through the
worker variables in `.env.example`.

## Heroku deployment

Heroku uses the committed `uv.lock` and `.python-version` files to install the
locked production dependencies on Python 3.12. The `Procfile` declares three
processes:

- `release` applies Alembic migrations before a release becomes active.
- `web` serves FastAPI on Heroku's assigned `$PORT`.
- `worker` processes the durable inbox and outbox queues.

The deployment requires a Heroku Postgres attachment plus the Meta variables
shown in `.env.example`. Keep all real tokens in Heroku config vars, never in
the repository.

The Phase 0 development deployment is available at
[`clinicflow.awaisnazir.tech`](https://clinicflow.awaisnazir.tech/health). The
custom domain points to Heroku and uses an automatically managed HTTPS
certificate. The deployment uses an Eco web dyno and Essential-0 PostgreSQL.
The worker stays scaled to zero until the Meta access token and webhook
credentials are configured.

## Configuration

Copy `.env.example` to `.env` for local development. Never commit `.env` or
real Meta credentials.
