# Managed Codex recovery
- `__init__.py` owns explicit opt-in, transactional selection, status, and rollback.
- `package.py` validates the release manifest, downloads, and extracts verified packages.
- `activation.py` owns only this component's launcher and marked shell-profile blocks.
- `verification.py` performs explicit bounded login-shell, BB launcher, and native-process checks. A selected launcher is not proof that every cached BB thread uses it.
- Production requires a validated, signed, notarized release. Local manifests are explicit developer-only inputs.
- Never replace package-manager launchers, alter credentials, kill agent processes, or change network/power settings.
- Test with temporary homes and loopback or file fixtures. No test writes to the real home.
