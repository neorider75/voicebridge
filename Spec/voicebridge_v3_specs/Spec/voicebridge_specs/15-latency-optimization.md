# 15 - Latency Optimization

> **Document V3 nouveau.** Catalogue exhaustif des optimisations de latence pour le pipeline live multilingue.

## Contexte

Le live multilingue avec clonage de voix est intrinsèquement séquentiel : capture → STT → traduction → TTS → restitution. Chaque étape ajoute de la latence. L'objectif V3 est d'atteindre **~1 seconde de latence perçue** (premier son) pour une expérience fluide en réunion Teams.

## Cible et budget de latence

### Budget V3 par étape (mode "Multilingue ma voix")

| Étape | Budget | Comment l'atteindre |
|---|---|---|
| Capture micro Mac (PyAudio) | 50ms | Inévitable |
| WebSocket Mac → Hostinger Paris | 10ms | Hostinger en France |
| Silero VAD | 5ms | Léger, déjà optimal |
| Buffer fin de souffle | 250ms | Tuning silence threshold |
| HTTPS Hostinger Paris → RunPod EU-FR-1 | 10ms | Datacenter France ↔ France |
| Endpoint unifié RunPod | | Pipeline cascade dans 1 seul container |
| ├ Whisper Distil-Large | 180ms | Modèle distillé, FP16 |
| ├ NLLB-200 distilled 1.3B | 150ms | Quantization INT8, batch=1 |
| └ F5-TTS streaming (premier chunk) | 200ms | Streaming activé |
| HTTPS retour | 10ms | |
| WebSocket → Mac | 10ms | |
| BlackHole + Teams | 80ms | Inévitable |
| **TOTAL latence perçue** | **~975ms** | |

### Cible mode "Hybride accent natif" (avec RVC)

```
... idem ci-dessus
+ RVC inference: 150ms
TOTAL: ~1.1s
```

## Catalogue des optimisations

### Catégorie A : Réseau (étapes 2, 5, 9, 10)

#### A1. Hostinger en France (Paris)
- **Impact** : -100 à -200ms vs Hostinger en Lituanie/UK
- **Effort** : trivial (changement de DC dans hPanel)
- **Coût** : 0€

#### A2. RunPod datacenter EU-FR-1
- **Impact** : -30 à -100ms vs EU-RO-1 ou EU-NL-1
- **Effort** : configuration endpoint
- **Coût** : 0€

#### A3. WebSocket binaire (msgpack au lieu de JSON+Base64)
- **Impact** : -20 à -50ms par chunk (audio en binaire pur)
- **Effort** : modéré (modifier `live.py` côté backend et `live-worklet.js` côté frontend)
- **Coût** : 0€

```python
# Avant : JSON + base64
await ws.send_json({"type": "audio_pcm", "data": base64.b64encode(pcm).decode(), "seq": seq})

# Après : binary frame
import msgpack
await ws.send_bytes(msgpack.packb({"type": "audio_pcm", "data": pcm, "seq": seq}))
```

```javascript
// Frontend : décodeur msgpack
import * as msgpack from 'msgpack-lite';

ws.onmessage = (event) => {
  if (event.data instanceof ArrayBuffer) {
    const msg = msgpack.decode(new Uint8Array(event.data));
    // ...
  }
};
```

#### A4. HTTP/2 keep-alive Hostinger ↔ RunPod
- **Impact** : -30 à -80ms par appel (pas de re-handshake TLS)
- **Effort** : trivial (httpx[http2])
- **Coût** : 0€

```python
# services/runpod_client.py
self.client = httpx.AsyncClient(
    http2=True,
    limits=httpx.Limits(
        max_keepalive_connections=20,
        max_connections=100,
        keepalive_expiry=300,
    ),
)
```

#### A5. Compression brotli/deflate
- **Impact** : -10 à -30ms (audio + texte)
- **Effort** : trivial (config Nginx + websockets)
- **Coût** : 0€

