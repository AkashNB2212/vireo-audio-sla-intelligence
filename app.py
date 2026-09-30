"""Vireo Audio - first-response SLA breach report. Run: streamlit run app.py"""
from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from vireo_sla import ai
from vireo_sla import metrics as M
from vireo_sla.data import PACK_MISSING_MESSAGE, missing_pack_files
from vireo_sla.config import BREACH_CREDIT_INR, MIN_TICKETS_FOR_RATE, MIN_TICKETS_FOR_REVIEW, SLA_TARGET_MIN
from vireo_sla.pipeline import AGENT, BREACH_CLASSES, COVERAGE_GAP, HANDOFF, SHIFT_START_BACKLOG, build
from vireo_sla.security import csv_safe
from vireo_sla.validation import run_checks

st.set_page_config(page_title="Vireo SLA breach report", layout="wide")

if missing_pack_files():
    st.error(PACK_MISSING_MESSAGE)
    st.code("data/tickets.csv\ndata/agents.csv", language=None)
    st.stop()

# Fixed categorical order (validated palette, slots 1-4). Colour follows the class, never rank.
CLASS_COLOURS = {COVERAGE_GAP: "#2a78d6", SHIFT_START_BACKLOG: "#eb6834",
                 HANDOFF: "#1baf7a", AGENT: "#eda100"}
CLASS_HELP = {
    COVERAGE_GAP: "Arrived when nobody in the channel's frontline team was rostered, and nobody was due on shift before the SLA deadline. No agent could have met the target.",
    SHIFT_START_BACKLOG: "Arrived when nobody was rostered; the next shift started before the deadline but the overnight queue was not cleared in time.",
    HANDOFF: "Arrived during rostered hours but the ticket was transferred / re-teamed, or the resolving agent was not on shift when the deadline passed or when the first reply went out - the delay cannot be pinned on the resolver.",
    AGENT: "Arrived during rostered hours, no hand-off, and the resolving agent was on shift both when the SLA deadline passed and when the late first reply went out. Associated with the agent - not proof of cause.",
}


@st.cache_data(show_spinner="Building ticket facts from the export...")
def load():
    facts, agents, dq = build()
    return facts, agents, dq


f, agents, dq = load()
weeks = sorted(f["week"].unique())
# The export starts and ends mid-week, so the first and last weeks are partial.
PARTIAL = {weeks[0], weeks[-1]}
weeks_desc = weeks[::-1]
DEFAULT_WEEK_IDX = 1  # latest complete week


def week_label(w):
    return f"{w} (partial week)" if w in PARTIAL else str(w)


roster_names = agents["name"].unique().tolist()


def inr(x):
    return ai.inr(x)


def month_span(start: pd.Timestamp, end: pd.Timestamp) -> str:
    """'Jan-Jun 2025', or 'Nov 2025-Feb 2026' across years."""
    if start.year == end.year:
        return f"{start:%b}-{end:%b %Y}"
    return f"{start:%b %Y}-{end:%b %Y}"


def join_words(items: list[str]) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# Coverage facts are derived from the data, never hard-coded.
CHANNEL_ORDER = ["chat", "social", "email", "voice"]
_unstaffed = f[f["arrived_unstaffed"]]
GAP_START = _unstaffed["created_ist"].min() if len(_unstaffed) else None
GAP_CHANNELS = [c for c in CHANNEL_ORDER if c in set(_unstaffed["channel"])]
_ref = f[(f["arrival_window"] == "Night") & ~f["arrived_unstaffed"]]
REF_SPAN = month_span(_ref["created_ist"].min(), _ref["created_ist"].max()) if len(_ref) else None
TOP_STRUCTURAL_SHIFT = (f.loc[f["structural_breach"], "agent_shift"].mode().iat[0]
                        if f["structural_breach"].any() else None)


class _DoNotCache(Exception):
    """Raised inside the cached function so a failed or skipped API call is not cached
    (Streamlit never caches exceptions). Paid results, including ones rejected by the
    grounding check, are cached so they are not bought twice."""

    def __init__(self, briefing):
        self.briefing = briefing


