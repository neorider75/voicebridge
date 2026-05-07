"""Tests unitaires du handler RunPod (mockés, sans GPU).

Ces tests vérifient le routing et la structure des réponses sans charger
les modèles ML réels. Pour les tests avec GPU, voir test_integration.py.
"""
from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock, patch

import numpy as np
import pytest  # noqa: F401  (utilisé indirectement par fixtures)
import soundfile as sf


def _fake_wav_b64(duration_s: float = 1.0, sr: int = 24000) -> str:
    """Crée un WAV base64 d'audio aléatoire pour les tests."""
    audio = np.random.randn(int(duration_s * sr)).astype(np.float32) * 0.1
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ============================================================================
# Tests warmup
# ============================================================================

class TestWarmup:
    @patch("handler.get_whisper")
    @patch("handler.get_f5tts")
    @patch("handler.get_nllb")
    def test_warmup_default(self, mock_nllb, mock_f5tts, mock_whisper):
        from handler import handle_warmup
        result = handle_warmup({})
        assert result["ok"] is True
        assert "whisper" in result["loaded"]
        assert "f5tts" in result["loaded"]
        assert "nllb" in result["loaded"]
        mock_whisper.assert_called_once()
        mock_f5tts.assert_called_once()
        mock_nllb.assert_called_once()

    @patch("handler.get_whisper")
    def test_warmup_specific(self, mock_whisper):
        from handler import handle_warmup
        result = handle_warmup({"components": ["whisper"]})
        assert result["loaded"] == ["whisper"]
        mock_whisper.assert_called_once()

    @patch("handler.get_rvc_router")
    def test_warmup_rvc(self, mock_rvc):
        from handler import handle_warmup
        result = handle_warmup({"components": ["rvc"]})
        assert result["loaded"] == ["rvc"]
        mock_rvc.assert_called_once()


# ============================================================================
# Tests translate
# ============================================================================

class TestTranslate:
    @patch("handler.get_nllb")
    def test_translate_nllb(self, mock_nllb):
        mock_instance = MagicMock()
        mock_instance.translate.return_value = "Hello world"
        mock_nllb.return_value = mock_instance

        from handler import handle_translate
        result = handle_translate({
            "provider": "nllb",
            "text": "Bonjour le monde",
            "src_lang": "fr",
            "tgt_lang": "en",
        })
        assert result["translated"] == "Hello world"
        assert result["provider"] == "nllb"

    @patch("handler.get_opus_mt")
    def test_translate_opus_mt(self, mock_opus):
        mock_instance = MagicMock()
        mock_instance.translate.return_value = "Hallo Welt"
        mock_opus.return_value = mock_instance

        from handler import handle_translate
        result = handle_translate({
            "provider": "opus-mt",
            "text": "Bonjour le monde",
            "src_lang": "fr",
            "tgt_lang": "de",
        })
        assert result["translated"] == "Hallo Welt"
        assert result["provider"] == "opus-mt"

    def test_translate_empty_text(self):
        from handler import handle_translate
        result = handle_translate({
            "text": "",
            "src_lang": "fr",
            "tgt_lang": "en",
        })
        assert result["translated"] == ""

    def test_translate_same_language(self):
        from handler import handle_translate
        result = handle_translate({
            "text": "Bonjour",
            "src_lang": "fr",
            "tgt_lang": "fr",
        })
        assert result["translated"] == "Bonjour"

    def test_translate_unknown_provider(self):
        from handler import handle_translate
        result = handle_translate({
            "provider": "deepl",
            "text": "Bonjour",
            "src_lang": "fr",
            "tgt_lang": "en",
        })
        assert "error" in result


# ============================================================================
# Tests live_pipeline
# ============================================================================