```nginx
# nginx config
brotli on;
brotli_comp_level 4;
brotli_types text/plain application/json;

gzip on;
gzip_types text/plain application/json;
```

#### A6. TCP_NODELAY activé
- **Impact** : -10 à -30ms
- **Effort** : trivial
- **Coût** : 0€

```python
# Sur les sockets WebSocket et HTTP
import socket
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
```

### Catégorie B : VAD et chunking (étape 4)

#### B1. Silence threshold réduit
- **Impact** : -150ms
- **Effort** : trivial (1 paramètre)
- **Risque** : possibilité de couper des phrases naturellement pausées

```python
# routes/live.py
SILENCE_FLUSH_TICKS = 8  # 250ms au lieu de 13 (400ms)
```

#### B2. Min speech duration réduit
- **Impact** : -50ms
- **Effort** : trivial

```python
SPEECH_MIN_TICKS = 12  # 400ms au lieu de 16 (500ms)
```

#### B3. VAD prédictif (V3.5)
- **Impact** : -100ms supplémentaires
- **Effort** : élevé (model ML séparé pour prédire la fin de phrase)
- **Coût** : calcul léger

#### B4. Streaming partiel STT
- **Impact** : -200ms
- **Effort** : très élevé (whisper-streaming)
- **À considérer V3.5**

### Catégorie C : Modèles ML

#### C1. Whisper Distil-Large-V3 (au lieu de Large V3)
- **Impact** : -100 à -200ms
- **Effort** : trivial (changement de modèle)
- **Compromis** : qualité quasi identique (-1% WER environ)
- **Coût** : 0€

```python
# runpod-worker/models/whisper.py
MODEL_ID = "distil-whisper/distil-large-v3"  # au lieu de "openai/whisper-large-v3"
```

#### C2. CTranslate2 pour Whisper (faster-whisper)
- **Impact** : -100 à -150ms
- **Effort** : modéré (nouvelle API)
- **Coût** : 0€

```python
from faster_whisper import WhisperModel

class WhisperSTT:
    def __init__(self):
        self.model = WhisperModel(
            "distil-large-v3",
            device="cuda",
            compute_type="float16",
            download_root="/runpod-volume/hf-cache",
        )
    
    def transcribe(self, audio, language):
        segments, info = self.model.transcribe(
            audio, language=language, beam_size=1,
            vad_filter=False,  # déjà fait côté Hostinger
        )
        return " ".join([s.text for s in segments])
```

#### C3. NLLB-200 distilled 600M (au lieu de 1.3B)
- **Impact** : -50 à -100ms
- **Effort** : trivial
- **Compromis** : qualité un peu moindre
- **Coût** : 0€

⚠️ Recommandé seulement si la qualité reste acceptable. Tests à faire.

#### C4. Quantization INT8 sur NLLB
- **Impact** : -30 à -80ms
- **Effort** : modéré
- **Coût** : 0€

```python
from transformers import AutoModelForSeq2SeqLM, BitsAndBytesConfig

bnb_config = BitsAndBytesConfig(load_in_8bit=True)
model = AutoModelForSeq2SeqLM.from_pretrained(
    "facebook/nllb-200-distilled-1.3B",
    quantization_config=bnb_config,
)
```

#### C5. F5-TTS streaming (chunks audio progressifs)
- **Impact** : -200 à -400ms (latence perçue, premier son)
- **Effort** : modéré (configurer streaming dans F5-TTS)
- **Coût** : 0€

C'est probablement l'optimisation avec le meilleur ROI : **-300ms gratuit**.

```python
class F5TTS:
    def synthesize_streaming(self, text, voice_ref, language):
        # F5-TTS supporte le streaming via .generate(streaming=True)
        # Yield chunks 100-200ms au fur et à mesure
        for chunk in self.model.generate(
            text=text, ref_audio=voice_ref, language=language,
            streaming=True, chunk_size_ms=200,
        ):
            yield chunk
```

