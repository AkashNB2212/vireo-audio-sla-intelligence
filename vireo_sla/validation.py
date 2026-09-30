"""Reconciliation checks. Each check recomputes a figure by a second, independent route
(raw CSV, direct roster filtering) and compares it with the pipeline output."""
from __future__ import annotations

import pandas as pd

from .config import BREACH_CREDIT_INR, CHANNEL_FRONTLINE, DATA_DIR, SLA_TARGET_MIN
from . import metrics as M
from .pipeline import BREACH_CLASSES, COVERAGE_GAP, NO_BREACH, SHIFT_START_BACKLOG


def _check(name, passed, detail=""):
    return {"check": name, "passed": bool(passed), "detail": str(detail)}


def independent_breach_count(data_dir=DATA_DIR) -> tuple[int, int]:
    """Recompute (tickets, breaches) straight from the raw CSV, without the pipeline."""
    raw = pd.read_csv(data_dir / "tickets.csv")
    raw = raw.drop_duplicates("ticket_id")          # which copy is kept does not affect timestamps
    mins = (pd.to_datetime(raw["first_response_at"]) - pd.to_datetime(raw["created_at"])).dt.total_seconds() / 60
    return len(raw), int((mins > raw["channel"].map(SLA_TARGET_MIN)).sum())


def independent_unstaffed(f: pd.DataFrame, agents: pd.DataFrame) -> pd.Series:
    """Was the channel's frontline team rostered for the arrival window? Direct roster filter."""
    hour = f["created_ist"].dt.hour
    shift = pd.Series("Day", index=f.index)
    shift[(hour >= 6) & (hour < 14)] = "Morning"
    shift[(hour >= 22) | (hour < 6)] = "Night"
    block_date = f["created_ist"].dt.normalize() - pd.to_timedelta((hour < 6).astype(int), unit="D")
    team = f["channel"].map(CHANNEL_FRONTLINE)
    keys = pd.DataFrame({"team": team, "shift": shift, "d": block_date})
    uniq = keys.drop_duplicates()
    empty = {}
    for tm, sh, d in uniq.itertuples(index=False):
        a = agents[(agents["team"] == tm) & (agents["shift"] == sh) & (agents["from_date"] <= d)
                   & (agents["to_date"].isna() | (agents["to_date"] >= d))]
        empty[(tm, sh, d)] = a.empty
    return pd.Series([empty[k] for k in keys.itertuples(index=False, name=None)], index=f.index)


def run_checks(f: pd.DataFrame, agents: pd.DataFrame | None = None, data_dir=DATA_DIR) -> pd.DataFrame:
    rows = []
    n, b = len(f), int(f["breach"].sum())

    rows.append(_check("Ticket IDs unique after de-duplication", f["ticket_id"].is_unique))
    rows.append(_check("Every ticket parsed a creation and first-response time",
                       f["created_at"].notna().all() and f["first_response_at"].notna().all()))
    rows.append(_check("No negative first-response durations", (f["frt_min"] >= 0).all(),
                       f"min = {f['frt_min'].min()}"))
    rows.append(_check("Every channel mapped to an SLA target", f["sla_target_min"].notna().all()))
    rows.append(_check("Every ticket matched exactly one roster assignment", f["roster_matched"].all()))

    raw_n, raw_b = independent_breach_count(data_dir)
    rows.append(_check("Breach count matches independent recomputation from raw CSV",
                       (raw_n, raw_b) == (n, b), f"pipeline {n}/{b} vs raw {raw_n}/{raw_b}"))

    wk = M.weekly_agent_report(f)
    rows.append(_check("Weekly agent report conserves ticket volume", wk["eligible_tickets"].sum() == n,
                       f"{wk['eligible_tickets'].sum()} vs {n}"))
    rows.append(_check("Weekly agent report conserves breaches", wk["breaches"].sum() == b,
                       f"{wk['breaches'].sum()} vs {b}"))
    rows.append(_check("Attribution classes sum to breaches in every agent-week",
                       (wk[BREACH_CLASSES].sum(axis=1) == wk["breaches"]).all()))
    sh = M.weekly_shift_report(f)
    rows.append(_check("Shift report equals agent report rolled up by shift",
                       sh.groupby("agent_shift")["breaches"].sum().equals(
                           wk.groupby("agent_shift")["breaches"].sum())))
    rows.append(_check("Each agent-week sits in one shift (agent-to-shift reconciliation)",
                       not wk.duplicated(["week", "agent_id"]).any()))
    rows.append(_check("Breach flag and attribution agree",
                       ((f["attribution"] != NO_BREACH) == f["breach"]).all()))

    exp = M.credit_exposure(f)
    rows.append(_check("Credit exposure = breaches x credit per breach",
                       exp["total_exposure_inr"] == b * BREACH_CREDIT_INR
                       and exp["credits_due_on_resolved_inr"] + exp["credits_pending_open_inr"] == exp["total_exposure_inr"],
                       f"Rs {exp['total_exposure_inr']:,}"))
    g = M.business_goal(f)
    q = f[f["quarter"] == g["quarter"]]
    rows.append(_check("Business-goal baseline reconciles to quarter tickets",
                       g["breaches"] == int(q["breach"].sum()) and g["eligible_tickets"] == len(q)))
    rows.append(_check("Modelled savings = avoided breaches x credit per breach",
                       abs(g["credits_avoided_inr"] - g["breaches_avoided"] * BREACH_CREDIT_INR) < 1e-6))

    if agents is not None:
        ind = independent_unstaffed(f, agents)
        rows.append(_check("'Nobody rostered at arrival' matches a direct roster filter",
                           ind.equals(f["arrived_unstaffed"]),
                           f"{int((ind != f['arrived_unstaffed']).sum())} mismatches"))
    structural = f["attribution"].isin([COVERAGE_GAP, SHIFT_START_BACKLOG])
    rows.append(_check("Structural classes only on tickets that arrived with nobody rostered",
                       f.loc[structural, "arrived_unstaffed"].all()))
    return pd.DataFrame(rows)
