@AGENTS.md

## Claude-specific notes

- Use the `prism-verify` skill (`.claude/skills/prism-verify/`) to run the full verification
  pass — mock providers, `validate_pack.py`, `smoke_test.py`, `load_test.py`, and the routing
  eval — instead of invoking the scripts one by one.
- `scripts/*.py`, `data/*`, and the `docs/` pack are read-only reference material that define
  the grading contract. Don't edit them; if a check seems wrong, flag it rather than patching it
  away.
