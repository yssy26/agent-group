# Git policy

This workflow is designed to preserve evidence and prevent agents from publishing or destroying work without explicit human authorization.

## Lead

Allowed:

- `git status`;
- `git diff`;
- `git log`;
- `git show`;
- read-only inspection.

Forbidden:

- editing production files;
- commit;
- push;
- merge;
- rebase;
- cherry-pick;
- destructive reset/restore/clean.

## Reviewer

Same Git restrictions as Lead.

Reviewer is a plan auditor, not a code author.

## Executor

Allowed:

- edit files within the approved scope;
- compile and test;
- inspect `git status` and `git diff`.

Forbidden:

- `git commit`;
- `git push`;
- `git merge`;
- `git rebase`;
- `git cherry-pick`;
- `git reset --hard`;
- `git clean`;
- bulk restore/checkout intended to discard evidence.

## Orchestrator

The orchestrator does not commit or push.

It records evidence under `.agent-runtime/`, which should be excluded locally through `.git/info/exclude`.

## Dirty worktrees

A target repository may already contain user changes.

The workflow must not reset or overwrite them.

Lead should treat existing modifications as part of the observed project state unless the project charter says otherwise.

If an Executor task would touch pre-existing user changes in a way that cannot be safely isolated, it should report `BLOCKED`.
