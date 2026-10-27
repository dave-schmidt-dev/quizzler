"""Tests for scripts/ingest_issue_reports.py issue report ingestion."""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "ingest_issue_reports.py"
FIXTURE_PATH = ROOT / "protocol-fixtures" / "issue-inbox-v1.json"

spec = importlib.util.spec_from_file_location("ingest_issue_reports", SCRIPT_PATH)
ingest_mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ingest_mod)


class IngestIssueReportsBaseTest(unittest.TestCase):
    """Base setup providing isolated temporary directories."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.courses_root = self.base_path / "question-packs"
        self.feedback_root = self.base_path / ".logs" / "feedback"
        self.ledger_path = self.feedback_root / ".ingested-issues.json"
        self.log_dir = self.base_path / ".logs"

        self.courses_root.mkdir(parents=True, exist_ok=True)
        self.feedback_root.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        for h in list(ingest_mod._logger.handlers):
            ingest_mod._logger.removeHandler(h)
            h.close()
        self.temp_dir.cleanup()

    def make_course(self, course_id: str) -> Path:
        """Create a mock course directory with _course.json."""
        course_dir = self.courses_root / course_id
        course_dir.mkdir(parents=True, exist_ok=True)
        course_file = course_dir / "_course.json"
        course_file.write_text(json.dumps({"course_id": course_id, "title": course_id}), encoding="utf-8")
        return course_dir

    def run_ingest(
        self,
        sources: list[Path],
        dry_run: bool = False,
        summary: bool = False,
        debug: bool = False,
    ) -> int:
        """Invoke ingest_mod.ingest with test directories."""
        return ingest_mod.ingest(
            sources=sources,
            courses_root=self.courses_root,
            feedback_root=self.feedback_root,
            ledger_path=self.ledger_path,
            log_dir=self.log_dir,
            dry_run=dry_run,
            summary=summary,
            debug=debug,
        )

    def run_cli_subprocess(self, extra_args: list[str]) -> subprocess.CompletedProcess[str]:
        """Invoke scripts/ingest_issue_reports.py via subprocess."""
        cmd = [
            sys.executable,
            str(SCRIPT_PATH),
            "--courses-root",
            str(self.courses_root),
            "--feedback-root",
            str(self.feedback_root),
            "--ledger",
            str(self.ledger_path),
            "--log-dir",
            str(self.log_dir),
            *extra_args,
        ]
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
