"""Keep integration tests away from the installed watcher's files."""
import tempfile
from pathlib import Path
from unittest import mock

from immortal.core import logbook


def isolate_state(test):
    root = Path(test.enterContext(tempfile.TemporaryDirectory()))
    test.enterContext(mock.patch.multiple(logbook, STATE_DIR=root,
        STATE_PATH=root / 'state.json', LOG_PATH=root / 'watcher.log'))
    return root
