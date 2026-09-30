"""Aggregations, guardrails, financials and the business goal. Deterministic pandas only."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import (AGENT_HOUR_COST_INR, ATTENDED_STATUSES, BREACH_CREDIT_INR,
                     MIN_TICKETS_FOR_RATE, MIN_TICKETS_FOR_REVIEW, REVIEW_Z, SHIFT_HOURS)
from .pipeline import AGENT, BREACH_CLASSES, COVERAGE_GAP, HANDOFF, SHIFT_START_BACKLOG


def _summary(g: pd.core.groupby.DataFrameGroupBy) -> pd.DataFrame:
    out = g.agg(
        eligible_tickets=("eligible", "sum"),
        breaches=("breach", "sum"),
        mean_frt_min=("frt_min", "mean"),
        median_frt_min=("frt_min", "median"),
        **{c: ("attribution", lambda s, c=c: int((s == c).sum())) for c in BREACH_CLASSES},
    )
    out["breach_rate"] = out["breaches"] / out["eligible_tickets"]
    return out


def weekly_agent_report(f: pd.DataFrame) -> pd.DataFrame:
    """One row per week x resolver shift x site x agent: what the client asked for, plus attribution."""
    keys = ["week", "agent_shift", "agent_site", "agent_team", "agent_id", "agent_name"]
    r = _summary(f.groupby(keys, dropna=False)).reset_index()
    week_breaches = r.groupby("week")["breaches"].transform("sum")
    r["share_of_week_breaches"] = np.where(week_breaches > 0, r["breaches"] / week_breaches, 0.0)
    r["low_volume"] = r["eligible_tickets"] < MIN_TICKETS_FOR_RATE
    r["breach_rate_display"] = np.where(r["low_volume"], np.nan, r["breach_rate"])
    r["structural_share"] = np.where(
        r["breaches"] > 0, (r[COVERAGE_GAP] + r[SHIFT_START_BACKLOG]) / r["breaches"], 0.0)
    for c in ["mean_frt_min", "median_frt_min"]:
        r[c] = r[c].round(1)
    return r.sort_values(["week", "agent_shift", "breaches"], ascending=[True, True, False])


def weekly_shift_report(f: pd.DataFrame, by: str = "agent_shift") -> pd.DataFrame:
    """Weekly totals per shift. by='agent_shift' (who resolved) or 'arrival_window' (when it arrived)."""
    r = _summary(f.groupby(["week", by])).reset_index()
    return r


def weekly_totals(f: pd.DataFrame) -> pd.DataFrame:
    r = _summary(f.groupby("week")).reset_index()
    r["structural_breaches"] = r[COVERAGE_GAP] + r[SHIFT_START_BACKLOG]
    r["non_structural_breach_rate"] = (r["breaches"] - r["structural_breaches"]) / r["eligible_tickets"]
    return r


def agent_review(f: pd.DataFrame) -> pd.DataFrame:
    """Fair agent comparison over a window (use >= 13 weeks).

    Only tickets the agent plausibly owned from start to SLA deadline are used: arrived while
    the frontline team was rostered, no hand-off, and both arrival and SLA deadline fell inside
    the resolving agent's shift. These conditions do not depend on the outcome (unlike "was the
    reply sent on shift", which is correlated with lateness), so the selection does not bias rates.
    Expected breaches = sum over the agent's tickets of the pooled breach rate for that channel
    in the same window, so an agent is not penalised for channel mix. Tier 2 is excluded from
    flags (Tier 2 is not compared with Tier 1 on volume metrics).
    """
    base = f[~f["arrived_unstaffed"] & ~f["handoff"] & (f["arrival_window"] == f["agent_shift"])
             & f["resolver_on_shift_at_deadline"]].copy()
    if base.empty:
        return pd.DataFrame()
    base["p"] = base.groupby("channel")["breach"].transform("mean")
    g = base.groupby(["agent_id", "agent_name", "agent_team", "agent_shift", "agent_site", "agent_tier"])
    r = g.agg(attributable_tickets=("breach", "size"), breaches=("breach", "sum"),
              expected=("p", "sum"), var=("p", lambda s: float((s * (1 - s)).sum()))).reset_index()
    r["breach_rate"] = r["breaches"] / r["attributable_tickets"]
    r["expected_rate"] = r["expected"] / r["attributable_tickets"]
    r["z"] = np.where(r["var"] > 0, (r["breaches"] - r["expected"]) / np.sqrt(r["var"]), 0.0)
    r["flag"] = np.select(
        [r["agent_tier"] == 2, r["attributable_tickets"] < MIN_TICKETS_FOR_REVIEW,
         r["z"] > REVIEW_Z, r["z"] < -REVIEW_Z],
        ["Tier 2 - not compared", "Too few tickets", "Above expected - discuss", "Below expected"],
        default="Within normal range",
    )
    r["expected"] = r["expected"].round(1)
    r["z"] = r["z"].round(2)
    return r.drop(columns="var").sort_values("z", ascending=False)


def headline(f: pd.DataFrame) -> dict:
    n = int(f["eligible"].sum())
    b = int(f["breach"].sum())
    counts = f["attribution"].value_counts()
    return {
        "eligible_tickets": n,
        "breaches": b,
        "breach_rate": b / n if n else 0.0,
        **{c: int(counts.get(c, 0)) for c in BREACH_CLASSES},
        "structural_breaches": int(counts.get(COVERAGE_GAP, 0) + counts.get(SHIFT_START_BACKLOG, 0)),
        "agent_associated_rate": counts.get(AGENT, 0) / n if n else 0.0,
    }


def credit_exposure(f: pd.DataFrame) -> dict:
    """Credit per breach (BREACH_CREDIT_INR), issued on resolution. We have no ledger, so these are
    policy-implied amounts, not booked P&L."""
    br = f[f["breach"]]
    attended = br["status"].isin(ATTENDED_STATUSES)
    return {
        "breaches": len(br),
        "credits_due_on_resolved_inr": int(attended.sum()) * BREACH_CREDIT_INR,
        "credits_pending_open_inr": int((~attended).sum()) * BREACH_CREDIT_INR,
        "total_exposure_inr": len(br) * BREACH_CREDIT_INR,
        "structural_exposure_inr": int(br["structural_breach"].sum()) * BREACH_CREDIT_INR,
    }


def monthly_credits(f: pd.DataFrame) -> pd.DataFrame:
    r = f.groupby("month").agg(tickets=("eligible", "sum"), breaches=("breach", "sum"),
                               structural=("structural_breach", "sum")).reset_index()
    r["breach_rate"] = r["breaches"] / r["tickets"]
    r["credit_exposure_inr"] = r["breaches"] * BREACH_CREDIT_INR
    return r


def business_goal(f: pd.DataFrame, quarter: str | None = None,
                  channels: tuple[str, ...] = ("chat", "social", "email")) -> dict:
    """Modelled effect of restoring a rostered Night shift for the given channels.

    Baseline: the latest full quarter. Counterfactual: tickets in `channels` that arrived with
    no frontline agent rostered would breach at the rate observed for Night-window arrivals
    in those channels when the Night shift WAS rostered. Everything else unchanged.
    """
    quarter = quarter or sorted(f["quarter"].unique())[-1]
    q = f[f["quarter"] == quarter]
    in_scope = f["channel"].isin(channels)
    ref = f[in_scope & (f["arrival_window"] == "Night") & ~f["arrived_unstaffed"]]
    ref_rate = float(ref["breach"].mean())

    n, b = int(q["eligible"].sum()), int(q["breach"].sum())
    uns = q[q["arrived_unstaffed"] & q["channel"].isin(channels)]
    n_u, b_u = len(uns), int(uns["breach"].sum())
    expected_after = (b - b_u) + n_u * ref_rate
    avoided = b - expected_after
    # Night-window volume per night, for sizing the shift.
    nights = q["created_ist"].dt.normalize().nunique()
    night_tickets_per_night = n_u / nights if nights else 0.0
    return {
        "quarter": quarter,
        "channels": ", ".join(channels),
        "eligible_tickets": n,
        "breaches": b,
        "baseline_rate": b / n,
        "unstaffed_arrivals": n_u,
        "unstaffed_breaches": b_u,
        "unstaffed_breach_rate": b_u / n_u if n_u else 0.0,
        "reference_rate": ref_rate,
        "reference_tickets": len(ref),
        "target_rate": expected_after / n,
        "breaches_avoided": avoided,
        "credits_avoided_inr": avoided * BREACH_CREDIT_INR,
        "night_tickets_per_night": night_tickets_per_night,
        "one_night_seat_cost_inr_per_quarter": AGENT_HOUR_COST_INR * SHIFT_HOURS * 91,
    }


def csat_by_breach(f: pd.DataFrame) -> pd.DataFrame:
    """CSAT (blank = no response, excluded) for breached vs on-time tickets."""
    return f.groupby("breach")["csat_score"].agg(["mean", "count"]).rename(
        index={True: "Breached", False: "On time"})
