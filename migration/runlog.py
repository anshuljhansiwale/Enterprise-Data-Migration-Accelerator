"""Log each approved spec hash with the tests and results that ran for it."""

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from migration.load import prepare


_state = {"installed": False}


def log_run(spec, test_paths, cases, *, root, logged_at=None, output=""):
    """Write runs/<specHash>/<loggedAt>/ for one approved spec. A new hash is a new directory."""
    prepare(spec)
    digest = spec["approval"]["specHash"]
    stamp = logged_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    folder = Path(root) / "runs" / digest / stamp
    copied = []
    payloads = []
    for path in test_paths:
        source = Path(path)
        payloads.append((source.name, source.read_text(encoding="utf-8")))
        copied.append(source.name)
    summary = {
        "specHash": digest,
        "loggedAt": stamp,
        "testFiles": copied,
        "testsRun": len(cases),
        "passed": sum(1 for case in cases if case["status"] == "passed"),
        "failures": sum(1 for case in cases if case["status"] == "failed"),
        "errors": sum(1 for case in cases if case["status"] == "error"),
        "skipped": sum(1 for case in cases if case["status"] == "skipped"),
        "ok": bool(cases) and all(case["status"] == "passed" for case in cases),
        "cases": cases,
    }
    text = output or _render(summary)
    folder.mkdir(parents=True)
    (folder / "spec-hash.txt").write_text(digest + "\n", encoding="utf-8")
    (folder / "approved-spec.json").write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tests_dir = folder / "tests"
    tests_dir.mkdir()
    for name, payload in payloads:
        (tests_dir / name).write_text(payload, encoding="utf-8")
    (folder / "results.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (folder / "results.txt").write_text(text, encoding="utf-8")
    return folder


def record_result(result, spec_path, test_paths, root):
    """Log LoadTests cases from a finished unittest result. Other suites are ignored."""
    selected = [
        test_id
        for test_id in getattr(result, "_migration_events", [])
        if _is_load_case(test_id)
    ]
    if not selected:
        return None
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    cases = _cases(result, selected)
    return log_run(spec, test_paths, cases, root=root, output=_render_cases(cases))


def install(spec_path, test_paths, root):
    """After the load tests finish, write their run folder."""
    if _state["installed"]:
        return
    _state["installed"] = True
    _state["spec_path"] = spec_path
    _state["test_paths"] = list(test_paths)
    _state["root"] = root
    original_start = unittest.TestResult.startTest
    original_stop = unittest.TestResult.stopTestRun

    def startTest(self, test):
        events = getattr(self, "_migration_events", None)
        if events is None:
            events = []
            self._migration_events = events
        events.append(test.id())
        return original_start(self, test)

    def stopTestRun(self):
        original_stop(self)
        if os.environ.get("MIGRATION_RUNLOG") == "0" or getattr(self, "_migration_logged", False):
            return
        self._migration_logged = True
        try:
            record_result(self, _state["spec_path"], _state["test_paths"], _state["root"])
        except Exception as error:
            print(f"run log was not written: {error}", file=sys.stderr)

    unittest.TestResult.startTest = startTest
    unittest.TestResult.stopTestRun = stopTestRun


def _is_load_case(test_id):
    return ".LoadTests." in test_id


def _cases(result, selected):
    failed = {test.id(): trace for test, trace in getattr(result, "failures", [])}
    errored = {test.id(): trace for test, trace in getattr(result, "errors", [])}
    skipped = {test.id(): reason for test, reason in getattr(result, "skipped", [])}
    cases = []
    for test_id in selected:
        if test_id in errored:
            cases.append({"id": test_id, "status": "error", "detail": errored[test_id]})
        elif test_id in failed:
            cases.append({"id": test_id, "status": "failed", "detail": failed[test_id]})
        elif test_id in skipped:
            cases.append({"id": test_id, "status": "skipped", "detail": str(skipped[test_id])})
        else:
            cases.append({"id": test_id, "status": "passed"})
    return cases


def _render_cases(cases):
    passed = sum(1 for case in cases if case["status"] == "passed")
    failed = sum(1 for case in cases if case["status"] == "failed")
    errors = sum(1 for case in cases if case["status"] == "error")
    skipped = sum(1 for case in cases if case["status"] == "skipped")
    ok = bool(cases) and passed == len(cases)
    lines = [f"{case['id']} ... {case['status']}" for case in cases]
    for case in cases:
        detail = case.get("detail")
        if detail:
            lines.extend(["", str(detail).rstrip()])
    lines.extend(
        [
            "",
            f"Ran {len(cases)} tests",
            f"passed {passed}, failures {failed}, errors {errors}, skipped {skipped}",
            "",
            "OK" if ok else "FAILED",
            "",
        ]
    )
    return "\n".join(lines)


def _render(summary):
    return _render_cases(summary["cases"])
