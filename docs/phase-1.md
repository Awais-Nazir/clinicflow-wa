# Phase 1: FAQ Agent for One Clinic

## Goal

A fake clinic answers grounded questions in the customer's language and says it
does not know when the clinic data contains no answer.

## Decision

Phase 1 uses **Noor Clinic (Demo)**, a fictional clinic. This lets us design and
test the system without copying real clinic claims or accidentally publishing
incorrect medical, pricing, or schedule information.

## Incremental checkpoints

1. ✅ **Tenant foundation** — map a unique Meta `phone_number_id` to one
   clinic and provide an idempotent demo-clinic seed.
2. ✅ **Structured clinic facts**
   - ✅ **Profile and services** — add tenant-scoped contact details,
     services, prices, and durations.
   - ✅ **Hours and doctors** — add weekly opening hours and doctor
     information.
3. 🚧 **Conversation storage**
   - ✅ **Storage primitives** — persist idempotent, tenant-scoped
     conversations plus inbound and outbound messages.
   - **Pipeline connection** — attach authenticated inbound messages to the
     correct tenant and conversation.
4. **Knowledge base** — store policy and FAQ chunks and add tenant-isolated
   hybrid retrieval.
5. **Grounded tools and agent** — implement `get_services`, `get_clinic_info`,
   and `search_kb`; application code injects the tenant ID.
6. **Numeric grounding check** — reject replies containing prices or numbers
   absent from tool results.
7. **Evaluation runner** — add about 25 answerable, unanswerable, and multilingual
   cases before expanding the agent.

Each checkpoint stays small enough to inspect, test, and understand before we
move to the next one.