#### C6. ref_codes pré-encodés
- **Impact** : -50 à -100ms
- **Effort** : déjà fait pour NeuTTS, à étendre F5-TTS
- **Coût** : 0€

Pour chaque voix utilisateur, pré-encoder le `ref_codes` au moment de l'ajout (pas à chaque inférence).

#### C7. Compilation TorchScript / Torch Compile
- **Impact** : -50 à -100ms
- **Effort** : modéré
- **Coût** : 0€

```python
# Au chargement du modèle
model = torch.compile(model, mode="reduce-overhead")
```

#### C8. xFormers attention
- **Impact** : -50 à -100ms (sur attention layers)
- **Effort** : trivial (install + flag)
- **Coût** : 0€

### Catégorie D : Architecture

#### D1. Endpoint unifié RunPod
- **Impact** : -100 à -200ms
- **Effort** : modéré (1 seul container Docker au lieu de 3)
- **Coût** : 0€ (économie même)

Au lieu de 3 appels séquentiels Hostinger → RunPod (STT + Trad + TTS), un seul appel qui chaîne en interne.

```
AVANT (3 round-trips):
Hostinger → RunPod (STT) → Hostinger → RunPod (Trad) → Hostinger → RunPod (TTS) → Hostinger

APRÈS (1 round-trip):
Hostinger → RunPod (STT + Trad + TTS) → Hostinger (streaming)
```

#### D2. Connection keep-alive
- **Impact** : -30 à -80ms
- **Effort** : trivial
- **Coût** : 0€

#### D3. Pre-warming prédictif
- **Impact** : -200ms (élimine cold start sur premier appel)
- **Effort** : modéré
- **Coût** : minimal

Bouton manuel "🔥 Préchauffer GPU" + auto-warmup 30s avant ouverture WS.

#### D4. Pipeline parallèle (V3.5)
- **Impact** : -300 à -600ms
- **Effort** : très élevé
- **Coût** : 0€

Au lieu d'attendre que la phrase 1 soit synthétisée pour traiter la phrase 2, traiter en parallèle.

```
Phrase 1: STT|Trad|TTS|TTS|TTS|...
Phrase 2:    |STT|Trad|TTS|TTS|...   ← démarre dès que phrase 1 a fini son STT
Phrase 3:        |STT|Trad|...
```

Risque : ordre des phrases si une plus rapide finit avant. Nécessite un sequencer.

### Catégorie E : Infrastructure GPU

#### E1. Active worker pendant horaires bureau
- **Impact** : -2 à -10s (élimine cold start premier appel de la journée)
- **Effort** : trivial (config RunPod)
- **Coût** : ~60€/mois (10h/jour × 22 jours × 0.24€)

```python
# Programmable via RunPod API
schedule = {
    "weekdays": {
        "09:00-18:00": {"min_workers": 1},
        "rest": {"min_workers": 0},
    },
}
```

#### E2. GPU H100 vs RTX 4090
- **Impact** : -100 à -300ms
- **Effort** : trivial (changer le type GPU)
- **Coût** : +50% du tarif horaire

Pour usage perso JC, **non justifié**. RTX 4090 suffit largement.

#### E3. GPU avec plus de VRAM (charger plusieurs modèles)
- **Impact** : -50 à -150ms (pas de eviction LRU)
- **Effort** : trivial (RTX 4090 24Go OK)
- **Coût** : 0€

#### E4. Network Volume sur SSD NVMe
- **Impact** : -50ms cold start
- **Effort** : déjà inclus
- **Coût** : 0€

### Catégorie F : Mode FR pur sans traduction (optimisation spécifique)

Si l'utilisateur reste en FR pur (mode V1 conservé), on peut court-circuiter beaucoup d'étapes. Mais c'est CPU donc lent.

⚠️ **Non recommandé en V3 pour le live** : on a vu que CPU = 5-15s. Le mode CPU reste seulement pour le mode fichier.

## Plan d'implémentation des optimisations

### Phase A : Optimisations gratuites (à activer dès la V3 launch)

