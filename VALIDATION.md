# How we know the output is right, and how often it isn't

*Public version: ticket IDs and client-specific figures are omitted. Every check below runs against the supplied task pack.*

## 1. Automated tests (`pytest`): 48 tests

There are 31 functional and 17 security tests. Tests that need the task pack are skipped, with an explanation, when it isn't present in `data/`.

| Area | What is tested |
|---|---|
| Schema | A missing required column raises `SchemaError`. |
| Cleaning | Duplicates keep the helpdesk copy. Legacy CSAT 0 becomes blank. UTC→IST conversion. Negative durations are counted. Malformed timestamps are quarantined. |
| SLA mapping and breach | Every channel at exactly its target (not a breach) and one minute over (breach). |
| Shift blocks | 05:59 / 06:00 / 14:00 boundaries. A 02:00 ticket belongs to the Night block that started the previous day. |
| Roster | Effective-dated rows apply. The IST date is used, not the UTC date. |
| Attribution | A hand-built ticket for each class, with hand-computed expected values. |
| Real-export reconciliation | All 17 live checks. Per-week volume and breach conservation. Agent shares sum to 1. The low-volume guardrail. The business-goal arithmetic recomputed independently. |
| AI layer | The template passes its own grounding check. Invented numbers, dates and agent names are caught. No key falls back to the template. A mocked client: grounded output is accepted, ungrounded output is rejected, and HTTP 400 triggers a retry with a plainer request. Paid calls aren't repeated for a cached briefing. |
| Security | See SECURITY.md. |

## 2. Live reconciliation checks: 17

These run every time the app loads (the **Data checks** tab) and in `scripts/export_report.py`. Several recompute a figure by a **second, independent route**:

- The breach count is recomputed straight from the raw CSV, without the pipeline.
- "Nobody rostered at arrival" is recomputed with a direct roster filter instead of the pipeline's staffing logic.
- The weekly agent report conserves tickets and breaches exactly. Attribution classes sum to breaches in every agent-week. The shift roll-up equals the agent roll-up. Each agent-week sits in exactly one shift.
- Credits equal breaches × the configured credit. "Due" plus "pending" equals the total. The business-goal baseline reconciles to the quarter's tickets. Modelled savings equal avoided breaches × the credit.

## 3. Manual check of a stratified sample (not circular)

`scripts/export_report.py` generates `outputs/validation_sample.csv`: 8 tickets per class (fixed seed), next to the raw UTC values. For each row, the checker converted UTC to IST, recomputed the duration, looked up the SLA target and the roster row, and decided the class from the written rules, all without running the pipeline code.

**Who did the check:** the AI coding assistant, during the build, reading the raw rows directly. It is independent of the code, but it is not a human review. The CSV has an empty `manual_check` column so a human can repeat it.

**Round 1 (first version of the rules):** conversions, durations, targets, roster rows, breach flags and classes all matched the written rules (40 of 40). But **1 of 8 "Agent-associated" rows was judged unfair**. It was an email whose deadline passed during a *rostered* Night shift, charged to the Day agent who closed it the next day. **Fix:** a new condition that the resolver was on shift when the deadline passed. The affected breaches moved to "Hand-off / unclear".

**Round 2 (current rules, regenerated sample):** Agent-associated was 8 of 8 fair. Hand-off / unclear was 8 of 8 per the rules. 2 of those 8 were emails that arrived just before the Day shift ended, with the deadline in an unrostered window: arguably coverage problems. So that class slightly *understates* coverage, which is the safe direction.

**Error-rate statement:** the mechanical calculations had **0 errors in 56 row checks** (40 in round 1 + 16 re-examined in round 2). Attribution is a rule, not ground truth. About 1 in 8 agent-associated rows was wrongly charged before the fix, and 0 in 8 after. That sample is small, so the true rate could still be a few percent, which is why the tool never ranks agents on weekly counts.

## Stability of agent-level flags

The Fair agent review's flags vary with the window: a borderline flag in one week can disappear the next. With about 30 agents compared, roughly 0.7 flags are expected from chance alone, and the app says so next to the flags.

## What is and isn't guaranteed

| Guaranteed (tested, reconciled) | Not guaranteed |
|---|---|
| Every count, rate, share and rupee figure is reproducible from the task pack and the configured parameters. | That `first_response_at` truly is a *human* reply. |
| Weekly figures sum exactly to the ticket-level data. | Who actually sent the first reply (the export has only the resolver). |
| The roster in force on each ticket's date is applied. | That rostered agents were actually working. |
| LLM text contains no number, date, agent name, link or HTML that isn't allowed by the verified facts. | The quality of the LLM's prose beyond the grounding check; it has not been benchmarked. |
| | The modelled savings. |
