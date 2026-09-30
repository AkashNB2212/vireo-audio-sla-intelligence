"""Unit tests on small hand-built fixtures (expected values worked out by hand) plus
reconciliation tests on the real export."""
import pandas as pd
import pytest

from vireo_sla import ai
from vireo_sla import metrics as M
from vireo_sla.config import BREACH_CREDIT_INR, DATA_DIR
from vireo_sla.data import AGENT_COLUMNS, SchemaError, clean_agents, clean_tickets, require_columns
from vireo_sla.pipeline import (AGENT, COVERAGE_GAP, HANDOFF, NO_BREACH, SHIFT_START_BACKLOG,
                                build, build_ticket_facts, shift_block)
from vireo_sla.validation import run_checks

# ---------------------------------------------------------------- fixtures
ROSTER = pd.DataFrame([
    # Chat agent on Night until 29 Jun, then Day (a mid-year shift move)
    ["X1", "Night Mover", "Indore", "Chat Frontline", "Night", 1, "2025-01-01", "2025-06-29"],
    ["X1", "Night Mover", "Indore", "Chat Frontline", "Day", 1, "2025-06-30", None],
    ["X2", "Morning Chat", "Bengaluru", "Chat Frontline", "Morning", 1, "2025-01-01", None],
    ["X3", "Morning Email", "Bengaluru", "Email Frontline", "Morning", 1, "2025-01-01", None],
    ["X4", "Billing Day", "Bengaluru", "Billing", "Day", 1, "2025-01-01", None],
], columns=AGENT_COLUMNS)


def ticket(tid, created, fr, channel="chat", agent="X2", team="Chat Frontline", transfers=0,
           source="helpdesk", csat=None, status="resolved"):
    return dict(ticket_id=tid, created_at=created, first_response_at=fr, resolved_at=fr, status=status,
                channel=channel, customer_id="C1", order_id=None, product_sku="S", category="Other",
                priority="Normal", assigned_team=team, agent_id=agent, transfers=transfers,
                csat_score=csat, refund_amount_inr=None, refund_reason_code=None, replacement_issued="N",
                customer_message="m", agent_notes="n", source_system=source)


def facts_for(rows):
    t, _ = clean_tickets(pd.DataFrame(rows))
    return build_ticket_facts(t, clean_agents(ROSTER)).set_index("ticket_id")


# ---------------------------------------------------------------- schema / cleaning
def test_schema_validation_rejects_missing_columns():
    with pytest.raises(SchemaError):
        require_columns(pd.DataFrame({"agent_id": []}), AGENT_COLUMNS, "agents.csv")


def test_duplicates_keep_helpdesk_copy_and_legacy_zero_csat_is_blank():
    rows = [ticket("T1", "2025-03-03 04:00", "2025-03-03 04:05", source="legacy_fd", csat=0),
            ticket("T1", "2025-03-03 04:00", "2025-03-03 04:05", source="helpdesk", csat=None),
            ticket("T2", "2025-03-03 04:00", "2025-03-03 04:05", source="legacy_fd", csat=0)]
    t, dq = clean_tickets(pd.DataFrame(rows))
    assert len(t) == 2 and dq["rows_removed_as_duplicates"] == 1
    assert t.set_index("ticket_id").loc["T1", "source_system"] == "helpdesk"
    assert t["csat_score"].isna().all()


def test_utc_to_ist_and_negative_duration_detection():
    rows = [ticket("T1", "2025-03-03 17:00", "2025-03-03 17:05"),
            ticket("T2", "2025-03-03 04:00", "2025-03-03 03:50")]
    t, dq = clean_tickets(pd.DataFrame(rows))
    t = t.set_index("ticket_id")
    assert t.loc["T1", "created_ist"] == pd.Timestamp("2025-03-03 22:30")
    assert dq["negative_frt"] == 1


def test_shift_block_boundaries():
    assert shift_block(pd.Timestamp("2025-03-03 05:59").to_pydatetime())[0] == "Night"
    assert shift_block(pd.Timestamp("2025-03-03 06:00").to_pydatetime())[0] == "Morning"
    assert shift_block(pd.Timestamp("2025-03-03 14:00").to_pydatetime())[0] == "Day"
    name, start, _ = shift_block(pd.Timestamp("2025-03-03 02:00").to_pydatetime())
    assert name == "Night" and start == pd.Timestamp("2025-03-02 22:00")


