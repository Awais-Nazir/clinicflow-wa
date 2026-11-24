# ClinicFlow WA

ClinicFlow WA is a multi-tenant WhatsApp assistant for clinics. The project will
be built incrementally, beginning with a reliable webhook-to-worker pipeline and
growing into grounded FAQ answers, booking, voice notes, human handoff, and
multi-clinic isolation.

## Current milestone

Phase 2 adds booking for the fictional Noor Clinic. The local FAQ worker and
25-case evaluation suite build on Phase 0's WhatsApp delivery pipeline.

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
