"""Load and clean the export. Deterministic; no inference beyond what the pack documentation states."""
from __future__ import annotations

import pandas as pd

from .config import DATA_DIR, IST_OFFSET_MINUTES

TICKET_COLUMNS = [
    "ticket_id", "created_at", "first_response_at", "resolved_at", "status", "channel",
    "customer_id", "order_id", "product_sku", "category", "priority", "assigned_team",
    "agent_id", "transfers", "csat_score", "refund_amount_inr", "refund_reason_code",
    "replacement_issued", "customer_message", "agent_notes", "source_system",
]
AGENT_COLUMNS = ["agent_id", "name", "site", "team", "shift", "tier", "from_date", "to_date"]

# The task pack is confidential and is not in the public repository. The app needs these two files.
REQUIRED_PACK_FILES = ("tickets.csv", "agents.csv")
PACK_MISSING_MESSAGE = (
    "Task pack not found. The original task-pack data is intentionally not included in the repository. "
    "Copy tickets.csv and agents.csv from the task pack supplied with the assignment into the data/ folder, "
    "then run again.")


def missing_pack_files(data_dir=DATA_DIR) -> list[str]:
    return [name for name in REQUIRED_PACK_FILES if not (data_dir / name).is_file()]


class SchemaError(ValueError):
    pass


def require_columns(df: pd.DataFrame, cols: list[str], name: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SchemaError(f"{name} is missing required columns: {missing}")


def load_raw(data_dir=DATA_DIR) -> tuple[pd.DataFrame, pd.DataFrame]:
    tickets = pd.read_csv(data_dir / "tickets.csv", dtype={"ticket_id": str, "agent_id": str})
    agents = pd.read_csv(data_dir / "agents.csv", dtype={"agent_id": str})
    require_columns(tickets, TICKET_COLUMNS, "tickets.csv")
    require_columns(agents, AGENT_COLUMNS, "agents.csv")
    return tickets, agents


def clean_agents(agents: pd.DataFrame) -> pd.DataFrame:
    a = agents.copy()
    a["from_date"] = pd.to_datetime(a["from_date"], errors="raise")
    a["to_date"] = pd.to_datetime(a["to_date"], errors="raise")  # blank = still current
    return a


def clean_tickets(tickets: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Return cleaned tickets (one row per ticket_id, IST timestamps) and a data-quality log."""
    t = tickets.copy()
    dq: dict = {"raw_rows": len(t)}

    # Timestamps: parse strictly; the export is UTC -> convert to IST.
    for col in ["created_at", "first_response_at", "resolved_at"]:
        t[col] = pd.to_datetime(t[col], errors="coerce", format="%Y-%m-%d %H:%M")
    dq["unparseable_created"] = int(t["created_at"].isna().sum())
    dq["missing_first_response"] = int(t["first_response_at"].isna().sum())
    # A ticket with no valid creation time cannot be placed in a week or shift: quarantine it
    # (counted above) rather than letting one malformed row stop the whole report.
    t = t[t["created_at"].notna()].copy()
    off = pd.Timedelta(minutes=IST_OFFSET_MINUTES)
    t["created_ist"] = t["created_at"] + off
    t["first_response_ist"] = t["first_response_at"] + off
    t["resolved_ist"] = t["resolved_at"] + off

    # Duplicates: legacy tickets were re-imported under both source systems.
    # Pairs are identical except csat (legacy 0 = no response) -> keep the helpdesk copy.
    n_before_dedup = len(t)
    dup_ids = t.loc[t["ticket_id"].duplicated(keep=False), "ticket_id"]
    dq["duplicate_ticket_ids"] = int(dup_ids.nunique())
    t["_pref"] = (t["source_system"] != "helpdesk").astype(int)
    t = t.sort_values(["ticket_id", "_pref"]).drop_duplicates("ticket_id", keep="first")
    t = t.drop(columns="_pref")
    dq["rows_removed_as_duplicates"] = n_before_dedup - len(t)

    # CSAT: legacy rows use 0 for no response (README) -> blank.
    t.loc[(t["source_system"] == "legacy_fd") & (t["csat_score"] == 0), "csat_score"] = pd.NA

    # Response durations.
    t["frt_min"] = (t["first_response_at"] - t["created_at"]).dt.total_seconds() / 60
    dq["negative_frt"] = int((t["frt_min"] < 0).sum())
    dq["zero_frt"] = int((t["frt_min"] == 0).sum())
    dq["clean_rows"] = len(t)
    return t.reset_index(drop=True), dq


def load_clean(data_dir=DATA_DIR):
    tickets, agents = load_raw(data_dir)
    t, dq = clean_tickets(tickets)
    return t, clean_agents(agents), dq
