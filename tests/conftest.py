# -*- coding: utf-8 -*-
"""pytest configuration / shared fixtures for the WhisperMark 2.0 QA suite."""
import os
import socket
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# This sandbox denies writes to the platform temp dir; keep pytest tmp dirs
# inside the workspace so `python -m pytest tests -q` works out of the box.
_QA_TMP = ROOT / ".qa-pytest-tmp"
_QA_TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = os.environ["TMPDIR"] = str(_QA_TMP)
tempfile.tempdir = str(_QA_TMP)

from wm.engine.state import state_manager  # noqa: E402


@pytest.fixture(scope="session")
def tmp_path_factory():
    """Workspace-local replacement for pytest's tmp_path_factory.

    The platform temp dir is not fully writable in this sandbox (pytest's
    chmod/lock dance fails with WinError 5), so keep temp files in the workspace.
    The run root is wiped at session start so repeated `pytest` runs are clean.
    """
    import shutil

    root = _QA_TMP / "run"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)

    class _Factory:
        counter = 0

        def mktemp(self, basename, numbered=True):
            _Factory.counter += 1
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in basename)[:60]
            path = root / f"{_Factory.counter:03d}-{safe}"
            path.mkdir(parents=True, exist_ok=True)
            return path

    return _Factory()


@pytest.fixture
def tmp_path(tmp_path_factory, request):
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in request.node.name)[:60]
    return tmp_path_factory.mktemp(safe)


def _clear_listeners():
    with state_manager._notify_lock:
        state_manager._listeners.clear()


@pytest.fixture(autouse=True)
def clean_state():
    """Give every test a pristine global StateManager (records/errors/listeners)."""
    _clear_listeners()
    state_manager.reset()
    yield
    _clear_listeners()
    state_manager.reset()


@pytest.fixture
def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@pytest.fixture
def occupied_port():
    """A port that is definitely already bound/listening (for bind-failure tests)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        yield s.getsockname()[1]
    finally:
        s.close()
