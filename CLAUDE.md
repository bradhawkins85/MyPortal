# MyPortal

The project rules live in `.github/agents.md` and apply to every change:

@.github/agents.md

## Claude Code notes

- New SQL migration: use `/new-migration`. It takes the next number free on
  both this branch and `origin/main`, and writes the required phase metadata
  header. The migration runner refuses new files without it.
- Change log entry: use `/changelog-entry`. It writes the GUID-named
  `changes/*.json` file with a correct `content_hash`; never hand-compute it.
- Run the smallest relevant test target, e.g. `pytest tests/test_change_log_service.py -q`.
  Tests marked `browser_gate` need a staged deployment and are not part of a
  normal local run.
- The `rmm/` and `tray/` agents are Go modules with their own build lifecycle;
  see their READMEs before changing them.
