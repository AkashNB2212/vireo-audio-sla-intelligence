# Security

This records a defensive review of the repository: what was found, what was fixed, and what still carries risk. It does not claim the app is breach-proof. Status: **no known critical or high findings**.

## Public repository and confidential data

- The client task pack is **not committed**. That covers ticket free text, customer, order and product data, the employee roster and client-internal documents. `.gitignore` excludes `data/*` except `data/README.md`, which only explains where to put the pack.
- Generated `outputs/` are **not committed**: they contain per-agent reports and ticket-level validation samples.
- Client-facing deliverables with detailed results are kept out of the repository.
- The source holds no client figures, names or IDs. The app's finding text is derived from the data at runtime. The only client parameters in code are the policy constants the calculations need (SLA targets, credit per breach, cost per agent-hour, shift hours, channel ownership), in `vireo_sla/config.py`.

## Architecture and attack surface

| Aspect | Reality |
|---|---|
| Deployment | Local Streamlit app, one process, run by the user. Served on `localhost` only (`.streamlit/config.toml`). |
| Users / login | None. Anyone who can reach the port can use every page. |
| Storage | Read-only CSV files in `data/`. No database. Writes go only to `outputs/`, from the export script. |
| User input | Streamlit widgets only. None of them reach a file path, shell, query or URL. There is no file upload. |
| Network | One outbound HTTPS call to the Anthropic Messages API, for the optional weekly briefing. |
| Code execution | No `eval`, `exec`, `subprocess`, `pickle`, YAML, SQL or dynamic imports. `bandit` reports 0 issues. |

## Findings and fixes

| # | Severity | Finding | Fix | Regression test |
|---|---|---|---|---|
| 1 | Medium | Streamlit listens on **all network interfaces** by default, which exposed this login-free app to the local network. | `server.address = "localhost"`. Verified: the socket listens on `127.0.0.1` only. | config guard test |
| 2 | Medium | The Anthropic SDK honours an inherited `ANTHROPIC_BASE_URL`, which could route the API key to another host. | `base_url` pinned to `https://api.anthropic.com`. | `test_client_pinned_to_official_api_with_timeout` |
| 3 | Low | Model output is rendered as markdown, so links, images or HTML could appear. | The prompt forbids them, and the grounding check rejects them. | `test_links_images_html_in_model_output_are_rejected` (5 cases) |
| 4 | Low | Roster labels copied into the fact sheet could carry instructions and whitelist digits in the grounding check. | `safe_label()` replaces non-label text with the agent ID or "Unknown". | `test_poisoned_roster_label_cannot_whitelist_numbers` |
| 5 | Low | API error text was shown unredacted. | `redact_secrets()`. Output echoing a credential is rejected. | `test_api_error_message_is_redacted`, `test_model_output_echoing_a_credential_is_rejected` |
| 6 | Low | No explicit timeout (SDK default: 10 minutes, 2 retries). | 30 s timeout, 1 retry. At most 3 attempts, only on HTTP 400. | `test_client_pinned_to_official_api_with_timeout` |
| 7 | Low | No overall cap on paid calls; failed calls were retried on every click. | A process-wide request budget (`VIREO_LLM_MAX_CALLS`, default 60). Skipped or failed calls are not cached; paid results are cached per week. | `test_request_budget_caps_paid_calls`, `test_app_does_not_cache_a_skipped_call`, `test_app_does_not_repeat_paid_call_on_rerun` |
| 8 | Low | Spreadsheet formula injection from untrusted customer and agent text. | `csv_safe()` on every download, the drill-down table and all outputs. Numbers are untouched. | `test_csv_safe_neutralises_formulas_without_touching_numbers`, `test_exported_reports_have_no_live_formula_cells` |
| 9 | Low | Stack traces, with local paths, were shown in the browser. | `client.showErrorDetails = "none"`. | config guard test |
| 10 | Low | One malformed timestamp stopped the whole report. | The row is quarantined and counted. | `test_malformed_timestamps_are_quarantined_not_fatal` |
| 11 | Low | The data checks re-read the raw CSV on every click. | Cached. | headless app run |
| 12 | Low | The venv's bootstrap pip and setuptools had known advisories. | Upgraded; the README upgrades pip first. | `pip-audit` |
| 13 | Info | Dependencies were unbounded; usage telemetry was on. | Major-version bounds plus `requirements-lock.txt`; telemetry off. | `pip-audit`, config guard |
| — | Accepted | `unsafe_allow_html=True` on the class legend. | It renders constant strings only. | — |

## Secrets handling

- API credentials are read **only from environment variables**, and only by the Anthropic client. They are never written to files, logs, the UI or the request body.
- `.gitignore` excludes `.env*`, `.streamlit/secrets.toml` and key files.
- The test sentinel "key" is assembled at runtime, so no key-shaped literal exists in the source.

## LLM security

- **Data minimisation.** The model receives only the aggregate weekly fact sheet: counts, rates and totals, plus the names of agents flagged by the 26-week review. It receives no customer names, IDs, ticket or order IDs, or message text. This is proven by a test.
- **Prompt injection.** Ticket text never enters the prompt (tested with injected text). FACTS are marked as data. Roster labels are sanitised.
- **Grounding.** Mandatory. Every violation falls back to the deterministic template.
- **Output.** Untrusted text, never executed, rendered only after the checks pass.

## Authentication, authorisation, RLS and IDOR

**RLS/IDOR testing is not applicable to the current local Streamlit architecture.** There are no users, login, database or ID-based routes. The protection is serving on localhost only. Before any shared deployment, put the app behind authentication.

## Tools run

- `pip-audit`: 0 known vulnerabilities.
- `bandit`: 0 issues.
- `detect-secrets`: no findings.
- A regex sweep for keys, tokens, private keys, local paths, and client and personal identifiers.
- An `lsof` bind check.
- `pytest`.

Not available locally: gitleaks, trufflehog, semgrep.

## Remaining risks

1. **No authentication.** If someone overrides the bind address or deploys the app, it is open to whoever can reach it.
2. **Grounding checks form, not meaning.** A misleading sentence built only from allowed numbers can still pass.
3. **Point-in-time audit.** Re-run `pip-audit` before each release; the lock file targets Python 3.11.
4. **The request budget is in memory, per process.** It is not per user.
