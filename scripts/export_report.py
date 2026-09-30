"""Write the report CSVs and a stratified validation sample to outputs/.

Run: python scripts/export_report.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import warnings  # noqa: E402

import pandas as pd  # noqa: E402

from vireo_sla import metrics as M  # noqa: E402
from vireo_sla.data import PACK_MISSING_MESSAGE, missing_pack_files  # noqa: E402
from vireo_sla.pipeline import NO_BREACH, BREACH_CLASSES, build  # noqa: E402
from vireo_sla.security import csv_safe  # noqa: E402
from vireo_sla.validation import run_checks  # noqa: E402

if missing_pack_files():
    sys.exit(PACK_MISSING_MESSAGE)

OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)

f, agents, dq = build()
csv_safe(M.weekly_agent_report(f)).to_csv(OUT / "weekly_agent_report.csv", index=False)
csv_safe(M.weekly_shift_report(f)).to_csv(OUT / "weekly_shift_report.csv", index=False)
weeks = sorted(f["week"].unique())[:-1]  # last week is partial
csv_safe(M.agent_review(f[f["week"].isin(weeks[-26:])])).to_csv(OUT / "agent_review_26_weeks.csv", index=False)
csv_safe(M.monthly_credits(f)).to_csv(OUT / "monthly_credits.csv", index=False)
checks = run_checks(f, agents)
csv_safe(checks).to_csv(OUT / "reconciliation_checks.csv", index=False)

# Stratified sample for manual checking against the raw CSV (8 per class, fixed seed).
raw = pd.read_csv(ROOT / "data" / "tickets.csv").drop_duplicates("ticket_id")
cols = ["ticket_id", "channel", "created_at", "first_response_at", "agent_id", "transfers", "assigned_team"]
with warnings.catch_warnings():  # pandas FutureWarning about apply on grouping columns; result unchanged
    warnings.simplefilter("ignore", FutureWarning)
    sample = (f[f["attribution"].isin(BREACH_CLASSES + [NO_BREACH])]
              .groupby("attribution", group_keys=False).apply(lambda g: g.sample(8, random_state=7)))
sample = sample[["ticket_id", "created_ist", "first_response_ist", "frt_min", "sla_target_min", "breach",
                 "agent_name", "agent_team", "agent_shift", "arrival_window", "response_window",
                 "frontline_staffed_at_arrival", "staffed_min_before_deadline", "handoff", "attribution"]]
sample = sample.merge(raw[cols].add_prefix("raw_").rename(columns={"raw_ticket_id": "ticket_id"}), on="ticket_id")
sample["manual_check"] = ""   # filled in by a human reviewer; see VALIDATION.md
csv_safe(sample).to_csv(OUT / "validation_sample.csv", index=False)

g = M.business_goal(f)
print(f"tickets={len(f)} breaches={int(f['breach'].sum())} checks_passed={checks['passed'].sum()}/{len(checks)}")
print(f"goal: {g['quarter']} {g['baseline_rate']:.1%} -> {g['target_rate']:.1%}, "
      f"~{g['breaches_avoided']:.0f} breaches, Rs {g['credits_avoided_inr']:,.0f}/quarter")
print(f"wrote {sorted(p.name for p in OUT.iterdir())}")
