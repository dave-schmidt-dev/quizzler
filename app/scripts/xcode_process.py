"""Bounded process-group runner for attended Xcode archive commands."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable


def run_grouped_xcode(
    arguments: list[str], *, cwd: Path, timeout: int, on_progress: Callable[[], None],
) -> subprocess.CompletedProcess[str]:
    """Run the Xcode wrapper in its own group and reap it on timeout or cancel."""
    proc = subprocess.Popen(
        arguments, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    started = time.monotonic()
    try:
        while True:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, timeout)
            try:
                stdout, stderr = proc.communicate(timeout=min(20, remaining))
                return subprocess.CompletedProcess(arguments, proc.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if time.monotonic() - started >= timeout:
                    raise
                on_progress()
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        raise