@st.cache_data(show_spinner="Writing the weekly briefing...")
def _llm_briefing(facts: dict, names: tuple, model: str, workspace_set: bool) -> ai.Briefing:
    """One paid call per (facts, model, workspace) - reruns and other widget clicks reuse the result."""
    b = ai.generate_briefing(facts, list(names), use_llm=True)
    if b.source != "llm" and not b.violations:
        raise _DoNotCache(b)
    return b


def get_briefing(facts: dict, use_llm: bool) -> ai.Briefing:
    if not (use_llm and ai.llm_available()):
        return ai.generate_briefing(facts, roster_names, use_llm=use_llm)  # free deterministic template
    try:
        return _llm_briefing(facts, tuple(roster_names), ai.DEFAULT_MODEL,
                             bool(os.environ.get("ANTHROPIC_WORKSPACE_ID")))
    except _DoNotCache as e:
        return e.briefing


@st.cache_data(show_spinner="Running data checks...")
def cached_checks() -> pd.DataFrame:
    """The checks re-read the raw CSV; the data is immutable, so run them once, not on every click."""
    f_, agents_, _ = load()
    return run_checks(f_, agents_)


def stacked_weekly(df: pd.DataFrame, title: str):
    wk = df.groupby("week")["attribution"].value_counts().unstack(fill_value=0)
    fig = go.Figure()
    for c in BREACH_CLASSES:
        if c in wk:
            fig.add_bar(x=wk.index, y=wk[c], name=c, marker_color=CLASS_COLOURS[c],
                        marker_line_width=0,
                        hovertemplate="%{x}<br>" + c + ": %{y}<extra></extra>")
    fig.update_layout(barmode="stack", bargap=0.15, title=title, height=340,
                      legend=dict(orientation="h", y=-0.2), margin=dict(t=50, b=10, l=10, r=10),
                      yaxis_title="Breached tickets", hovermode="x unified")
    return fig


# ---------------------------------------------------------------- sidebar
st.sidebar.title("Vireo SLA breach report")
st.sidebar.caption("Source: helpdesk API export (UTC, converted to IST), roster and support-policy parameters. "
                   "All numbers are computed deterministically from these files.")
st.sidebar.markdown("**SLA targets**\n\n" + "\n".join(
    f"- {k}: {v} min" for k, v in SLA_TARGET_MIN.items()) + f"\n\nCredit per breach: Rs {BREACH_CREDIT_INR}")

tab_over, tab_week, tab_why, tab_agent, tab_checks = st.tabs(
    ["Overview", "Weekly breach report", "Why breaches happen", "Fair agent review", "Data checks"])

