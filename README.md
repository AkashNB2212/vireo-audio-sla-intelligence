# Vireo Audio: first-response SLA breach report

A take-home project. It is a weekly first-response SLA breach report **by agent and shift** that also separates breaches an agent could have prevented from breaches that happened when **nobody was rostered** to answer. All numbers come from deterministic Python/pandas. An optional AI-written weekly briefing is checked against those numbers before it is shown.

> **Confidentiality.** The original task pack (ticket export, roster, customer and order data, and client-internal documents) is **intentionally not included** in this repository. It contains customer and employee personal data and client-internal material. Generated outputs and the client-facing deliverables (memo, detailed results) are excluded for the same reason. To reproduce the exact results, use the task pack supplied with the assignment (step 4 below).

## Setup and run

Needs Python 3.10+.

1. Clone the repository.

```bash
git clone <this-repository-url>
```
```bash
cd <repository-folder>
```

2. Create and activate a virtual environment.

```bash
python3 -m venv .venv
```
```bash
source .venv/bin/activate
```

3. Install the requirements. `requirements-lock.txt` holds exact, audited versions for Python 3.11; use it instead for an exact reproduction.

```bash
python -m pip install --upgrade pip setuptools
```
```bash
pip install -r requirements.txt
```

4. Copy the task-pack files supplied with the assignment into `data/`. The app needs `tickets.csv` and `agents.csv`; the other pack files are not read. See [data/README.md](data/README.md).

```bash
cp /path/to/task-pack/tickets.csv /path/to/task-pack/agents.csv data/
```

5. Run the tests. Without the task pack, the data-dependent tests are skipped with an explanation and the rest still run.

```bash
python -m pytest -q
```

6. Export the reports and the validation sample to `outputs/`. This folder is generated and not committed.

```bash
python scripts/export_report.py
```

7. Launch the app. It opens at http://localhost:8501 and is served on this machine only (`.streamlit/config.toml`), because it has no login.

```bash
streamlit run app.py
```

Without the task pack, the app and the export script stop with a message explaining which files to copy into `data/`.

## What's in the app

| Tab | What it shows |
|---|---|
| **Overview** | Quarter KPIs, the key finding and the modelled business goal (all computed from the pack at runtime), monthly breaches split by "nobody rostered" vs rostered hours, and the weekly briefing (AI-written or template). |
| **Weekly breach report** | Week, shift and agent filters. Eligible tickets, breaches, breach rate (hidden below 10 tickets), mean and median first-response time, share of the week's breaches, and breaches by attribution class. CSV downloads. |
| **Why breaches happen** | The attribution classes, a weekly stacked chart, breach rate by arrival hour, a shift × class table, and a ticket drill-down. |
| **Fair agent review** | Observed vs expected breaches, adjusted for channel mix, on tickets each agent owned from arrival to deadline, over an adjustable window, with transparent flags. |
| **Data checks** | The data-quality log and 17 reconciliation checks, run live. |

## Method

- **Timestamps:** the export is UTC, while shifts are defined in IST. Everything is converted to IST.
- **Breach:** first response minus creation is later than the channel's target. The targets and the credit per breach are configured in `vireo_sla/config.py`.
- **Week:** Monday to Sunday in IST, by creation time. Partial first and last weeks are labelled.
- **Agent, shift and site:** the resolving agent (the only agent field in the export), joined to the roster row in force on the ticket's IST creation date. Roster rows are effective-dated.
- **Attribution.** Every breach falls into the first class that applies:
  1. **Coverage gap:** nobody in the channel's frontline team was rostered at any point before the SLA deadline.
  2. **Shift-start backlog:** the ticket arrived with nobody rostered; the next shift started before the deadline but still missed it.
  3. **Hand-off / unclear:** transferred or re-teamed, or the resolver was off shift when the deadline passed or when the reply went out.
  4. **Agent-associated:** everything else. This is an *association*: the export does not record who replied first.
- **Guardrails:**
  - Weekly per-agent rates are hidden below 10 tickets.
  - Individual conversations use a 26-week, channel-mix-adjusted review.
  - Flags need at least 40 owned tickets and a gap of more than 2 standard deviations.
  - Tier 2 is never compared with Tier 1.
- **Business goal:** a modelled reduction in the breach rate and in SLA credits from restoring overnight rostering with existing staff. It is computed in the app from the pack. It is a projection, not realised savings.

