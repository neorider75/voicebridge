"""Tests services/rvc_models_store.py — paths Volume + CRUD metadata.

Note : test de validate_pth_file() limité aux cas qui ne nécessitent
pas torch (magic bytes, taille). Le chargement PyTorch lui-même est
testé en intégration (Phase K scénario 8).
"""
from __future__ import annotations

import pytest


def test_runpod_pth_key_format(isolated_data_dir):
    from app.services import rvc_models_store as rs
    assert rs.runpod_pth_key("rvc_abc") == "rvc_models/rvc_abc/model.pth"
    assert rs.runpod_index_key("rvc_abc") == "rvc_models/rvc_abc/added.index"


def test_runpod_keys_safe_id(isolated_data_dir):
    """safe_id doit rejeter les caractères dangereux pour S3 / FS."""
    from app.services import rvc_models_store as rs
    # safe_id whitelist [A-Za-z0-9_-] — donc / ou .. doivent lever ValueError
    with pytest.raises(ValueError):
        rs.runpod_pth_key("../escape")
    with pytest.raises(ValueError):
        rs.runpod_pth_key("model/with/slashes")


def test_add_and_get(isolated_data_dir):
    from app.services import rvc_models_store as rs
    rs.add({
        "id": "rvc_test",
        "name": "Test",
        "sample_rate": 40000,
        "f0": True,
        "version": "v2",
        "size_mb": 142.0,
        "status": "active",
    })
    fetched = rs.get("rvc_test")
    assert fetched["name"] == "Test"
    assert fetched["status"] == "active"
    assert fetched["created_at"]  # auto-populé


def test_add_id_required(isolated_data_dir):
    from app.services import rvc_models_store as rs
    with pytest.raises(ValueError, match="meta.id requis"):
        rs.add({"name": "no id"})


def test_patch_updates_subset(isolated_data_dir):
    from app.services import rvc_models_store as rs
    rs.add({"id": "rvc_x", "name": "X", "status": "uploading"})
    rs.patch("rvc_x", {"status": "active", "size_mb": 150.0})
    fetched = rs.get("rvc_x")
    assert fetched["status"] == "active"
    assert fetched["size_mb"] == 150.0
    assert fetched["name"] == "X"


def test_patch_missing_returns_none(isolated_data_dir):
    from app.services import rvc_models_store as rs
    assert rs.patch("rvc_missing", {"status": "active"}) is None


def test_list_models_sorted_by_created(isolated_data_dir):
    from app.services import rvc_models_store as rs
    import time
    rs.add({"id": "rvc_old", "name": "Old"})
    time.sleep(1.1)
    rs.add({"id": "rvc_new", "name": "New"})
    items = rs.list_models()
    assert items[0]["id"] == "rvc_new"
    assert items[1]["id"] == "rvc_old"


def test_delete_removes_entry(isolated_data_dir):
    from app.services import rvc_models_store as rs
    rs.add({"id": "rvc_del", "name": "ToDel"})
    assert rs.delete("rvc_del") is True
    assert rs.get("rvc_del") is None
    assert rs.delete("rvc_del") is False  # idempotent


def test_validate_pth_file_missing(isolated_data_dir, tmp_path):
    from app.services import rvc_models_store as rs
    with pytest.raises(ValueError, match="introuvable"):
        rs.validate_pth_file(tmp_path / "nope.pth")


def test_validate_pth_file_too_small(isolated_data_dir, tmp_path):
    from app.services import rvc_models_store as rs
    p = tmp_path / "tiny.pth"
    p.write_bytes(b"x" * 100)  # < 1024
    with pytest.raises(ValueError, match="trop petit"):
        rs.validate_pth_file(p)


def test_validate_pth_file_bad_magic(isolated_data_dir, tmp_path):
    from app.services import rvc_models_store as rs
    p = tmp_path / "bad.pth"
    p.write_bytes(b"NOTAPTH" + b"x" * 5000)  # ni "PK" ni 0x80
    with pytest.raises(ValueError, match="Magic bytes"):
        rs.validate_pth_file(p)


def test_validate_index_file_too_small(isolated_data_dir, tmp_path):
    from app.services import rvc_models_store as rs
    p = tmp_path / "tiny.index"
    p.write_bytes(b"x" * 50)  # < 100
    with pytest.raises(ValueError, match="trop petit"):
        rs.validate_index_file(p)


def test_validate_index_file_ok(isolated_data_dir, tmp_path):
    from app.services import rvc_models_store as rs
    p = tmp_path / "ok.index"
    p.write_bytes(b"FAKE_FAISS" + b"x" * 1000)
    result = rs.validate_index_file(p)
    assert result["valid"] is True
    assert result["size_bytes"] > 100
