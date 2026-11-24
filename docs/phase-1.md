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
7. ✅ **Evaluation runner** — keep 25 versioned answerable, unanswerable, and
   multilingual cases, run them through the real tools and guardrails, print
   scores by category, and fail the command when a regression is detected. The
   current deterministic fixture is a zero-cost pipeline baseline, not a claim
   about real-model answer quality.
8. **Free development model and WhatsApp integration**
   - ✅ **Ollama adapter** — call a configurable local model through Ollama's
     chat and tool-calling API, with zero token cost and no external API key.
   - ✅ **Real-model baseline** — `qwen3:4b` scores 9/25 before tuning. The
     deterministic unanswerable path passes 4/4; most failures come from the
     small model skipping the required clinic tool and being safely rejected.
   - ✅ **Tool-routing tune** — clarify when to call each tool and use the
     localized fallback when the tool output lacks the answer. With the tuned
     prompt, the pinned Qwen3-4B-Instruct model scores 12/25
     (clinic info 5/6, services 1/6, policy 3/6, unanswerable 3/4,
     multilingual 0/3). Llama 3.2 scored up to 10/25 in exploratory runs.
   - ✅ **Grounding and eval follow-up** — render named prices from the
     catalogue and scope recognized service and doctor lookups to the requested
     record. Compare whole times, equivalent number formats, Urdu-script
     replies, and documented policy paraphrases. The selected local model
     passed all 25 revised cases.
   - ✅ **Local worker connection** — in FAQ mode, save the trace and the
     outbound intent together. Verify signed webhook ingestion, real Ollama
     inference, and one simulated send on a fresh database. Live Meta delivery
     for FAQ mode requires a deployment with access to a configured model.

Each checkpoint stays small enough to inspect, test, and understand before we
move to the next one.
