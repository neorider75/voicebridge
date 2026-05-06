"""Tests pour utils/audio.py."""
import numpy as np

from utils.audio import (
    decode_wav_b64, encode_wav_b64, encode_pcm_b64,
    resample_linear, chunk_audio, normalize_peak,
)


def test_encode_decode_roundtrip():
    """Encoder puis décoder doit donner un audio quasi identique."""
    audio = np.random.randn(24000).astype(np.float32) * 0.5
    b64 = encode_wav_b64(audio, sample_rate=24000)
    decoded, sr = decode_wav_b64(b64)
    assert sr == 24000
    # PCM_16 introduit une petite perte mais le RMS doit être proche
    assert abs(np.sqrt(np.mean(audio ** 2)) - np.sqrt(np.mean(decoded ** 2))) < 0.01


def test_resample_double_length():
    audio = np.array([0.0, 1.0, 0.0, -1.0], dtype=np.float32)
    out = resample_linear(audio, 16000, 32000)
    assert len(out) == 8


def test_resample_same_rate_passthrough():
    audio = np.random.randn(100).astype(np.float32)
    out = resample_linear(audio, 16000, 16000)
    np.testing.assert_array_equal(audio, out)


def test_chunk_audio():
    audio = np.arange(1000)
    chunks = list(chunk_audio(audio, 256))
    assert len(chunks) == 4  # 256, 256, 256, 232
    assert len(chunks[0]) == 256
    assert len(chunks[-1]) == 232


def test_normalize_peak():
    audio = np.array([0.1, -0.2, 0.5, -0.3], dtype=np.float32)
    out = normalize_peak(audio, target_db=-3.0)
    target_linear = 10 ** (-3.0 / 20)
    assert abs(np.max(np.abs(out)) - target_linear) < 1e-5


def test_normalize_peak_zero_audio():
    audio = np.zeros(100, dtype=np.float32)
    out = normalize_peak(audio)
    np.testing.assert_array_equal(audio, out)


def test_encode_pcm_b64_no_header():
    """encode_pcm_b64 ne doit PAS contenir de header WAV."""
    import base64
    audio = np.zeros(100, dtype=np.float32)
    b64 = encode_pcm_b64(audio, sample_rate=24000)
    raw = base64.b64decode(b64)
    assert raw[:4] != b"RIFF"
    assert len(raw) == 200  # 100 samples × 2 bytes (int16)
