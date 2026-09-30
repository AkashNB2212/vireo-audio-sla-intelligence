# Decisions log

Each entry records what was unclear, what I decided, and why. The supplied task pack and its support-policy parameters are the only authority.

*Public version: client-specific figures, names and quotations are omitted. The exact results are reproduced by running the project with the supplied task pack.*

## Data handling

| # | Question | Decision | Why |
|---|---|---|---|
| D1 | Timezone of the export | All timestamps are **UTC**. Add 5h30m to get IST. | Stated in the pack's documentation and the client's correspondence. Checked in the data: voice callback requests, which the IVR only accepts during set IST hours, fall inside that window only after conversion, in both source systems. |
| D2 | Duplicate tickets | Some ticket IDs appear twice, once per source system (a migration re-import). I keep the current-helpdesk copy. | The pack documentation describes the re-import. The pairs differ only in CSAT (legacy uses 0 for "no response"), so which copy is kept doesn't change any SLA figure. |
| D3 | CSAT = 0 on legacy rows | Treated as blank. | Per the pack documentation. |
| D4 | Eligible ticket | Every de-duplicated ticket with a parsable creation time and a non-negative first-response time, whatever its status. Rows with an unparseable creation time are quarantined and counted. | Every row has a first response, so breach status is known. The small number of zero-minute chat responses are spread across hours and systems (not an overnight bot pattern), so they are kept. |
| D5 | First human response | `first_response_at`, as exported. | The pack defines it as the first human reply. The data can't test that claim. |
| D6 | Breach | First response minus creation is **strictly greater** than the channel target. | A breach is defined as "later than the target". Priority doesn't change the target. The targets are in `vireo_sla/config.py`. |
| D7 | Reporting week | Monday-to-Sunday week of the **IST** creation time. The partial first and last weeks are labelled, and the app defaults to the latest complete week. | Shifts are defined in IST. |
| D8 | Agent | `agent_id`, which is the **resolving** agent. Names are shown; joins use the ID. | The export has no first-responder field, and the helpdesk's own reports attribute breaches to the resolver. |
| D9 | Shift and site of a ticket | From the resolving agent's roster row **in force on the ticket's IST creation date**. Every ticket matched exactly one row. | The roster is effective-dated because people change shift. Tests cover a shift-change boundary, including a ticket that is still the previous day in UTC. |
| D10 | Arrival window | The IST shift block containing the creation time. A Night block belongs to the date it starts on. | The shift definitions supplied with the pack. |
| D11 | Which team must be rostered for "covered" | The channel's frontline team: chat and social → Chat Frontline, email → Email Frontline, voice → Voice Frontline. | First contact per channel belongs to these teams, including overnight chat cover. |
| D12 | Rostered vs actually working | Only the roster is visible; leave and absence aren't. | This is a data limitation. "Coverage gap" is therefore a floor on the real effect, not an overstatement. |

## Attribution (the core analytical decision)

The obvious report (count breaches per resolving agent and shift) turned out to be misleading. A large share of breaches were tickets that arrived when the roster had nobody on for that channel. The shift that resolves them gets booked with the breaches.

Each breach goes into the first class that applies:

1. **Coverage gap:** nobody in the channel's frontline team was rostered at any point between creation and the SLA deadline. No agent could have met the target.
2. **Shift-start backlog:** the ticket arrived with nobody rostered; the next shift started before the deadline, but the reply was still late. This is the unattended queue landing when the next shift starts, not an individual problem.
3. **Hand-off / unclear:** the ticket arrived while the team was rostered, but (a) it was transferred or resolved by a team other than the first-routed one, or (b) the resolver was not on shift when the first reply went out, or (c) the resolver was not on shift when the deadline passed.
4. **Agent-associated:** none of the above applies. This is an *association*, not proof of cause.

Rule (c) was added after the manual validation sample (see VALIDATION.md).

## Guardrails

| # | Decision | Why |
|---|---|---|
| G1 | Weekly per-agent breach rates are hidden below **10 tickets** and shown as "low volume". Counts are always shown. | A typical agent-week has only a handful of tickets, so a weekly rate would be noise. |
| G2 | Individual conversations use the **Fair agent review**: a 26-week window by default, **at least 40** tickets the agent owned from arrival to deadline, and a flag only above **2 standard deviations** from the channel-mix-adjusted expectation. | The ticket selection doesn't depend on the outcome, so it doesn't bias the rates. The app also shows how many flags chance alone would produce. |
| G3 | Tier 2 is never flagged against Tier 1. | Tier 2 work is measured differently from Tier 1. |

## Business goal and money

| # | Decision | Why |
|---|---|---|
| B1 | The baseline is the latest full quarter in the export. | It is the most recent complete run-rate. |
| B2 | The counterfactual: tickets that arrive with nobody rostered breach at the rate observed for the same window when it *was* rostered, earlier in the export. Nothing else changes. | This uses the account's own history, not an external benchmark. It is conservative because it ignores CSAT and repeat-contact benefits. |
| B3 | The intervention is to roster **one existing agent** overnight. **No hire.** | Staffing constraints called for using existing staff, and overnight volume is small enough for one seat. |
| B4 | Money is the configured credit per breach × breaches. I report three separate figures: **exposure**, **credits due on resolved tickets**, and **modelled savings**. None of them is a booked loss. | There is no ledger in the pack. |
| B5 | The agent-hour cost is shown only as context. Moving an agent is not a new cost; the real cost is one fewer agent on the shift they leave, which a pilot must monitor. | |

## AI layer

| # | Decision | Why |
|---|---|---|
| A1 | The LLM writes only the weekly narrative, from a fact sheet that was already computed. It never computes. | Business numbers come from code. |
| A2 | Every number, date and agent name in the output must appear in the fact sheet. Links, images, HTML and credential-like text are rejected. On any violation, the deterministic template is shown instead. | It makes the rules enforceable rather than a request in the prompt. |
| A3 | With no API key, the deterministic template is used and the app runs fully. | It must run from the README without paid credits. |
| A4 | I did not build free-text classification of customer messages or agent notes. | The question is *when* tickets arrive and *who is rostered*, which timestamps answer. The bot-set category field covers ticket mix. |
| A5 | Model: `claude-opus-5-5` at low effort, overridable with `VIREO_LLM_MODEL`, with the server-side refusal fallback enabled. | The call is small: an estimated 650–700 input and about 250 output tokens plus thinking, about 1–3 US cents per briefing (estimate; token usage is not logged). It is cached per week. |
| A6 | Security controls are in SECURITY.md. | The app has no login and an optional paid API, so the controls target exposure, key safety and cost. |

## Out of scope (deliberately)

- Refund, replacement, lot-quality and repeat-contact analyses: not what the client asked.
- A staffing optimiser or queue simulation: a one-seat pilot measured by this tool is simpler and more convincing.
- Resolution-time SLAs: no target is defined for them.
- Coaching insights mined from note text (see A4).