# ---------------------------------------------------------------- SLA / breach
@pytest.mark.parametrize("channel,minutes,breach", [
    ("chat", 15, False), ("chat", 16, True), ("social", 240, False), ("social", 241, True),
    ("email", 480, False), ("email", 481, True), ("voice", 120, False), ("voice", 121, True)])
def test_sla_target_mapping_and_breach_boundary(channel, minutes, breach):
    created = pd.Timestamp("2025-03-03 03:00")  # 08:30 IST, Morning, staffed
    fr = created + pd.Timedelta(minutes=minutes)
    team = {"chat": "Chat Frontline", "social": "Chat Frontline", "email": "Email Frontline",
            "voice": "Voice Frontline"}[channel]
    f = facts_for([ticket("T", str(created)[:16], str(fr)[:16], channel=channel, team=team)])
    assert bool(f.loc["T", "breach"]) is breach


# ---------------------------------------------------------------- roster assignment
def test_roster_assignment_follows_effective_date():
    f = facts_for([ticket("A", "2025-06-28 10:00", "2025-06-28 10:05", agent="X1"),
                   ticket("B", "2025-07-01 10:00", "2025-07-01 10:05", agent="X1")])
    assert f.loc["A", "agent_shift"] == "Night" and f.loc["B", "agent_shift"] == "Day"


def test_roster_date_uses_ist_not_utc():
    # 2025-06-29 19:00 UTC = 2025-06-30 00:30 IST -> the Day row (from 30 Jun) applies.
    f = facts_for([ticket("A", "2025-06-29 19:00", "2025-06-29 19:05", agent="X1")])
    assert f.loc["A", "agent_shift"] == "Day"


# ---------------------------------------------------------------- attribution
def test_attribution_classes():
    rows = [
        # 23:30 IST chat, July 2025: no Night chat agent; next shift 06:00 > 15-min deadline -> gap
        ticket("GAP", "2025-07-02 18:00", "2025-07-03 00:45"),
        # 23:30 IST email: deadline 07:30 IST; Morning email starts 06:00; answered 08:00 -> backlog
        ticket("BACKLOG", "2025-07-02 18:00", "2025-07-03 02:30", channel="email", agent="X3",
               team="Email Frontline"),
        # Same night window BEFORE the move: Night chat agent rostered -> not structural
        ticket("STAFFED_NIGHT", "2025-06-02 18:00", "2025-06-02 18:40", agent="X1"),
        # Morning chat, late, transferred -> hand-off
        ticket("HANDOFF", "2025-07-02 03:00", "2025-07-02 03:30", transfers=1),
        # Morning chat, late, resolver X4 is Day shift and reply went out in Morning -> hand-off
        ticket("OFFSHIFT", "2025-07-02 03:00", "2025-07-02 03:30", agent="X4", team="Billing"),
        # Morning chat, late, no hand-off, resolver on shift -> agent-associated
        ticket("AGENT", "2025-07-02 03:00", "2025-07-02 03:30"),
        ticket("OK", "2025-07-02 03:00", "2025-07-02 03:05"),
    ]
    f = facts_for(rows)
    assert f.loc["GAP", "attribution"] == COVERAGE_GAP
    assert f.loc["BACKLOG", "attribution"] == SHIFT_START_BACKLOG
    assert f.loc["STAFFED_NIGHT", "attribution"] == AGENT
    assert not f.loc["STAFFED_NIGHT", "arrived_unstaffed"]
    assert f.loc["HANDOFF", "attribution"] == HANDOFF
    assert f.loc["OFFSHIFT", "attribution"] == HANDOFF
    assert f.loc["AGENT", "attribution"] == AGENT
    assert f.loc["OK", "attribution"] == NO_BREACH
    assert f.loc["BACKLOG", "wait_to_staffed_min"] == 390  # 23:30 -> 06:00


# ---------------------------------------------------------------- real export
@pytest.fixture(scope="module")
def real():
    return build()


def test_all_reconciliation_checks_pass(real):
    f, agents, _ = real
    res = run_checks(f, agents)
    assert res["passed"].all(), res[~res["passed"]].to_string()


