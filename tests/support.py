"""Keep integration tests away from the installed watcher's files."""
import tempfile
from pathlib import Path
from unittest import mock

from immortal.core import logbook, native_recovery, notify


def isolate_state(test):
    root = Path(test.enterContext(tempfile.TemporaryDirectory()))
    test.enterContext(mock.patch.multiple(logbook, STATE_DIR=root,
        STATE_PATH=root / 'state.json', LOG_PATH=root / 'watcher.log'))
    test.enterContext(mock.patch.object(native_recovery, '_homes', return_value=[]))
    test.enterContext(mock.patch.object(notify, 'webhook_url', return_value=None))
    return root