| Optimisation | Gain estimé |
|---|---|
| A1. Hostinger Paris | -100ms |
| A2. RunPod EU-FR-1 | -50ms |
| A4. HTTP/2 keep-alive | -50ms |
| B1. VAD silence 250ms | -150ms |
| C1. Whisper Distil-Large | -150ms |
| C5. F5-TTS streaming | -300ms |
| D1. Endpoint unifié | -150ms |
| **Total gain Phase A** | **-950ms** |

Permet d'atteindre **~1s de latence perçue**.

### Phase B : Optimisations modérées (V3.0.1)

| Optimisation | Gain estimé |
|---|---|
| A3. WebSocket binaire | -50ms |
| C2. Faster-Whisper | -100ms |
| C7. TorchCompile | -50ms |
| D3. Pre-warming prédictif | -200ms (premier appel) |
| **Total gain Phase B** | **-200 à -400ms** |

Permet d'atteindre **~700-800ms de latence perçue**.

### Phase C : Optimisations avancées (V3.5)

| Optimisation | Gain estimé |
|---|---|
| B4. Streaming STT | -200ms |
| D4. Pipeline parallèle | -300ms |
| **Total gain Phase C** | **-500ms** |

Permet d'atteindre **~500ms de latence perçue** (proche du temps réel humain).

## Métriques à monitorer

### Côté Hostinger (logs)

```python
# Pour chaque flush, logger :
log.info("live: chunk processed", extra={
    "session_id": ...,
    "mode": ...,
    "stt_ms": stt_duration,
    "translate_ms": translate_duration,
    "tts_first_chunk_ms": tts_first_chunk_duration,
    "tts_total_ms": tts_total_duration,
    "rvc_ms": rvc_duration,  # si mode hybrid
    "round_trip_ms": total_duration,
})
```

Aggrégé dans un dashboard Settings → Diagnostics.

### Côté frontend (UI)

Affichage en mode debug :
```
Latence perçue : 987ms
- Buffer VAD : 250ms
- Réseau : 22ms
- Pipeline GPU : 615ms (Whisper 180 + NLLB 145 + F5-TTS first 290)
- Retour + scheduling : 100ms
```

### Alertes

Si latence > 2s sur 3 chunks consécutifs : warning "Latence élevée, vérifie ta connexion".

## Tests de performance

```python
# tests/perf_pipeline.py
@pytest.mark.perf
async def test_pipeline_latency_under_1500ms():
    """Le pipeline live doit rester sous 1.5s en moyenne."""
    audio_b64 = load_test_audio("hello_world_2sec.wav")
    
    durations = []
    for _ in range(10):
        t0 = time.time()
        async for msg in runpod_client.live_pipeline(
            audio_b64, "gpu-clone", "fr", "en", voice_ref_b64=...
        ):
            if msg["type"] == "audio_pcm" and msg.get("seq") == 0:
                durations.append(time.time() - t0)
                break
    
    avg = sum(durations) / len(durations)
    assert avg < 1.5, f"Latence moyenne {avg:.2f}s > 1.5s"
```

## Monitoring continu

Dashboard Settings → Diagnostics → Latence :

```
[Graphique 7 derniers jours]
P50: 920ms
P95: 1.4s
P99: 2.1s

[Détail par étape, médiane]
- Buffer VAD     : 250ms
- Whisper STT    : 178ms
- NLLB Trad      : 142ms
- F5-TTS first   : 287ms
- Réseau         : 65ms

[Cold starts détectés]
Aujourd'hui : 1 cold start (12s) à 09:14
Hier        : 2 cold starts (avg 8.5s)
```

## Conclusions

L'objectif **~1s de latence perçue** est atteignable dès la V3 launch avec les optimisations gratuites de Phase A. Au-delà, des gains supplémentaires sont possibles mais avec un effort/risque croissant.

**Recommandation pour V3 launch** : implémenter Phase A complète. Phase B en V3.0.1 si besoin. Phase C reportée à V3.5.