# ---------------------------------------------------------------- overview
with tab_over:
    goal = M.business_goal(f)
    h_q = M.headline(f[f["quarter"] == goal["quarter"]])
    exp = M.credit_exposure(f[f["quarter"] == goal["quarter"]])
    _q = pd.Period(goal["quarter"])
    st.subheader(f"Latest full quarter: {goal['quarter']} ({month_span(_q.start_time, _q.end_time)})")
    c = st.columns(4)
    c[0].metric("First-response breach rate", f"{goal['baseline_rate']:.1%}",
                help=f"{goal['breaches']} of {goal['eligible_tickets']} tickets")
    c[1].metric("Breached tickets", f"{goal['breaches']:,}")
    c[2].metric("SLA credit exposure", inr(exp["total_exposure_inr"]),
                help=f"Rs {BREACH_CREDIT_INR} x breaches. Policy-implied, not read from a ledger.")
    c[3].metric("Breaches with nobody rostered", f"{h_q['structural_breaches'] / h_q['breaches']:.0%}",
                help="Coverage gap + shift-start backlog, as share of all breaches")

    if GAP_START is not None:
        st.info(
            f"**Key finding.** Tickets that arrive between 22:00 and 06:00 IST breach "
            f"{goal['unstaffed_breach_rate']:.0%} of the time. Since {GAP_START:%-d %B %Y} the roster has no "
            f"Night shift for {join_words(GAP_CHANNELS).replace(' and ', ' or ')}. When it did ({REF_SPAN}), "
            f"the same window breached {goal['reference_rate']:.1%}. The {TOP_STRUCTURAL_SHIFT.lower()} team "
            f"resolves these tickets, so the breaches land on them, but most arrived while nobody was rostered.")
    else:
        st.info("**Key finding.** Every ticket arrived while its frontline team was rostered.")
    st.success(
        f"**Business goal.** Cut first-response breach rate from {goal['baseline_rate']:.1%} to about "
        f"{goal['target_rate']:.1%} by rostering one existing agent on the 22:00-06:00 window for "
        f"{join_words(goal['channels'].split(', '))} (no new hire). Modelled value: about {goal['breaches_avoided']:.0f} fewer breaches, "
        f"**{inr(goal['credits_avoided_inr'])} a quarter** in SLA credits. This is a model, not realised savings.")

    mc = M.monthly_credits(f)
    fig = go.Figure()
    fig.add_bar(x=mc["month"], y=mc["breaches"] - mc["structural"], name="Breaches during rostered hours",
                marker_color=CLASS_COLOURS[AGENT], marker_line_width=0)
    fig.add_bar(x=mc["month"], y=mc["structural"], name="Breaches with nobody rostered",
                marker_color=CLASS_COLOURS[COVERAGE_GAP], marker_line_width=0)
    fig.update_layout(barmode="stack", title="Monthly breaches: rostered hours vs nobody rostered",
                      height=340, legend=dict(orientation="h", y=-0.2), margin=dict(t=50, b=10, l=10, r=10),
                      yaxis_title="Breached tickets", hovermode="x unified")
    st.plotly_chart(fig, width="stretch")
    with st.expander(f"Monthly table (credit exposure = breaches x Rs {BREACH_CREDIT_INR})"):
        st.dataframe(mc.assign(breach_rate=mc["breach_rate"].map("{:.1%}".format)), hide_index=True)

    st.markdown("#### Weekly briefing")
    wk_sel = st.selectbox("Week starting (Monday, IST)", weeks_desc, index=DEFAULT_WEEK_IDX, key="brief_week", format_func=week_label)
    use_llm = st.toggle("Use AI narrative", value=ai.llm_available())
    facts = ai.weekly_fact_sheet(f, wk_sel)
    brief = get_briefing(facts, use_llm)
    st.caption(f"Source: **{brief.source}** - {brief.note}")
    st.markdown(brief.text.replace("\n", "  \n"))  # keep line breaks in markdown
    if brief.violations:
        st.warning("Rejected LLM output. Problems: " + "; ".join(brief.violations))
    with st.expander("Facts passed to the model (the only numbers it may use)"):
        st.json(facts)

