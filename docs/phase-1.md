# Phase 1: FAQ Agent for One Clinic

## Goal

A fake clinic answers grounded questions in the customer's language and says it
does not know when the clinic data contains no answer.

## Decision

Phase 1 uses **Noor Clinic (Demo)**, a fictional clinic. This lets us design and
test the system without copying real clinic claims or accidentally publishing
incorrect medical, pricing, or schedule information.

## Embedding strategy

Development uses the zero-cost local `HashingEmbedder` baseline. Before the
production-quality evaluation, we will replace it with OpenAI
`text-embedding-3-small`, re-embed the knowledge chunks, and tune the hybrid
retrieval threshold against the eval set. The embedding interface remains
provider-independent so this change does not affect tenant or retrieval logic.

## Incremental checkpoints

1. ✅ **Tenant foundation** — map a unique Meta `phone_number_id` to one
   clinic and provide an idempotent demo-clinic seed.
2. ✅ **Structured clinic facts**
   - ✅ **Profile and services** — add tenant-scoped contact details,
     services, prices, and durations.
   - ✅ **Hours and doctors** — add weekly opening hours and doctor
     information.
3. ✅ **Conversation storage**
   - ✅ **Storage primitives** — persist idempotent, tenant-scoped
     conversations plus inbound and outbound messages.
   - ✅ **Pipeline connection** — attach authenticated inbound messages to
     the correct tenant and conversation; safely ignore unknown numbers.
4. ✅ **Knowledge base**
   - ✅ **Chunks and lexical search** — store seeded FAQ/policy chunks
     and retrieve them with tenant-filtered PostgreSQL full-text search.
   - ✅ **Hybrid retrieval** — add swappable embeddings and combine exact
     cosine similarity with lexical ranking using Reciprocal Rank Fusion.
5. ✅ **Grounded tools and agent**
   - ✅ **Tenant-bound tools** — expose `get_services`, `get_clinic_info`,
     and `search_kb`; application code injects the tenant ID and model-supplied
     tenant overrides are rejected.
   - ✅ **Agent orchestration** — use a provider-independent, bounded tool loop
     with a free scripted model in tests and a same-language deterministic
     fallback when no grounding evidence is available.
   - ✅ **Run traces** — persist tenant-scoped tool calls, tokens, latency,
     model, prompt version, fallback status, and exact USD cost for each run.
6. ✅ **Numeric grounding check** — normalize ASCII and Urdu/Arabic digits,
   then reject replies containing any price or number absent from tool results;
   persist the rejection reason and unsupported values in the run trace.
7. **Evaluation runner** — add about 25 answerable, unanswerable, and multilingual
   cases before expanding the agent.

Each checkpoint stays small enough to inspect, test, and understand before we
move to the next one.
