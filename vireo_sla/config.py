"""Client support-policy parameters supplied in the task pack."""
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Export timestamps are UTC; shifts are defined in IST.
IST_OFFSET_MINUTES = 330

# First-response targets, in minutes.
SLA_TARGET_MIN = {"chat": 15, "voice": 120, "social": 240, "email": 480}

# Store credit issued per breach, on resolution.
BREACH_CREDIT_INR = 350

# Fully loaded agent cost and shift length.
AGENT_HOUR_COST_INR = 165
SHIFT_HOURS = 8

# Shift windows in IST (start hour, end hour). Night wraps past midnight.
SHIFTS = {"Morning": (6, 14), "Day": (14, 22), "Night": (22, 6)}
SHIFT_ORDER = ["Morning", "Day", "Night"]

# Which frontline team owns first contact for each channel.
# Social is worked by Chat Frontline; overnight chat coverage is a Chat Frontline Night shift.
CHANNEL_FRONTLINE = {
    "chat": "Chat Frontline",
    "social": "Chat Frontline",
    "email": "Email Frontline",
    "voice": "Voice Frontline",
}

# Attendance = resolved or closed. Credits are issued on resolution.
ATTENDED_STATUSES = {"resolved", "closed"}

# LLM call safety (see SECURITY.md). The base URL is pinned so an inherited ANTHROPIC_BASE_URL
# (e.g. from another tool's environment) cannot route the API key to a different host.
ANTHROPIC_API_URL = "https://api.anthropic.com"
LLM_TIMEOUT_S = 30.0
LLM_MAX_RETRIES = 1                 # SDK retries per request (429/5xx/connection); attempts are also bounded
LLM_DEFAULT_REQUEST_BUDGET = 60     # max API requests per server process; override with VIREO_LLM_MAX_CALLS

# Report guardrails (our decision, see DECISIONS.md).
MIN_TICKETS_FOR_RATE = 10     # below this, a rate is shown as "low volume"
MIN_TICKETS_FOR_REVIEW = 40   # agent-review window: minimum agent-attributable tickets to flag
REVIEW_Z = 2.0                # flag only if observed breaches exceed expected by > 2 std devs
