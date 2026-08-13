# agent-group

A configurable three-role engineering workflow for coding agents, coordinated by [Herdr](https://herdr.dev/).

The workflow separates **technical judgment**, **independent review**, and **implementation**:

```text
User
  |
  v
Lead
  |
  | draft plan
  v
Reviewer
  |\
  | \-- REVISE --> Lead --> Reviewer
  |
  \---- APPROVED
          |
          v
       Executor
          |
          v
        Lead
   final evidence review
```

The key rule is a hard gate: **the Executor is never called unless the Reviewer has approved the exact task that will be executed.**

## Model-agnostic by design

Roles are not tied to one model or one CLI.

Each role has independent `kind`, `model`, `profile`, and launch arguments in `config/agents.toml`. You can therefore use, for example:

- Lead = Codex / GPT-5.6
- Reviewer = OpenCode / DeepSeek V4 Pro
- Executor = OpenCode / DeepSeek V4 Flash

and later switch to:

- Lead = OpenCode / DeepSeek
- Reviewer = Codex / another model
- Executor = OpenCode / another model

without changing the orchestration logic.

Runtime overrides are also supported:

```bash
python scripts/launch.py \
  --project ~/TO-ANISOTROPIC \
  --lead-kind opencode \
  --lead-model deepseek/deepseek-v4-pro \
  --reviewer-kind codex \
  --reviewer-model gpt-5.6 \
  --executor-kind opencode \
  --executor-model deepseek/deepseek-v4-flash
```

Model strings are intentionally **not validated against a hard-coded allow-list**. The selected CLI/provider remains the source of truth.

Changing a role's `kind` or `model` affects the **next launch**. An already-running interactive agent keeps its current model/session; launch a new Herdr session to change it cleanly.

## Repository layout

```text
agent-group/
├── config/
│   └── agents.toml
├── roles/
│   ├── lead.md
│   ├── reviewer.md
│   └── executor.md
├── policies/
│   ├── workflow.md
│   └── git-policy.md
├── opencode/
│   └── agents/
│       ├── agent-group-lead.md
│       ├── agent-group-reviewer.md
│       └── agent-group-executor.md
├── templates/
│   ├── PROJECT_CHARTER.md
│   └── CURRENT_STATE.md
└── scripts/
    ├── doctor.py
    ├── install_opencode_profiles.py
    ├── init_project.py
    ├── launch.py
    └── orchestrate.py
```

## 1. Prerequisites

Install Herdr on the host/WSL environment where the agents run, then install the relevant integrations:

```bash
herdr integration install codex
herdr integration install opencode
```

Install/configure whichever agent CLIs and model providers you intend to use.

For OpenCode, confirm available model identifiers with:

```bash
opencode models
```

Herdr passes arguments after `--` directly to the selected agent CLI. OpenCode accepts models in `provider/model` form via `-m` / `--model`.

## 2. Clone this workflow repo

```bash
git clone https://github.com/yssy26/agent-group.git
cd agent-group
```

## 3. Install the optional OpenCode role profiles

These profiles enforce role-level permissions independently of the model:

```bash
python scripts/install_opencode_profiles.py
```

They contain **no model setting**. This is deliberate: model selection remains in `config/agents.toml` or CLI overrides.

The profiles enforce:

- Lead: no edits; safe inspection only.
- Reviewer: no edits; safe inspection only.
- Executor: edits allowed; destructive/publishing Git commands denied.

If a role is run with a non-OpenCode agent, the orchestration policy still applies, but tool-level permission enforcement depends on that agent's own permission system.

## 4. Configure role runners and models

Edit `config/agents.toml`:

```toml
[roles.lead]
kind = "codex"
model = "gpt-5.6"

[roles.reviewer]
kind = "opencode"
model = "deepseek/deepseek-v4-pro"

[roles.executor]
kind = "opencode"
model = "deepseek/deepseek-v4-flash"
```

You may change each role independently at any time.

## 5. Initialize a target project

```bash
python scripts/init_project.py --project ~/TO-ANISOTROPIC
```

This creates a local, ignored runtime directory:

```text
.agent-runtime/
├── PROJECT_CHARTER.md
├── CURRENT_STATE.md
└── ...
```

Fill in `PROJECT_CHARTER.md` and, when useful, seed `CURRENT_STATE.md`.

The runtime directory is added to `.git/info/exclude`, so it does not modify the target project's tracked `.gitignore`.

## 6. Launch the three agents through Herdr

```bash
python scripts/launch.py --project ~/TO-ANISOTROPIC
```

The launcher:

1. creates a Herdr workspace,
2. creates Lead / Reviewer / Executor panes plus a normal test pane,
3. starts each configured agent with its configured model,
4. writes the resolved agent names, pane IDs, kinds, and models to `.agent-runtime/session.json`.

The role names used by Herdr include a timestamp, so relaunching with different models does not collide with a still-live previous session.

Use `--dry-run` to inspect the resolved configuration without starting anything.

## 7. Run exactly one controlled development round

```bash
python scripts/orchestrate.py --project ~/TO-ANISOTROPIC
```

A round is:

1. Lead inspects the project and returns a structured draft task.
2. Reviewer audits the draft.
3. If `REVISE`, the review is returned to Lead for revision.
4. The review loop runs at most `max_review_cycles`.
5. If `APPROVED`, the orchestrator canonicalizes and SHA-256 seals the approved task.
6. Executor receives only that sealed approved task.
7. Executor implements and tests.
8. Lead independently reviews the actual repository state and execution evidence.
9. The orchestrator writes the final assessment and updated current-state summary.
10. The process stops.

By default, this repository intentionally runs **one engineering round at a time**.

## Hard controls

### Reviewer gate

`Executor` is called only when:

```text
review.verdict == "APPROVED"
```

If the maximum review cycles are exhausted, the round becomes `ESCALATED` and execution is blocked.

### Approval hash

The approved task is canonicalized and hashed. Before Executor is called, the hash is recomputed. Any mismatch aborts execution and requires another review.

### Read-only Lead/Reviewer check

Before and after Lead/Reviewer turns, the orchestrator fingerprints the target Git working tree. If either role changes production files, the workflow aborts. It does **not** auto-reset or destroy evidence.

### No self-certification

- Reviewer decides whether a plan is safe/reasonable to execute.
- Executor reports what it changed and what tests did.
- Lead decides what the evidence means after execution.
- Executor cannot declare the overall algorithm scientifically correct.

## Configuration priority

For runner/model selection:

```text
CLI override
    >
environment variable
    >
config/agents.toml
```

Environment variables:

```text
AG_LEAD_KIND
AG_LEAD_MODEL
AG_REVIEWER_KIND
AG_REVIEWER_MODEL
AG_EXECUTOR_KIND
AG_EXECUTOR_MODEL
```

Examples:

```bash
AG_LEAD_KIND=opencode \
AG_LEAD_MODEL=deepseek/deepseek-v4-pro \
python scripts/launch.py --project ~/my-project
```

## Safety philosophy

The orchestrator never runs `git reset --hard`, `git clean`, `git commit`, or `git push`.

If something unexpected happens, it stops and preserves the working tree for inspection.

See:

- `policies/workflow.md`
- `policies/git-policy.md`
- `roles/*.md`

## Current scope

This first version is intentionally conservative:

- persistent interactive agents are managed by Herdr;
- one round is executed per command;
- reviewer approval is mandatory;
- maximum review cycles are bounded;
- model selection is runtime-configurable;
- report exchange uses machine-readable JSON sentinels;
- runtime evidence stays inside the target project's ignored `.agent-runtime/`.

Once this baseline is stable on a small test repository, multi-round automation can be added without changing the role model.
