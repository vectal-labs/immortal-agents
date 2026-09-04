# Detector behavior tests
- `test_detect_claude.py` covers every Claude evaluator decision reason.
- `test_detect_codex.py` covers every Codex evaluator decision reason.
- `test_detect_harness.py` covers Claude, Codex, Pi, and ambiguous routing.
- Fixtures are trimmed real captures, with minimal synthetic records for missing cases.
- Contract tests call detector APIs directly; remove compatibility adapters after migrations.