# ---------------------------------------------------------------- weekly report
with tab_week:
    st.subheader("Weekly breach report by shift and agent")
    st.caption(f"Shift = the resolving agent's rostered shift on the ticket's creation date (as the helpdesk "
               f"reports it). Rates are hidden below {MIN_TICKETS_FOR_RATE} tickets: most agents handle 2-5 "
               f"tickets a week, so a weekly rate is mostly noise.")
    c1, c2, c3 = st.columns([2, 2, 3])
    wk = c1.selectbox("Week starting", weeks_desc, index=DEFAULT_WEEK_IDX, format_func=week_label)
    shifts = c2.multiselect("Shift", ["Morning", "Day", "Night"], default=["Morning", "Day", "Night"])
    agent_opts = sorted(f.loc[f["week"] == wk, "agent_name"].dropna().unique())
    agent_sel = c3.multiselect("Agent (optional)", agent_opts)

    rep = M.weekly_agent_report(f)
    r = rep[(rep["week"] == wk) & rep["agent_shift"].isin(shifts)]
    if agent_sel:
        r = r[r["agent_name"].isin(agent_sel)]
    w = f[(f["week"] == wk) & f["agent_shift"].isin(shifts)]
    if agent_sel:
        w = w[w["agent_name"].isin(agent_sel)]
    k = st.columns(4)
    k[0].metric("Eligible tickets", int(w["eligible"].sum()))
    k[1].metric("Breaches", int(w["breach"].sum()))
    k[2].metric("Breach rate", f"{w['breach'].mean():.1%}" if len(w) else "-")
    k[3].metric("...of which nobody rostered", int(w["structural_breach"].sum()))

    sh = M.weekly_shift_report(f[f["week"] == wk])
    sh = sh[sh["agent_shift"].isin(shifts)]
    st.markdown("**By shift**")
    st.dataframe(sh.assign(breach_rate=sh["breach_rate"].map("{:.1%}".format),
                           mean_frt_min=sh["mean_frt_min"].round(1),
                           median_frt_min=sh["median_frt_min"].round(1)).drop(columns="week"),
                 hide_index=True, width="stretch")

    st.markdown("**By agent**")
    show = r[["agent_shift", "agent_site", "agent_team", "agent_id", "agent_name", "eligible_tickets",
              "breaches", "breach_rate_display", "share_of_week_breaches", "mean_frt_min", "median_frt_min",
              *BREACH_CLASSES]].rename(columns={"breach_rate_display": "breach_rate"})
    show = show.assign(breach_rate=show["breach_rate"].map(
        lambda x: f"low volume (<{MIN_TICKETS_FOR_RATE})" if pd.isna(x) else f"{x:.1%}"))
    st.dataframe(show, hide_index=True, width="stretch", column_config={
        "share_of_week_breaches": st.column_config.NumberColumn(format="percent"),
    })
    st.download_button("Download full weekly agent report (all weeks, CSV)",
                       csv_safe(rep).to_csv(index=False).encode(), "vireo_weekly_agent_report.csv", "text/csv")
    st.download_button("Download ticket-level facts for this week (CSV)",
                       csv_safe(w.drop(columns=["customer_message", "agent_notes"])).to_csv(index=False).encode(),
                       f"vireo_tickets_week_{wk}.csv", "text/csv")

# ---------------------------------------------------------------- why
with tab_why:
    st.subheader("Why breaches happen: attribution from timestamps and roster")
    for c in BREACH_CLASSES:
        st.markdown(f"- <span style='color:{CLASS_COLOURS[c]}'>■</span> **{c}** - {CLASS_HELP[c]}",
                    unsafe_allow_html=True)
    st.plotly_chart(stacked_weekly(f[f["breach"]], "Weekly breaches by attribution class"),
                    width="stretch")

    col1, col2 = st.columns(2)
    # From the first full month after the coverage gap began (the whole export if there is no gap).
    chart_from = (GAP_START.normalize() + pd.offsets.MonthBegin(1)) if GAP_START is not None else f["created_ist"].min()
    post = f[f["created_ist"] >= chart_from]
    hr = post.assign(hour=post["created_ist"].dt.hour).groupby(["hour", "channel"])["breach"].mean().unstack()
    fig = go.Figure()
    for i, ch in enumerate(["chat", "social", "email", "voice"]):
        fig.add_scatter(x=hr.index, y=hr[ch], name=ch, mode="lines+markers", line=dict(width=2),
                        marker=dict(size=8), line_color=list(CLASS_COLOURS.values())[i])
    fig.add_vrect(x0=-0.5, x1=5.5, fillcolor="grey", opacity=0.12, line_width=0)
    fig.add_vrect(x0=21.5, x1=23.5, fillcolor="grey", opacity=0.12, line_width=0)
    fig.update_layout(title=f"Breach rate by arrival hour (IST), since {chart_from:%b %Y}<br><sup>Shaded = hours with no Night shift rostered</sup>",
                      height=360, yaxis_tickformat=".0%", xaxis_title="Hour ticket arrived (IST)",
                      legend=dict(orientation="h", y=-0.25), margin=dict(t=50, b=10, l=10, r=10),
                      hovermode="x unified")
    col1.plotly_chart(fig, width="stretch")

    cross = pd.crosstab(f["agent_shift"], f["attribution"])[[*BREACH_CLASSES]]
    n_months = f["month"].nunique()
    col2.markdown(f"**All {n_months} months: breaches by resolving shift x class**")
    col2.dataframe(cross, width="stretch")
    if TOP_STRUCTURAL_SHIFT:
        col2.caption(f"The {TOP_STRUCTURAL_SHIFT} shift carries most breaches with nobody rostered because it "
                     "inherits the overnight queue. Compare the Agent-associated column across shifts.")

    st.markdown("#### Ticket drill-down")
    d1, d2, d3 = st.columns(3)
    cls = d1.selectbox("Class", BREACH_CLASSES)
    wk2 = d2.selectbox("Week", ["All"] + [str(x) for x in weeks[::-1]])
    n_show = d3.slider("Rows", 5, 200, 25)
    dd = f[f["attribution"] == cls]
    if wk2 != "All":
        dd = dd[dd["week"].astype(str) == wk2]
    cols = ["ticket_id", "channel", "created_ist", "first_response_ist", "frt_min", "sla_target_min",
            "arrival_window", "frontline_staffed_at_arrival", "staffed_min_before_deadline",
            "agent_name", "agent_shift", "response_window", "transfers", "assigned_team", "agent_team",
            "category", "customer_message", "agent_notes"]
    # Customer text is untrusted: neutralise spreadsheet formulas in case the table is exported.
    st.dataframe(csv_safe(dd[cols].head(n_show)), hide_index=True, width="stretch")

