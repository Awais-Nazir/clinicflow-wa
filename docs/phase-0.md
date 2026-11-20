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
3. **Webhook verification** — implement and test Meta's GET verification
   handshake.
4. **Authenticated ingestion** — verify POST signatures using the raw request
   body and safely ignore unsupported events.
5. **Durable inbox** — add PostgreSQL, SQLAlchemy, Alembic, and unique inbound
   message IDs.
6. **Worker and outbox** — claim work safely, record outbound intent, send an
   echo, and recover from restarts.
7. **Meta connection** — configure the test number, webhook subscription, and
   credentials.
8. **Heroku deployment** — add the web and worker process definitions, database,
   migrations, and config vars.
9. **Acceptance checks** — exercise duplicate payloads, bad signatures,
   restarts, and delivered/read status events.

Each checkpoint should leave the repository runnable or verifiable before the
next one begins.
