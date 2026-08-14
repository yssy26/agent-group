# agent-group

A configurable three-role engineering workflow coordinated by [Herdr](https://herdr.dev/).

The workflow separates **technical judgment**, **independent review**, **implementation**, and **authoritative command-line validation**:

```text
User / project state
        |
        v
Lead (plan)
        |
        v
Reviewer ---- REVISE ----> Lead
        |
     APPROVED
        |
        v
SHA-256 sealed task
        |
        v
Executor (implementation)
        |
        v
Herdr test pane (sealed tests)
        |
        v
Lead (independent assessment)
```

The hard rule is unchanged: **Executor is never called unless Reviewer has approved the exact task that will be executed.** The approved task now also contains the exact authoritative `test_plan`.

## Model-agnostic roles

Each role has independent `kind`, `model`, `profile`, and launch arguments in `config/agents.toml`. The default configuration is:

```text
Lead      = Codex    / GPT-5.6 Sol
Reviewer  = OpenCode / DeepSeek V4 Pro
Executor  = OpenCode / DeepSeek V4 Flash
```

Roles are not tied to those models. You can swap a role's runner or model without changing orchestration logic.

Example runtime override:

```bash
uv run --python 3.11 python scripts/launch.py \
  --project ~/TO-ANISOTROPIC \
  --lead-kind opencode \
  --lead-model deepseek/deepseek-v4-pro \
  --reviewer-kind codex \
  --reviewer-model gpt-5.6-sol
```

Model strings are deliberately not validated against a hard-coded allow-list; the selected CLI/provider is the source of truth.

## Repository layout

```text
agent-group/
├── config/agents.toml
├── roles/
│   ├── lead.md
│   ├── reviewer.md
│   └── executor.md
├── policies/
│   ├── workflow.md
│   └── git-policy.md
├── opencode/agents/
│   ├── agent-group-lead.md
│   ├── agent-group-reviewer.md
│   └── agent-group-executor.md
├── templates/
│   ├── PROJECT_CHARTER.md
│   └── CURRENT_STATE.md
├── scripts/
│   ├── common.py
│   ├── doctor.py
│   ├── init_project.py
│   ├── install_opencode_profiles.py
│   ├── launch.py
│   ├── orchestrate.py
│   ├── round_prompts.py
│   ├── structured_protocol.py
│   └── test_pane.py
└── tests/
    └── test_orchestrate_protocol.py
```

## 1. Prerequisites

Run Herdr, Codex, and OpenCode in the same Linux/WSL environment. Install the Herdr integrations:

```bash
herdr integration install codex
herdr integration install opencode
herdr integration status
```

For OpenCode, confirm provider/model identifiers with:

```bash
opencode models
```

The workflow scripts require Python 3.11+ (`tomllib`). On Ubuntu 20.04, using `uv` avoids replacing the system Python:

```bash
uv python install 3.11
```

## 2. Install OpenCode role profiles

```bash
cd ~/agent-group
uv run --python 3.11 python scripts/install_opencode_profiles.py
```

The profiles contain no model IDs. They enforce role permissions independently of model selection:

- Lead: read-only inspection;
- Reviewer: read-only inspection;
- Executor: edits allowed, publishing/destructive Git denied.

## 3. Configure models and workflow controls

Edit `config/agents.toml` when needed. Important workflow settings include:

```toml
[workflow]
max_review_cycles = 3
prompt_timeout_ms = 300000
read_lines = 500
structured_repair_attempts = 1
test_output_tail_lines = 40
test_stop_on_failure = true
```

Default roles:

```toml
[roles.lead]
kind = "codex"
model = "gpt-5.6-sol"

[roles.reviewer]
kind = "opencode"
model = "deepseek/deepseek-v4-pro"

[roles.executor]
kind = "opencode"
model = "deepseek/deepseek-v4-flash"
```

## 4. Initialize a target Git project

```bash
uv run --python 3.11 python scripts/init_project.py \
  --project ~/TO-ANISOTROPIC
```

This creates the local ignored runtime directory:

```text
.agent-runtime/
├── PROJECT_CHARTER.md
├── CURRENT_STATE.md
└── ...
```

`PROJECT_CHARTER.md` defines long-lived project goals, invariants, forbidden behavior, and validation philosophy. `CURRENT_STATE.md` records the confirmed current state and next focus. `.agent-runtime/` is added to `.git/info/exclude`, so the target repository's tracked `.gitignore` is not changed.

## 5. Check prerequisites

```bash
uv run --python 3.11 python scripts/doctor.py \
  --project ~/TO-ANISOTROPIC
```

Do not launch until `Doctor: OK`.

## 6. Launch Herdr workspace and roles

First inspect the resolved launch configuration:

```bash
uv run --python 3.11 python scripts/launch.py \
  --project ~/TO-ANISOTROPIC \
  --dry-run
```

Then launch:

```bash
uv run --python 3.11 python scripts/launch.py \
  --project ~/TO-ANISOTROPIC
```

The launcher creates four panes:

```text
┌──────────────────────┬──────────────────────┐
│ Lead                 │ Reviewer             │
│ coding agent         │ coding agent         │
├──────────────────────┼──────────────────────┤
│ Test pane            │ Executor             │
│ ordinary shell       │ coding agent         │
└──────────────────────┴──────────────────────┘
```

Live agent names, pane IDs, models, and `test_pane_id` are written to `.agent-runtime/session.json`. After a computer reboot, launch again because the persisted session file cannot resurrect dead Herdr processes.

## 7. Run one controlled engineering round

```bash
uv run --python 3.11 python scripts/orchestrate.py \
  --project ~/TO-ANISOTROPIC
```

One round is:

1. Lead inspects project evidence and proposes a structured task plus exact `test_plan`.
2. Reviewer audits the task, scope, acceptance criteria, and test commands.
3. `REVISE` returns the critical issues to Lead; the loop is bounded.
4. `APPROVED` causes the exact task (including tests) to be canonicalized and SHA-256 sealed.
5. Executor receives only the sealed implementation task.
6. Executor implements the approved scope and may run cheap developer checks.
7. The orchestrator independently executes the sealed `test_plan` in the ordinary Herdr test pane using `pane run` + `pane wait-output`.
8. Full test logs and exit codes are written to the round directory and summarized in `TEST_PANE_REPORT.json`.
9. Lead independently inspects the actual diff, test-pane evidence, and regressions.
10. Lead returns `PASS`, `FAIL`, or `INCONCLUSIVE`; the orchestrator renders the next `CURRENT_STATE.md`.

## Herdr-native structured transport

Agent communication remains Herdr-native:

```text
herdr agent prompt --wait
        ↓
herdr agent read --source recent-unwrapped
        ↓
strict JSON validation
```

The structured protocol was hardened for full-screen TUI agents:

- response schema examples use `<AGENT_GROUP_SCHEMA>` so prompt history cannot masquerade as an answer;
- the real answer uses one compact `<AGENT_GROUP_JSON>...</AGENT_GROUP_JSON>` block;
- every turn carries a unique `response_nonce`, preventing stale terminal history from satisfying a new turn;
- narrative fields use arrays of short strings rather than long multiline strings;
- literal newlines inside JSON strings are forbidden;
- a malformed response receives at most the configured number of **JSON-only repair** attempts, with explicit instructions not to re-analyze or change the technical conclusion;
- persistent malformed output stops the round with parser diagnostics rather than guessing intent.

This keeps Herdr `prompt/wait/read` as the primary control plane while making the structured hand-off substantially more robust.

## Dedicated test pane

The test pane is deliberately **not** an LLM agent. It is a deterministic execution surface with zero LLM token cost.

The approved Lead task contains entries such as:

```json
{
  "name": "unit tests",
  "command": "python3 -m unittest -v",
  "timeout_ms": 120000
}
```

After Executor returns, the orchestrator runs each exact command in the test pane, waits for a unique completion marker, captures the exit code, and tees the full output to `.agent-runtime/rounds/<round>/TEST_*.log`.

Unsafe validation commands are rejected before execution, including publishing/destructive Git operations, `sudo`, shutdown/reboot/mkfs, and destructive root deletion patterns.

For OpenFOAM projects, this pane is the preferred place for deterministic work such as `wmake`, solver runs, serial/parallel regression checks, finite-difference probes, and Python post-processing gates.

## Hard controls

- **Reviewer gate:** Executor cannot run without `APPROVED`.
- **Exact-task approval:** the approved task and `test_plan` share one SHA-256 seal.
- **Lead/Reviewer read-only fingerprint:** if either role changes production files, orchestration aborts and preserves evidence.
- **No self-certification:** Executor's `PASS` is implementation status, not the authoritative validation verdict.
- **No automatic reset:** unexpected repository changes are preserved for inspection.
- **One round at a time:** unattended multi-round automation must be built above this state machine, not by weakening it.

## Protocol regression tests

Run:

```bash
uv run --python 3.11 python -m unittest -v tests/test_orchestrate_protocol.py
```

The tests cover compact JSON parsing, stale-response nonces, literal-newline rejection, test-pane completion-marker safety, and rejection of publishing commands in an approved test plan.