# ---------------------------------------------------------------- agent review
with tab_agent:
    st.subheader("Fair agent review (use this, not a weekly count, for individual conversations)")
    n_weeks = st.slider("Window (weeks ending with the selected week)", 8, 52, 26)
    end = st.selectbox("Window ends with week", weeks_desc, index=DEFAULT_WEEK_IDX, key="rev_end", format_func=week_label)
    win = [x for x in weeks if x <= end][-n_weeks:]
    rv = M.agent_review(f[f["week"].isin(win)])
    st.caption(
        "Counts only tickets the agent owned from arrival to SLA deadline: arrived while the frontline team was rostered, no "
        "transfer or re-teaming, and both arrival and deadline fell inside the agent's shift. 'Expected' is what "
        "the agent would have breached at the team-wide rate for their channel mix. Flagged only when the gap "
        f"is more than 2 standard deviations and the agent has at least {MIN_TICKETS_FOR_REVIEW} such tickets. "
        "Tier 2 is not compared with Tier 1.")
    counts = rv["flag"].value_counts()
    st.write(" · ".join(f"**{k}**: {v}" for k, v in counts.items()))
    n_cmp = int(rv["flag"].isin(["Within normal range", "Above expected - discuss", "Below expected"]).sum())
    st.caption(f"{n_cmp} agents compared. Even if every agent performed identically, about "
               f"{n_cmp * 0.023:.1f} would land above the line by chance, so treat a single flag as a reason "
               "to look at the tickets together, not as a verdict.")
    st.dataframe(rv[["agent_name", "agent_id", "agent_team", "agent_shift", "agent_site", "attributable_tickets",
                     "breaches", "expected", "breach_rate", "expected_rate", "z", "flag"]],
                 hide_index=True, width="stretch", column_config={
                     "breach_rate": st.column_config.NumberColumn(format="percent"),
                     "expected_rate": st.column_config.NumberColumn(format="percent")})
    st.download_button("Download agent review (CSV)", csv_safe(rv).to_csv(index=False).encode(),
                       "vireo_agent_review.csv", "text/csv")

# ---------------------------------------------------------------- checks
with tab_checks:
    st.subheader("Data-quality log and reconciliation checks (run live on every load)")
    st.json(dq)
    res = cached_checks()
    st.dataframe(res, hide_index=True, width="stretch")
    if res["passed"].all():
        st.success(f"All {len(res)} checks passed.")
    else:
        st.error(f"{(~res['passed']).sum()} check(s) failed.")