def test_known_data_quality_counts(real):
    f, _, dq = real
    # Invariants, not pack-specific counts: every raw row is either kept or removed as a duplicate.
    assert dq["clean_rows"] + dq["rows_removed_as_duplicates"] + dq["unparseable_created"] == dq["raw_rows"]
    assert dq["duplicate_ticket_ids"] == dq["rows_removed_as_duplicates"]   # one extra copy per duplicated ID
    assert dq["clean_rows"] == len(f) and f["ticket_id"].is_unique
    assert dq["roster_unmatched"] == 0 and dq["negative_frt"] == 0


def test_weekly_conservation_per_week(real):
    f, _, _ = real
    wk = M.weekly_agent_report(f).groupby("week")[["eligible_tickets", "breaches"]].sum()
    direct = f.groupby("week").agg(eligible_tickets=("eligible", "sum"), breaches=("breach", "sum"))
    pd.testing.assert_frame_equal(wk.astype(int), direct.astype(int), check_names=False)


def test_share_of_week_breaches_sums_to_one(real):
    f, _, _ = real
    wk = M.weekly_agent_report(f)
    has_breach = wk.groupby("week")["breaches"].sum() > 0
    s = wk.groupby("week")["share_of_week_breaches"].sum()
    assert ((s[has_breach] - 1).abs() < 1e-9).all()
    assert (s[~has_breach] == 0).all()


def test_low_volume_guardrail_hides_rates(real):
    f, _, _ = real
    wk = M.weekly_agent_report(f)
    assert wk.loc[wk["low_volume"], "breach_rate_display"].isna().all()
    assert wk.loc[~wk["low_volume"], "breach_rate_display"].notna().all()


def test_business_goal_arithmetic(real):
    f, _, _ = real
    g = M.business_goal(f)
    q = f[f["quarter"] == g["quarter"]]
    uns = q[q["arrived_unstaffed"] & q["channel"].isin(["chat", "social", "email"])]
    expected_after = (q["breach"].sum() - uns["breach"].sum()) + len(uns) * g["reference_rate"]
    assert g["target_rate"] == pytest.approx(expected_after / len(q))
    assert g["credits_avoided_inr"] == pytest.approx((q["breach"].sum() - expected_after) * BREACH_CREDIT_INR)
    assert g["target_rate"] < g["baseline_rate"]


# ---------------------------------------------------------------- AI grounding
def test_template_briefing_passes_its_own_grounding_check(real):
    f, agents, _ = real
    names = agents["name"].unique().tolist()
    for wk in sorted(f["week"].unique())[-6:]:
        facts = ai.weekly_fact_sheet(f, wk)
        assert ai.grounding_violations(ai.template_briefing(facts), facts, names) == []


def test_grounding_check_catches_invented_numbers_and_names(real):
    f, agents, _ = real
    facts = ai.weekly_fact_sheet(f, sorted(f["week"].unique())[-1])
    bad = "Breaches rose to 612 (31.4%) and Asha Verma was slowest on 2026-01-01."
    names = agents["name"].unique().tolist() + ["Asha Verma"]   # fictional roster name, never flagged
    v = ai.grounding_violations(bad, facts, names)
    assert any("612" in x for x in v) and any("31.4" in x for x in v)
    assert any("Asha Verma" in x for x in v) and any("2026-01-01" in x for x in v)