Design decisions: [DECISIONS.md](DECISIONS.md). Validation: [VALIDATION.md](VALIDATION.md). Prompt and approach history: [PROMPT_EVOLUTION.md](PROMPT_EVOLUTION.md). Security: [SECURITY.md](SECURITY.md).

## Architecture

```
data/*.csv ──> vireo_sla/data.py        load, schema check, de-duplicate, UTC→IST, CSAT 0→blank,
                                        quarantine malformed rows
           ──> vireo_sla/pipeline.py    per ticket: SLA target, breach, roster row in force,
                                        arrival/deadline/response shift, staffing, attribution
           ──> vireo_sla/metrics.py     weekly agent/shift report, guardrails, fair review,
                                        credits, business goal                (pandas only)
           ──> vireo_sla/validation.py  17 reconciliation checks (independent recomputation)
           ──> vireo_sla/ai.py          fact sheet ──> LLM narrative ──> grounding check
                                                   └─> deterministic template (fallback)
           ──> vireo_sla/security.py    redaction, CSV formula neutralisation, label sanitising
app.py (Streamlit)    scripts/export_report.py    tests/ (pytest)
```

**Deterministic:** every count, rate, time, share, class, rupee figure and the business goal.
**AI-assisted:** only the wording of the weekly briefing. The model gets pre-formatted, verified facts; any number, date or agent name in its output that isn't in those facts, and any link, image, HTML or credential-like text, causes the output to be rejected.

## Validation

- **48 pytest tests.** 31 functional and 17 security regression tests. They cover schema, cleaning, timezone, SLA and shift boundaries, roster effective dates, each attribution class, reconciliation on the real export, the business-goal arithmetic, the AI grounding check (with a mocked LLM), caching of paid calls, and task-pack detection. With the pack present all 48 run; without it, the data-dependent ones are skipped.
- **17 live reconciliation checks.** Several recompute a figure by an independent route, for example breaches straight from the raw CSV, and coverage by a direct roster filter.
- **A manual stratified sample.** Details in [VALIDATION.md](VALIDATION.md).

## AI usage and cost

- **Build:** Claude Code (desktop app, using Claude Sonnet 5.5 and Claude Opus 5.5), for exploration, code, tests and document drafts. Every figure was checked against script output.
- **Runtime:** optional. Set `ANTHROPIC_API_KEY` (and `ANTHROPIC_WORKSPACE_ID` if the key is not workspace-scoped) to have the weekly briefing written by `claude-opus-5-5`.
  - Low effort; server-side refusal fallback enabled.
  - Override the model with `VIREO_LLM_MODEL`. Cap API requests per server run with `VIREO_LLM_MAX_CALLS` (default 60).
- **Cost estimate** (not measured; the app does not log token usage). One briefing is about 650–700 input tokens and about 250 output tokens plus the model's thinking:
  - input 700 × $4/M ≈ $0.003;
  - output 250–1,250 × $20/M ≈ $0.005–$0.025;
  - so about 1–3 US cents per briefing, and about $0.03–$0.12 a month at one briefing a week.
  - Briefings are cached per week. Without a key the cost is $0: a deterministic template is used instead.

## Known limitations

- The export records the **resolving** agent, not who replied first, so attribution is inferred from timestamps and the roster.
- The roster shows who was **scheduled**, not who was present.
- "Hand-off / unclear" can include tickets that arrived just before a shift ended with the deadline falling in an unrostered window, so the coverage share is slightly understated.
- The savings figure is modelled; the operational cost of moving an agent to nights is not modelled.
- Credits are implied (credit per breach × breaches); there is no ledger in the pack.
- The AI briefing's prose quality is not benchmarked.

## Repository map

```
app.py                      Streamlit app
vireo_sla/                  config, data, pipeline, metrics, validation, ai, security
tests/                      test_pipeline.py (functional), test_security.py (security), conftest.py
scripts/export_report.py    writes outputs/*.csv (generated; not committed)
data/README.md              where to put the task pack (the pack itself is not committed)
.streamlit/config.toml      local-only serving, no error details, no telemetry
requirements.txt            direct dependencies (major-version bounded)
requirements-lock.txt       exact audited versions (Python 3.11)
DECISIONS.md  VALIDATION.md  PROMPT_EVOLUTION.md  SECURITY.md
```
