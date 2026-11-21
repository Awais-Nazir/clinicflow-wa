# ClinicFlow WA

ClinicFlow WA is a multi-tenant WhatsApp assistant for clinics. The project will
be built incrementally, beginning with a reliable webhook-to-worker pipeline and
growing into grounded FAQ answers, booking, voice notes, human handoff, and
multi-clinic isolation.

## Current milestone

Phase 0: receive a WhatsApp webhook, persist it safely, process it in a worker,
and send an echo response through the WhatsApp Cloud API.

The current checkpoint contains a local FastAPI application with a tested
health endpoint. See [the Phase 0 plan](docs/phase-0.md) for the small-step
build sequence.

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

## Configuration

Copy `.env.example` to `.env` for local development. Never commit `.env` or
real Meta credentials.
