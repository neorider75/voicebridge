"""Tests services/briefings_store.py — CRUD JSON + validations."""
from __future__ import annotations

import pytest


def test_create_minimal(isolated_data_dir):
    from app.services import briefings_store as bs
    b = bs.create("CODIR mensuel", "Réunion CODIR Limagrain, agenda mensuel")
    assert b["id"].startswith("br_")
    assert b["name"] == "CODIR mensuel"
    assert b["content"].startswith("Réunion")
    assert b["created_at"]
    assert b["updated_at"] == b["created_at"]


def test_list_sorted_by_updated_desc(isolated_data_dir):
    from app.services import briefings_store as bs
    import time
    b1 = bs.create("Premier", "")
    time.sleep(1.1)  # garantit timestamps différents
    b2 = bs.create("Second", "")
    items = bs.list_briefings()
    assert items[0]["id"] == b2["id"]
    assert items[1]["id"] == b1["id"]


def test_get_existing_and_missing(isolated_data_dir):
    from app.services import briefings_store as bs
    b = bs.create("X", "y")
    assert bs.get(b["id"])["name"] == "X"
    assert bs.get("br_nonexistent") is None


def test_update_partial(isolated_data_dir):
    from app.services import briefings_store as bs
    b = bs.create("A", "old content")
    updated = bs.update(b["id"], content="new content")
    assert updated["name"] == "A"  # inchangé
    assert updated["content"] == "new content"
    assert updated["updated_at"] >= b["created_at"]


def test_update_missing_returns_none(isolated_data_dir):
    from app.services import briefings_store as bs
    assert bs.update("br_missing", name="X") is None


def test_delete(isolated_data_dir):
    from app.services import briefings_store as bs
    b = bs.create("ToDel", "")
    assert bs.delete(b["id"]) is True
    assert bs.get(b["id"]) is None
    assert bs.delete(b["id"]) is False  # idempotent


def test_validation_name_empty(isolated_data_dir):
    from app.services import briefings_store as bs
    with pytest.raises(ValueError, match="name requis"):
        bs.create("", "content")
    with pytest.raises(ValueError, match="name requis"):
        bs.create("   ", "content")


def test_validation_name_too_long(isolated_data_dir):
    from app.services import briefings_store as bs
    with pytest.raises(ValueError, match="trop long"):
        bs.create("x" * 200, "content")


def test_validation_content_too_long(isolated_data_dir):
    from app.services import briefings_store as bs
    with pytest.raises(ValueError, match="trop long"):
        bs.create("Name", "x" * (bs.MAX_CONTENT_LEN + 1))


def test_unicode_safe(isolated_data_dir):
    from app.services import briefings_store as bs
    b = bs.create("Réunion CODIR éàü 🎤", "Contexte spécial 中文")
    fetched = bs.get(b["id"])
    assert fetched["name"] == "Réunion CODIR éàü 🎤"
    assert fetched["content"] == "Contexte spécial 中文"
