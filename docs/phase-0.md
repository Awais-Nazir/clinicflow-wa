# Phase 0: Skeleton on Heroku

## Goal

A message sent to the Meta test number travels through the real pipeline:

```text
WhatsApp webhook -> inbox row -> worker -> outbound message -> echo reply
```

## Incremental checkpoints

1. ✅ **Repository skeleton** — initialize Git, document configuration, and
   create the Python package.
2. ✅ **Local HTTP slice** — add FastAPI, a health endpoint, and automated
   tests.
3. ✅ **Webhook verification** — implement and test Meta's GET verification
   handshake.
4. ✅ **Authenticated ingestion**
   - ✅ **Signature verification** — authenticate POST requests using the raw
     body and `X-Hub-Signature-256`.
   - ✅ **Event classification** — safely ignore status and unsupported events.
5. ✅ **Durable inbox** — add PostgreSQL, SQLAlchemy, Alembic, and unique
   inbound message IDs.
6. 🚧 **Worker and outbox**
   - ✅ **Prepare outbound intent** — claim inbox rows with `SKIP LOCKED` and
     atomically record one durable echo per inbound message.
   - ✅ **Send outbound intent** — call the Graph API and record the resulting
     WhatsApp message ID, while preserving rejected messages for inspection.
   - ✅ **Recover safely** — retry safe failures with capped backoff and
     quarantine ambiguous sends rather than risk a duplicate reply.
7. **Meta connection** — configure the test number, webhook subscription, and
   credentials.
8. 🚧 **Heroku deployment**
   - ✅ **Process definitions** — pin Python 3.12 and define release, web, and
     worker commands for Heroku's native uv build.
   - ✅ **Provision and deploy** — deploy the Eco web process, provision
     Essential-0 Postgres, apply migrations, and verify the public health
     endpoint at `https://clinicflow.awaisnazir.tech/health`.
   - **Activate worker** — configure Meta secrets, scale the Eco worker to one,
     and verify an outbound send.
9. **Acceptance checks** — exercise duplicate payloads, bad signatures,
   restarts, and delivered/read status events.

Each checkpoint should leave the repository runnable or verifiable before the
next one begins.