class TestLivePipeline:
    @patch("handler.get_whisper")
    @patch("handler.get_f5tts")
    @patch("handler.get_nllb")
    def test_pipeline_gpu_clone(self, mock_nllb, mock_f5tts, mock_whisper):
        mock_w = MagicMock()
        mock_w.transcribe.return_value = "Bonjour"
        mock_whisper.return_value = mock_w

        mock_n = MagicMock()
        mock_n.translate.return_value = "Hello"
        mock_nllb.return_value = mock_n

        mock_f = MagicMock()
        mock_f.synthesize_streaming.return_value = iter([
            ("chunk1_b64", 0),
            ("chunk2_b64", 1),
        ])
        mock_f5tts.return_value = mock_f

        from handler import handle_live_pipeline
        messages = list(handle_live_pipeline({
            "mode": "gpu-clone",
            "audio": _fake_wav_b64(),
            "voice_ref": _fake_wav_b64(),
            "src_lang": "fr",
            "target_lang": "en",
            "translation_provider": "nllb",
        }))

        types = [m["type"] for m in messages]
        assert "transcript" in types
        assert "translated" in types
        assert types.count("audio_pcm") == 2
        assert types[-1] == "audio_end"

    @patch("handler.get_whisper")
    @patch("handler.get_f5tts")
    @patch("handler.get_nllb")
    def test_pipeline_gpu_native_requires_voice_ref(
        self, mock_nllb, mock_f5tts, mock_whisper
    ):
        """Décision 2 : gpu-native exige aussi un voice_ref (la voix native
        sélectionnée dans la lib unifiée Hostinger)."""
        mock_w = MagicMock()
        mock_w.transcribe.return_value = "Bonjour"
        mock_whisper.return_value = mock_w

        from handler import handle_live_pipeline
        messages = list(handle_live_pipeline({
            "mode": "gpu-native",
            "audio": _fake_wav_b64(),
            "src_lang": "fr",
            "target_lang": "en",
            # voice_ref MANQUANT
        }))

        # Should error
        assert any(
            m.get("type") == "error" and "voice_ref" in m.get("message", "")
            for m in messages
        )

    @patch("handler.get_whisper")
    @patch("handler.get_f5tts")
    @patch("handler.get_nllb")
    def test_pipeline_gpu_native_with_voice_ref_ok(
        self, mock_nllb, mock_f5tts, mock_whisper
    ):
        mock_w = MagicMock()
        mock_w.transcribe.return_value = "Bonjour"
        mock_whisper.return_value = mock_w

        mock_n = MagicMock()
        mock_n.translate.return_value = "Hello"
        mock_nllb.return_value = mock_n

        mock_f = MagicMock()
        mock_f.synthesize_streaming.return_value = iter([("c1", 0)])
        mock_f5tts.return_value = mock_f

        from handler import handle_live_pipeline
        messages = list(handle_live_pipeline({
            "mode": "gpu-native",
            "audio": _fake_wav_b64(),
            "voice_ref": _fake_wav_b64(),  # voix native sélectionnée
            "src_lang": "fr",
            "target_lang": "en",
            "translation_provider": "nllb",
        }))

        types = [m["type"] for m in messages]
        assert "transcript" in types
        assert "audio_pcm" in types

    @patch("handler.get_whisper")
    @patch("handler.get_f5tts")
    def test_pipeline_no_translation_when_same_lang(self, mock_f5tts, mock_whisper):
        mock_w = MagicMock()
        mock_w.transcribe.return_value = "Bonjour"
        mock_whisper.return_value = mock_w

        mock_f = MagicMock()
        mock_f.synthesize_streaming.return_value = iter([("c1", 0)])
        mock_f5tts.return_value = mock_f

        from handler import handle_live_pipeline
        messages = list(handle_live_pipeline({
            "mode": "gpu-clone",
            "audio": _fake_wav_b64(),
            "voice_ref": _fake_wav_b64(),
            "src_lang": "fr",
            "target_lang": "fr",  # même langue → pas de trad
            "translation_provider": "nllb",
        }))

        types = [m["type"] for m in messages]
        assert "translated" not in types
        assert "transcript" in types

    def test_pipeline_missing_audio(self):
        from handler import handle_live_pipeline
        messages = list(handle_live_pipeline({"mode": "gpu-clone"}))
        assert any(m.get("type") == "error" for m in messages)

    @patch("handler.get_whisper")
    def test_pipeline_hybrid_requires_voice_ref(self, mock_whisper):
        mock_w = MagicMock()
        mock_w.transcribe.return_value = "Bonjour"
        mock_whisper.return_value = mock_w

        from handler import handle_live_pipeline
        messages = list(handle_live_pipeline({
            "mode": "gpu-hybrid",
            "audio": _fake_wav_b64(),
            "src_lang": "fr",
            "target_lang": "fr",
            "rvc_model_id": "test-id",
            # voice_ref missing
        }))

        assert any(
            m.get("type") == "error" and "voice_ref" in m.get("message", "")
            for m in messages
        )

    @patch("handler.get_whisper")
    def test_pipeline_hybrid_requires_rvc_id(self, mock_whisper):
        mock_w = MagicMock()
        mock_w.transcribe.return_value = "Bonjour"
        mock_whisper.return_value = mock_w

        from handler import handle_live_pipeline
        messages = list(handle_live_pipeline({
            "mode": "gpu-hybrid",
            "audio": _fake_wav_b64(),
            "voice_ref": _fake_wav_b64(),
            "src_lang": "fr",
            "target_lang": "fr",
            # rvc_model_id missing
        }))

        assert any(
            m.get("type") == "error" and "rvc_model_id" in m.get("message", "")
            for m in messages
        )


