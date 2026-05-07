"""Tests services/progress_tasks.py — registre central + GC."""
from __future__ import annotations

import time

import pytest


def test_create_returns_task_id_with_prefix(isolated_data_dir):
    from app.services import progress_tasks as pt
    tid = pt.create("rvc_upload")
    assert tid.startswith("task_")
    snap = pt.snapshot(tid)
    assert snap["status"] == "queued"
    assert snap["progress_percent"] == 0
    assert snap["kind"] == "rvc_upload"


def test_update_progress_and_step(isolated_data_dir):
    from app.services import progress_tasks as pt
    tid = pt.create("test")
    update = pt.updater(tid)
    update(status="running", progress=42, step="Upload .pth")
    snap = pt.snapshot(tid)
    assert snap["status"] == "running"
    assert snap["progress_percent"] == 42
    assert snap["current_step"] == "Upload .pth"


def test_progress_clamped_0_100(isolated_data_dir):
    from app.services import progress_tasks as pt
    tid = pt.create("test")
    pt.update(tid, progress=150)
    assert pt.snapshot(tid)["progress_percent"] == 100
    pt.update(tid, progress=-10)
    assert pt.snapshot(tid)["progress_percent"] == 0


def test_auto_done_at_100(isolated_data_dir):
    from app.services import progress_tasks as pt
    tid = pt.create("test")
    pt.update(tid, status="running", progress=99)
    assert pt.snapshot(tid)["status"] == "running"
    pt.update(tid, progress=100)
    assert pt.snapshot(tid)["status"] == "done"


def test_error_marks_status(isolated_data_dir):
    from app.services import progress_tasks as pt
    tid = pt.create("test")
    pt.update(tid, error="boom")
    snap = pt.snapshot(tid)
    assert snap["status"] == "error"
    assert snap["error"] == "boom"


def test_snapshot_includes_elapsed_seconds(isolated_data_dir):
    from app.services import progress_tasks as pt
    tid = pt.create("test")
    time.sleep(1.05)
    snap = pt.snapshot(tid)
    assert snap["elapsed_seconds"] >= 1


def test_snapshot_missing_returns_none(isolated_data_dir):
    from app.services import progress_tasks as pt
    assert pt.snapshot("task_nonexistent") is None


def test_list_active_filters_done(isolated_data_dir):
    from app.services import progress_tasks as pt
    t1 = pt.create("a")
    t2 = pt.create("b")
    pt.update(t1, status="done", progress=100)
    active = pt.list_active()
    ids = [t["task_id"] for t in active]
    assert t1 not in ids
    assert t2 in ids


def test_details_merge(isolated_data_dir):
    """update(details=...) doit MERGER, pas remplacer."""
    from app.services import progress_tasks as pt
    tid = pt.create("test", details={"a": 1})
    pt.update(tid, details={"b": 2})
    snap = pt.snapshot(tid)
    assert snap["details"] == {"a": 1, "b": 2}


def test_result_stored(isolated_data_dir):
    from app.services import progress_tasks as pt
    tid = pt.create("test")
    pt.update(tid, status="done", progress=100,
              result={"clips_count": 142, "score": 87})
    snap = pt.snapshot(tid)
    assert snap["result"]["clips_count"] == 142
    assert snap["result"]["score"] == 87