def test_no_api_key_falls_back_to_template(real, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    f, agents, _ = real
    facts = ai.weekly_fact_sheet(f, sorted(f["week"].unique())[-1])
    b = ai.generate_briefing(facts, agents["name"].unique().tolist())
    assert b.source == "template" and "What happened" in b.text


def test_inr_indian_grouping():
    assert ai.inr(123456.7) == "Rs 1,23,457"
    assert ai.inr(500000) == "Rs 5,00,000"
    assert ai.inr(350) == "Rs 350"


class _FakeClient:
    """Stands in for anthropic.Anthropic so the LLM code path runs without a key or network."""
    reply = ""

    def __init__(self, *a, **k):
        from types import SimpleNamespace as NS
        outer = self

        class _Msgs:
            def create(self, **kw):
                outer.kwargs = kw
                return NS(stop_reason="end_turn", model=kw["model"],
                          content=[NS(type="text", text=_FakeClient.reply)],
                          usage=NS(input_tokens=900, output_tokens=150))
        self.beta = NS(messages=_Msgs())
        self.messages = _Msgs()


@pytest.mark.parametrize("grounded", [True, False])
def test_llm_path_accepts_grounded_and_rejects_ungrounded(real, monkeypatch, grounded):
    import anthropic
    f, agents, _ = real
    facts = ai.weekly_fact_sheet(f, sorted(f["week"].unique())[-2])
    _FakeClient.reply = (f"What happened\n{facts['breaches']} breaches ({facts['breach_rate']})." if grounded
                         else "What happened\nBreaches rose 40% to 777.")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    monkeypatch.setattr(anthropic, "Anthropic", _FakeClient)
    b = ai.generate_briefing(facts, agents["name"].unique().tolist())
    assert b.source == ("llm" if grounded else "template")
    assert bool(b.violations) is (not grounded)


def _bad_request(msg):
    import anthropic
    import httpx2
    r = httpx2.Response(400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return anthropic.BadRequestError(msg, response=r, body={"type": "error", "error": {
        "type": "invalid_request_error", "message": msg}})


def test_llm_retries_plainer_request_after_400(real, monkeypatch):
    """If the account rejects the beta fallback parameter, the standard request is tried next."""
    import anthropic
    f, agents, _ = real
    facts = ai.weekly_fact_sheet(f, sorted(f["week"].unique())[-2])
    _FakeClient.reply = f"What happened\n{facts['breaches']} breaches ({facts['breach_rate']})."

    class _BetaRejects(_FakeClient):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            from types import SimpleNamespace as NS

            def reject(**kw):
                raise _bad_request("fallbacks: Extra inputs are not permitted")
            self.beta = NS(messages=NS(create=reject))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    monkeypatch.setattr(anthropic, "Anthropic", _BetaRejects)
    b = ai.generate_briefing(facts, agents["name"].unique().tolist())
    assert b.source == "llm" and "standard request" in b.note


def test_llm_400_on_every_attempt_shows_api_message(real, monkeypatch):
    import anthropic
    from types import SimpleNamespace as NS
    f, agents, _ = real
    facts = ai.weekly_fact_sheet(f, sorted(f["week"].unique())[-2])

    class _AllReject:
        def __init__(self, *a, **k):
            def reject(**kw):
                raise _bad_request("model: invalid model name")
            self.beta = NS(messages=NS(create=reject))
            self.messages = NS(create=reject)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    monkeypatch.setattr(anthropic, "Anthropic", _AllReject)
    b = ai.generate_briefing(facts, agents["name"].unique().tolist())
    assert b.source == "template" and "invalid model name" in b.note


@pytest.mark.pack
def test_app_does_not_repeat_paid_call_on_rerun(monkeypatch):
    """Widget clicks re-run the whole Streamlit script; the same briefing must not be re-bought."""
    import anthropic
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    calls = []

    class _Counting(_FakeClient):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            from types import SimpleNamespace as NS
            inner = self.beta.messages

            def create(**kw):
                calls.append(kw["model"])
                return inner.create(**kw)
            self.beta = NS(messages=NS(create=create))

    _FakeClient.reply = "What happened\nSee facts."
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-real")
    monkeypatch.setattr(anthropic, "Anthropic", _Counting)
    st.cache_data.clear()

    at = AppTest.from_file(str(DATA_DIR.parent / "app.py"), default_timeout=120).run()
    assert not at.exception and len(calls) == 1
    at.slider[0].set_value(20).run()          # unrelated widget on another tab
    assert len(calls) == 1
    at.selectbox[0].set_value(at.selectbox[0].options[2]).run()  # different week -> new briefing
    assert len(calls) == 2
    st.cache_data.clear()


def test_missing_task_pack_is_detected(tmp_path):
    """Without the (confidential, not committed) task pack the app and export stop with instructions."""
    from vireo_sla.data import PACK_MISSING_MESSAGE, missing_pack_files
    assert missing_pack_files(tmp_path) == ["tickets.csv", "agents.csv"]
    (tmp_path / "tickets.csv").write_text("x")
    assert missing_pack_files(tmp_path) == ["agents.csv"]
    assert "data/" in PACK_MISSING_MESSAGE and "tickets.csv" in PACK_MISSING_MESSAGE