# ============================================================================
# Tests rvc_convert
# ============================================================================

class TestRvcConvert:
    @patch("handler.get_rvc_router")
    def test_rvc_convert_success(self, mock_router):
        mock_model = MagicMock()
        mock_model.convert.return_value = "converted_b64"
        mock_router_instance = MagicMock()
        mock_router_instance.load.return_value = mock_model
        mock_router.return_value = mock_router_instance

        from handler import handle_rvc_convert
        result = handle_rvc_convert({
            "rvc_model_id": "test-id",
            "audio": _fake_wav_b64(),
            "pitch_shift": 0,
            "index_rate": 0.7,
        })
        assert result["audio"] == "converted_b64"
        assert result["model_id"] == "test-id"

    def test_rvc_convert_missing_model_id(self):
        from handler import handle_rvc_convert
        result = handle_rvc_convert({"audio": _fake_wav_b64()})
        assert "error" in result


# ============================================================================
# Test handler dispatcher
# ============================================================================

class TestHandler:
    def test_unknown_operation(self):
        from handler import handler
        # handler est un generator OU un dict selon l'op ; pour unknown_op
        # c'est un dict (non-streaming).
        result = handler({"input": {"operation": "do_unknown_thing"}})
        # handler retourne un dict pour les ops non-streaming
        assert "error" in result
        assert result["error"] == "unknown_operation"

    @patch("handler.get_whisper")
    def test_handler_warmup_routing(self, mock_whisper):
        from handler import handler
        result = handler({"input": {"operation": "warmup",
                                     "components": ["whisper"]}})
        assert result["ok"] is True

    def test_handler_live_pipeline_returns_generator(self):
        """Régression : handler() doit retourner un objet générateur pour
        live_pipeline (et pas itérer dessus). Sans ça, les `return X` des
        ops sync deviendraient des StopIteration silencieux dans le même
        scope si on ajoutait yield à handler().
        """
        import inspect
        from handler import handler
        result = handler({"input": {"operation": "live_pipeline"}})
        # live_pipeline DOIT retourner un objet générateur, jamais un dict
        assert inspect.isgenerator(result), (
            f"live_pipeline doit retourner un générateur, got {type(result)}"
        )

    def test_handler_sync_ops_return_dict_not_generator(self):
        """Régression : warmup/translate/rvc_convert/unknown DOIVENT retourner
        un dict, jamais un générateur (sinon RunPod renvoie output: [])."""
        import inspect
        from handler import handler
        for op in ("unknown_op",):
            result = handler({"input": {"operation": op}})
            assert isinstance(result, dict), (
                f"op={op} doit retourner dict, got {type(result)}"
            )
            assert not inspect.isgenerator(result)
