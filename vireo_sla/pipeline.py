"""Ticket-level SLA facts: breach, roster assignment, coverage and breach attribution.

Everything here is deterministic arithmetic on the export + roster + policy parameters. No model is involved.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from .config import CHANNEL_FRONTLINE, SLA_TARGET_MIN, SHIFTS
from .data import load_clean

# Attribution classes, in the order they are tested.
COVERAGE_GAP = "Coverage gap"
SHIFT_START_BACKLOG = "Shift-start backlog"
HANDOFF = "Hand-off / unclear"
AGENT = "Agent-associated"
NO_BREACH = "No breach"
BREACH_CLASSES = [COVERAGE_GAP, SHIFT_START_BACKLOG, HANDOFF, AGENT]
STRUCTURAL_CLASSES = [COVERAGE_GAP, SHIFT_START_BACKLOG]


# ---------------------------------------------------------------- shift blocks
def shift_block(ts: datetime) -> tuple[str, datetime, datetime]:
    """Shift name, block start and block end (IST) containing a naive IST timestamp.

    A Night block belongs to the date on which it starts (22:00 on day d -> 06:00 on d+1),
    which is also how a roster row for a Night agent on day d is read.
    """
    day = datetime(ts.year, ts.month, ts.day)
    h = ts.hour
    if 6 <= h < 14:
        return "Morning", day + timedelta(hours=6), day + timedelta(hours=14)
    if 14 <= h < 22:
        return "Day", day + timedelta(hours=14), day + timedelta(hours=22)
    start = day + timedelta(hours=22) if h >= 22 else day - timedelta(hours=2)
    return "Night", start, start + timedelta(hours=8)


def arrival_window(ts: pd.Timestamp) -> str:
    return shift_block(ts.to_pydatetime())[0]


# ---------------------------------------------------------------- roster
def assign_roster(t: pd.DataFrame, agents: pd.DataFrame) -> pd.DataFrame:
    """Attach the resolving agent's roster row that was in force on the ticket's IST creation date."""
    t = t.copy()
    t["created_date"] = t["created_ist"].dt.normalize()
    m = t[["ticket_id", "agent_id", "created_date"]].merge(agents, on="agent_id", how="left")
    in_force = (m["created_date"] >= m["from_date"]) & (
        m["to_date"].isna() | (m["created_date"] <= m["to_date"])
    )
    m = m[in_force]
    if m["ticket_id"].duplicated().any():
        raise ValueError("Overlapping roster rows: a ticket matched more than one assignment")
    cols = ["ticket_id", "name", "site", "team", "shift", "tier"]
    out = t.merge(m[cols], on="ticket_id", how="left")
    out = out.rename(columns={"name": "agent_name", "team": "agent_team",
                              "shift": "agent_shift", "site": "agent_site", "tier": "agent_tier"})
    out["roster_matched"] = out["agent_shift"].notna()
    return out


class Staffing:
    """How many agents of a team are rostered on a given shift block (by roster dates only)."""

    def __init__(self, agents: pd.DataFrame):
        self.rows = [
            (r.team, r.shift, r.from_date.to_pydatetime(),
             None if pd.isna(r.to_date) else r.to_date.to_pydatetime())
            for r in agents.itertuples()
        ]
        self._cache: dict = {}

    def count(self, team: str, shift: str, block_date: datetime) -> int:
        key = (team, shift, block_date)
        if key not in self._cache:
            self._cache[key] = sum(
                1 for tm, sh, f, to in self.rows
                if tm == team and sh == shift and f <= block_date and (to is None or block_date <= to)
            )
        return self._cache[key]

    def staffed_minutes(self, team: str, start: datetime, end: datetime) -> tuple[float, float]:
        """(staffed minutes in [start, end), minutes from start until the team is next staffed)."""
        staffed, wait, cur, guard = 0.0, None, start, 0
        while cur < end or wait is None:
            name, b_start, b_end = shift_block(cur)
            is_staffed = self.count(team, name, datetime(b_start.year, b_start.month, b_start.day)) > 0
            if is_staffed and wait is None:
                wait = (cur - start).total_seconds() / 60
            if is_staffed and cur < end:
                staffed += (min(b_end, end) - cur).total_seconds() / 60
            cur = b_end
            guard += 1
            if guard > 30:  # > 10 days with nobody rostered
                wait = wait if wait is not None else float("inf")
                break
        return staffed, wait


# ---------------------------------------------------------------- main build
def build_ticket_facts(t: pd.DataFrame, agents: pd.DataFrame) -> pd.DataFrame:
    df = assign_roster(t, agents)

    df["sla_target_min"] = df["channel"].map(SLA_TARGET_MIN)
    if df["sla_target_min"].isna().any():
        raise ValueError(f"Unknown channel(s): {df.loc[df.sla_target_min.isna(), 'channel'].unique()}")
    df["eligible"] = df["frt_min"].notna() & (df["frt_min"] >= 0)
    # Breach = first response later than the target.
    df["breach"] = df["eligible"] & (df["frt_min"] > df["sla_target_min"])
    df["deadline_ist"] = df["created_ist"] + pd.to_timedelta(df["sla_target_min"], unit="m")

    # Reporting week: Monday-start ISO week of IST creation time.
    df["week"] = (df["created_ist"].dt.normalize()
                  - pd.to_timedelta(df["created_ist"].dt.weekday, unit="D")).dt.date
    df["quarter"] = df["created_ist"].dt.to_period("Q").astype(str)
    df["month"] = df["created_ist"].dt.to_period("M").astype(str)

    staffing = Staffing(agents)
    df["frontline_team"] = df["channel"].map(CHANNEL_FRONTLINE)
    arrival, arr_staffed, staffed_min, wait_min, resp_shift, dl_shift = [], [], [], [], [], []
    for r in df[["created_ist", "deadline_ist", "first_response_ist", "frontline_team"]].itertuples(index=False):
        dl_shift.append(shift_block(r.deadline_ist.to_pydatetime())[0])
        c = r.created_ist.to_pydatetime()
        name, b_start, _ = shift_block(c)
        arrival.append(name)
        arr_staffed.append(staffing.count(r.frontline_team, name,
                                          datetime(b_start.year, b_start.month, b_start.day)))
        s, w = staffing.staffed_minutes(r.frontline_team, c, r.deadline_ist.to_pydatetime())
        staffed_min.append(s)
        wait_min.append(w)
        resp_shift.append(shift_block(r.first_response_ist.to_pydatetime())[0]
                          if pd.notna(r.first_response_ist) else None)
    df["arrival_window"] = arrival
    df["frontline_staffed_at_arrival"] = arr_staffed
    df["arrived_unstaffed"] = df["frontline_staffed_at_arrival"] == 0
    df["staffed_min_before_deadline"] = staffed_min
    df["wait_to_staffed_min"] = wait_min
    df["response_window"] = resp_shift
    df["deadline_window"] = dl_shift

    # Could the resolving agent have been the first responder, and was the SLA missed on their watch?
    # (Added after the manual sample: a Day agent was being charged for an email whose deadline
    # passed during a rostered Night shift.)
    df["resolver_on_shift_at_response"] = df["response_window"] == df["agent_shift"]
    df["resolver_on_shift_at_deadline"] = df["deadline_window"] == df["agent_shift"]
    df["handoff"] = (df["transfers"] > 0) | (df["agent_team"] != df["assigned_team"])

    df["attribution"] = np.select(
        [
            ~df["breach"],
            df["staffed_min_before_deadline"] <= 0,
            df["arrived_unstaffed"],
            df["handoff"] | ~df["resolver_on_shift_at_response"] | ~df["resolver_on_shift_at_deadline"],
        ],
        [NO_BREACH, COVERAGE_GAP, SHIFT_START_BACKLOG, HANDOFF],
        default=AGENT,
    )
    df["structural_breach"] = df["attribution"].isin(STRUCTURAL_CLASSES)
    return df


def build(data_dir=None):
    """Load, clean and build ticket facts. Returns (facts, agents, data-quality log)."""
    t, agents, dq = load_clean(data_dir) if data_dir else load_clean()
    facts = build_ticket_facts(t, agents)
    dq["roster_unmatched"] = int((~facts["roster_matched"]).sum())
    dq["ineligible"] = int((~facts["eligible"]).sum())
    return facts, agents, dq
