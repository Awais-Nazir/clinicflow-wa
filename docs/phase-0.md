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
6. ✅ **Worker and outbox**
   - ✅ **Prepare outbound intent** — claim inbox rows with `SKIP LOCKED` and
     atomically record one durable echo per inbound message.
   - ✅ **Send outbound intent** — call the Graph API and record the resulting
     WhatsApp message ID, while preserving rejected messages for inspection.
   - ✅ **Recover safely** — retry safe failures with capped backoff and
     quarantine ambiguous sends rather than risk a duplicate reply.
7. ✅ **Meta connection** — configured the test number, webhook subscription,
   and credentials.
8. ✅ **Heroku deployment**
   - ✅ **Process definitions** — pin Python 3.12 and define release, web, and
     worker commands for Heroku's native uv build.
   - ✅ **Provision and deploy** — deploy the Eco web process, provision
     Essential-0 Postgres, apply migrations, and verify the public health
     endpoint at `https://clinicflow.awaisnazir.tech/health`.
   - ✅ **Activate worker** — configured Meta secrets, temporarily scaled the
     Eco worker to one, and verified the live echo. The worker can remain at
     zero between development sessions to conserve Eco hours.
9. ✅ **Acceptance checks** — verified duplicate payloads produce one outbound
   intent, bad signatures return 401, committed work survives a worker restart,
   and delivered/read status events are ignored safely.

Each checkpoint should leave the repository runnable or verifiable before the
next one begins.
