"""Tests services/translation_router.py — dispatch + fallback (mockés)."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest


def test_list_providers_returns_5(isolated_data_dir):
    from app.services import translation_router as tr
    providers = tr.list_providers()
    ids = [p["id"] for p in providers]
    assert ids == ["opus-mt-cpu", "opus-mt-gpu", "nllb",
                   "gpt-4o-mini", "gpt-4o"]


def test_list_providers_marks_unavailable_without_config(isolated_data_dir):
    """Sans clé RunPod ni OpenAI, seul opus-mt-cpu doit être available."""
    from app.services import translation_router as tr
    providers = {p["id"]: p for p in tr.list_providers()}
    assert providers["opus-mt-cpu"]["available"] is True
    assert providers["opus-mt-gpu"]["available"] is False
    assert providers["nllb"]["available"] is False
    assert providers["gpt-4o-mini"]["available"] is False
    assert providers["gpt-4o"]["available"] is False


def test_list_providers_nllb_has_license_note(isolated_data_dir):
    from app.services import translation_router as tr
    nllb = next(p for p in tr.list_providers() if p["id"] == "nllb")
    assert nllb["license_note"] == "CC-BY-NC"


def test_get_default_provider_fallback(isolated_data_dir):
    from app.services import translation_router as tr
    # Aucun default défini dans config → opus-mt-cpu
    assert tr.get_default_provider() == "opus-mt-cpu"


def test_translate_same_lang_passthrough(isolated_data_dir):
    from app.services import translation_router as tr
    res = tr.translate("Bonjour", "fr", "fr", provider="opus-mt-cpu")
    assert res.translated == "Bonjour"
    assert res.latency_ms == 0


def test_translate_empty_text_passthrough(isolated_data_dir):
    from app.services import translation_router as tr
    res = tr.translate("", "fr", "en")
    assert res.translated == ""


def test_translate_unknown_provider_fallback(isolated_data_dir):
    """Avec fallback=True, un provider inconnu retombe sur opus-mt-cpu."""
    from app.services import translation_router as tr
    with patch("app.services.translation.translate") as mock_t:
        mock_t.return_value = "Hello"
        res = tr.translate("Bonjour", "fr", "en",
                           provider="invalid-xyz", fallback=True)
        assert res.provider == "opus-mt-cpu"
        assert res.translated == "Hello"


def test_translate_unknown_provider_no_fallback_raises(isolated_data_dir):
    from app.services import translation_router as tr
    with pytest.raises(ValueError):
        tr.translate("Bonjour", "fr", "en",
                     provider="invalid-xyz", fallback=False)


def test_dispatch_opus_mt_cpu(isolated_data_dir):
    from app.services import translation_router as tr
    with patch("app.services.translation.translate") as mock_t:
        mock_t.return_value = "Hallo Welt"
        res = tr.translate("Bonjour le monde", "fr", "de",
                           provider="opus-mt-cpu")
        assert res.provider == "opus-mt-cpu"
        assert res.translated == "Hallo Welt"
        assert res.cost_eur == 0.0


def test_dispatch_opus_mt_gpu_uses_runpod_worker(isolated_data_dir):
    """opus-mt-gpu doit appeler runpod_client.runsync avec operation=translate
    et provider=opus-mt (pas opus-mt-gpu)."""
    from app.services import translation_router as tr
    with patch("app.services.runpod_client.runsync") as mock_rs:
        mock_rs.return_value = {"translated": "Hallo"}
        res = tr.translate("Bonjour", "fr", "de", provider="opus-mt-gpu",
                           fallback=False)
        assert mock_rs.called
        call_args = mock_rs.call_args[0][0]
        assert call_args["operation"] == "translate"
        assert call_args["provider"] == "opus-mt"  # mapping
        assert call_args["src_lang"] == "fr"
        assert call_args["tgt_lang"] == "de"
        assert res.provider == "opus-mt-gpu"


def test_dispatch_nllb(isolated_data_dir):
    from app.services import translation_router as tr
    with patch("app.services.runpod_client.runsync") as mock_rs:
        mock_rs.return_value = {"translated": "こんにちは"}
        res = tr.translate("Bonjour", "fr", "ja", provider="nllb",
                           fallback=False)
        assert res.provider == "nllb"
        assert res.translated == "こんにちは"


def test_dispatch_gpt_uses_openai_one_shot(isolated_data_dir):
    from app.services import translation_router as tr
    from app.services import openai_client
    fake_result = openai_client.TranslationResult(
        translated="Hello", provider="gpt-4o-mini",
        src="fr", tgt="en", latency_ms=600,
        input_tokens=10, output_tokens=2, cost_eur=0.0001,
    )
    with patch("app.services.openai_client.translate_one_shot",
                return_value=fake_result) as mock_oai:
        res = tr.translate("Bonjour", "fr", "en", provider="gpt-4o-mini",
                           briefing="Réunion CODIR", fallback=False)
        assert mock_oai.called
        # Vérifie que le briefing est bien passé
        kwargs = mock_oai.call_args.kwargs
        assert kwargs["briefing"] == "Réunion CODIR"
        assert res.provider == "gpt-4o-mini"
        assert res.cost_eur == 0.0001


def test_fallback_when_provider_raises(isolated_data_dir):
    """Si nllb plante, fallback=True doit retomber sur opus-mt-cpu."""
    from app.services import translation_router as tr
    with patch("app.services.runpod_client.runsync") as mock_rs, \
         patch("app.services.translation.translate") as mock_cpu:
        mock_rs.side_effect = RuntimeError("RunPod down")
        mock_cpu.return_value = "Hello"
        res = tr.translate("Bonjour", "fr", "en",
                           provider="nllb", fallback=True)
        assert res.provider == "opus-mt-cpu"
        assert res.translated == "Hello"
