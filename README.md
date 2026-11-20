# ClinicFlow WA

ClinicFlow WA is a multi-tenant WhatsApp assistant for clinics. The project will
be built incrementally, beginning with a reliable webhook-to-worker pipeline and
growing into grounded FAQ answers, booking, voice notes, human handoff, and
multi-clinic isolation.

## Current milestone

Phase 0: receive a WhatsApp webhook, persist it safely, process it in a worker,
and send an echo response through the WhatsApp Cloud API.

The current checkpoint contains repository metadata and the initial Python
package only. See [the Phase 0 plan](docs/phase-0.md) for the small-step build
sequence.

## Technology direction

- Python 3.12
- FastAPI
- PostgreSQL and Alembic
- A separate web process and worker process
- Heroku for the first deployment

Dependencies will be added when the first executable slice is introduced so
that each choice is explained and testable.

## Configuration

Copy `.env.example` to `.env` for local development. Never commit `.env` or
real Meta credentials.
