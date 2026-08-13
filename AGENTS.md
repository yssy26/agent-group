# AGENTS.md

This repository defines a model-agnostic multi-agent engineering workflow.

## Invariants for changes to this repository

1. Do not bind the Lead, Reviewer, or Executor role definition to a specific model vendor or model ID.
2. Keep runner/model selection in `config/agents.toml`, environment variables, or CLI overrides.
3. Reviewer approval must remain a hard prerequisite for Executor invocation.
4. Approval must remain bound to the exact approved-task hash.
5. Lead and Reviewer must remain production-read-only roles.
6. Executor must not receive Git publishing or destructive-history authority.
7. Do not add automatic `git reset --hard`, `git clean`, commit, push, merge, rebase, or cherry-pick behavior to the orchestrator.
8. Review loops must remain bounded and escalate rather than force approval.
9. Structured-output parse failure must stop the workflow rather than infer intent.
10. Preserve one-round-at-a-time behavior unless a higher-level multi-round controller retains all existing gates.

## Code style

- Python 3.11+.
- Standard library preferred.
- Scripts must work from any current working directory.
- Do not assume a particular target repository name.
- Avoid hard-coded absolute paths.
- Keep runtime state under the target project's configured runtime directory.
- Preserve user dirty-worktree evidence.

## Testing changes

At minimum:

```bash
python -m py_compile scripts/*.py
python scripts/doctor.py --help
python scripts/init_project.py --help
python scripts/launch.py --help
python scripts/orchestrate.py --help
```

For workflow changes, use a disposable toy Git repository before applying them to a real engineering project.
