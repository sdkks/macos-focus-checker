# Project guide

This is a small read-only macOS focus diagnostic tool, using Python 3.14 and
PyObjC Cocoa/Quartz. `main.py` owns the monitor and JSON/JSONL/text output;
`test_main.py` uses local fakes without switching apps or observing global input.
`scripts/validate_commit_msg.py` and `test_workflow.py` cover developer tooling.

- Use `make setup` for a fresh checkout; it creates a seeded `venv`, installs
  pinned requirements, and installs both Git hooks. It refuses a conflicting
  existing environment without replacing it. See README.md before migration.
- Use `make test`, `make lint`, and `make format-check`, or `make check` for all
  three. `make format` applies formatting. Keep changes small and verified.
- `make install-hooks` uses the existing environment. Pre-commit runs Gitleaks,
  Ruff lint/import checks, Ruff format checks, and unittest. Commit-msg validates
  Conventional Commit subjects. Fix failed checks; never bypass gates.
- Use subjects such as `feat: add output`, `fix(monitor): handle missing metadata`,
  or `docs(): clarify setup`; optional `!` marks breaking changes. Do not add AI
  attribution trailers to agent-authored commits.
- Keep internal tracking IDs/workflow labels, local usernames/hostnames, absolute
  developer paths, secrets, and real incident logs out of source, public docs,
  and commit messages. Use fabricated fixtures. Never read or modify private
  logs or local environment/index files as part of development.
- The monitor's intentional runtime OS/app-version diagnostics are accepted
  behavior, not permission to disclose unrelated host details. Do not change
  focus/input behavior, permissions, or running processes during tooling work.
