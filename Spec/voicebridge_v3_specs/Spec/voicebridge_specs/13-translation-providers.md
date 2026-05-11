# 13 - Translation Providers (multi-providers)

> **Document V3 nouveau.** Architecture multi-providers de traduction pour VoiceBridge.

## Vue d'ensemble

V1 utilise un seul provider de traduction : **OPUS-MT FR↔EN** sur Hostinger CPU. V3 ajoute un système modulaire qui permet à l'utilisateur de choisir parmi 6 providers selon ses besoins.

## Les 6 providers disponibles

| ID | Nom | Localisation | Latence | Qualité | Coût | Multi-langues |
|---|---|---|---|---|---|---|
| `opus-mt-cpu` | OPUS-MT CPU (V1 existant) | Hostinger CPU | 200-800ms | Bonne | 0€ | FR↔EN |
| `opus-mt-gpu` | OPUS-MT GPU (V3 nouveau) | RunPod GPU | 50-150ms | Bonne | Inclus GPU | FR↔EN, FR↔DE, FR↔ES, FR↔IT |
| `nllb` | NLLB-200 distilled 1.3B (V3 nouveau) | RunPod GPU | 100-300ms | Très bonne | Inclus GPU | 200+ langues |
| `gpt-4o-mini` | GPT-4o-mini (V3 nouveau) | OpenAI cloud | 300-800ms | Excellente | ~0.04€/1000 trad | Universel |
| `gpt-4o` | GPT-4o (V3 nouveau) | OpenAI cloud | 600-1500ms | Excellente++ | ~0.40€/1000 trad | Universel + glossaire |
| `libretranslate` | LibreTranslate (V3 nouveau, fallback) | Hostinger CPU | 200-1000ms | Moyenne | 0€ | Multi-paires limitées |

## Architecture modulaire

### Service router

```python
# Site/backend/app/services/translation_router.py
"""Routeur multi-providers de traduction.

Architecture :
- Interface uniforme TranslationProvider (ABC)
- Sélection dynamique selon config user ou override par appel
- Fallback automatique si provider principal en erreur
- Métriques de coût par provider
- Cache des dernières traductions (LRU, optionnel)
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from .. import config

log = logging.getLogger("voicebridge.translation_router")


class TranslationProvider(ABC):
    """Interface commune à tous les providers."""

    @property
    @abstractmethod
    def id(self) -> str:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def is_local(self) -> bool:
        """True si le provider tourne sur Hostinger (pas d'appel externe)."""
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """Vérifie que le provider est utilisable (clé API, modèle chargé...)."""
        ...

    @abstractmethod
    def supports_pair(self, src: str, tgt: str) -> bool:
        ...

    @abstractmethod
    async def translate(self, text: str, src: str, tgt: str,
                         glossary: dict | None = None,
                         context: list[dict] | None = None) -> str:
        ...

    @abstractmethod
    def estimate_cost(self, text: str) -> float:
        """Coût estimé en EUR pour traduire `text`. 0 si gratuit."""
        ...


class TranslationRouter:
    """Sélection et orchestration des providers."""

    def __init__(self):
        self._providers: dict[str, TranslationProvider] = {}
        self._register_providers()

    def _register_providers(self):
        from .translation_providers import (
            OpusMtCpuProvider, OpusMtGpuProvider, NllbProvider,
            Gpt4oMiniProvider, Gpt4oProvider, LibreTranslateProvider,
        )
        for cls in (OpusMtCpuProvider, OpusMtGpuProvider, NllbProvider,
                    Gpt4oMiniProvider, Gpt4oProvider, LibreTranslateProvider):
            p = cls()
            self._providers[p.id] = p

    def list_providers(self) -> list[dict]:
        """Liste les providers avec leur disponibilité."""
        return [
            {
                "id": p.id,
                "name": p.name,
                "is_local": p.is_local,
                "available": True,  # to be checked async
            }
            for p in self._providers.values()
        ]

    async def translate(
        self,
        text: str,
        src: str,
        tgt: str,
        provider_id: str | None = None,
        glossary: dict | None = None,
        context: list[dict] | None = None,
        fallback: bool = True,
    ) -> dict:
        """Traduit avec le provider demandé, fallback si erreur.
        
        Returns:
            { "translated": str, "provider": str, "duration_ms": int, "cost_eur": float }
        """
        if not text or not text.strip():
            return {"translated": text, "provider": "noop", "duration_ms": 0, "cost_eur": 0}
        if src == tgt:
            return {"translated": text, "provider": "noop", "duration_ms": 0, "cost_eur": 0}

        # Provider à utiliser
        provider_id = provider_id or config.get("translation_default_provider", "opus-mt-cpu")
        provider = self._providers.get(provider_id)
        if not provider:
            log.warning("Unknown provider %s, fallback to opus-mt-cpu", provider_id)
            provider = self._providers["opus-mt-cpu"]

        # Vérification supports_pair
        if not provider.supports_pair(src, tgt):
            log.warning("Provider %s ne supporte pas %s→%s", provider_id, src, tgt)
            if fallback:
                # Trouver un provider qui supporte la paire
                for p in self._providers.values():
                    if p.supports_pair(src, tgt) and await p.is_available():
                        provider = p
                        break
                else:
                    raise RuntimeError(f"Aucun provider disponible pour {src}→{tgt}")

        # Tentative
        t0 = time.time()
        try:
            translated = await provider.translate(text, src, tgt, glossary, context)
            duration_ms = int((time.time() - t0) * 1000)
            cost = provider.estimate_cost(text)
            log.info("translation: %s %s→%s %dms %.4f€",
                     provider.id, src, tgt, duration_ms, cost)
            return {
                "translated": translated,
                "provider": provider.id,
                "duration_ms": duration_ms,
                "cost_eur": cost,
            }
        except Exception as e:
            log.warning("Provider %s failed: %s", provider.id, e)
            if not fallback:
                raise
            # Fallback : essai le prochain disponible
            for fb_id in ["opus-mt-cpu", "libretranslate", "nllb"]:
                if fb_id == provider.id:
                    continue
                fb = self._providers.get(fb_id)
                if fb and fb.supports_pair(src, tgt) and await fb.is_available():
                    log.info("Fallback vers %s", fb_id)
                    return await self.translate(text, src, tgt, provider_id=fb_id,
                                                  glossary=glossary, context=context,
                                                  fallback=False)
            raise


_router = None


def get_router() -> TranslationRouter:
    global _router
    if _router is None:
        _router = TranslationRouter()
    return _router
```

### Implémentations des providers

```python
# Site/backend/app/services/translation_providers/__init__.py
from .opus_mt_cpu import OpusMtCpuProvider
from .opus_mt_gpu import OpusMtGpuProvider
from .nllb import NllbProvider
from .gpt_4o_mini import Gpt4oMiniProvider
from .gpt_4o import Gpt4oProvider
from .libretranslate import LibreTranslateProvider
```

#### OPUS-MT CPU (V1 existant, à adapter)

```python
# Site/backend/app/services/translation_providers/opus_mt_cpu.py
"""Wrapper du service translation.py existant V1."""
from ..translation import translate as _translate_v1
from .. import translation as trans_svc
from .base import TranslationProvider


class OpusMtCpuProvider(TranslationProvider):
    @property
    def id(self) -> str:
        return "opus-mt-cpu"

    @property
    def name(self) -> str:
        return "OPUS-MT CPU (Hostinger)"

    @property
    def is_local(self) -> bool:
        return True

    async def is_available(self) -> bool:
        return True  # toujours dispo (modèles téléchargés à la 1ere utilisation)

    def supports_pair(self, src: str, tgt: str) -> bool:
        return (src, tgt) in trans_svc._MODEL_NAMES

    async def translate(self, text, src, tgt, glossary=None, context=None) -> str:
        # Wrapper sync → async
        import asyncio
        return await asyncio.to_thread(_translate_v1, text, src, tgt)

    def estimate_cost(self, text: str) -> float:
        return 0.0
```

#### OPUS-MT GPU (V3)

```python
# Site/backend/app/services/translation_providers/opus_mt_gpu.py
from .base import TranslationProvider
from ..runpod_client import get_client


class OpusMtGpuProvider(TranslationProvider):
    @property
    def id(self) -> str:
        return "opus-mt-gpu"

    @property
    def name(self) -> str:
        return "OPUS-MT GPU (RunPod)"

    @property
    def is_local(self) -> bool:
        return False

    async def is_available(self) -> bool:
        client = get_client()
        if not await client.is_configured():
            return False
        health = await client.health()
        return health.get("ok", False)

    def supports_pair(self, src: str, tgt: str) -> bool:
        return (src, tgt) in {
            ("fr", "en"), ("en", "fr"),
            ("fr", "de"), ("de", "fr"),
            ("fr", "es"), ("es", "fr"),
            ("fr", "it"), ("it", "fr"),
        }

    async def translate(self, text, src, tgt, glossary=None, context=None) -> str:
        client = get_client()
        return await client.translate(text, src, tgt, provider="opus-mt")

    def estimate_cost(self, text: str) -> float:
        return 0.0  # GPU déjà payé au temps actif (négligeable pour traduction texte)
```

#### NLLB (V3)

```python
# Site/backend/app/services/translation_providers/nllb.py
from .base import TranslationProvider
from ..runpod_client import get_client


class NllbProvider(TranslationProvider):
    @property
    def id(self) -> str:
        return "nllb"

    @property
    def name(self) -> str:
        return "NLLB-200 (RunPod, 200+ langues)"

    @property
    def is_local(self) -> bool:
        return False

    async def is_available(self) -> bool:
        client = get_client()
        return await client.is_configured()

    def supports_pair(self, src: str, tgt: str) -> bool:
        # NLLB supporte 200+ langues. On simplifie en disant tout.
        return True  # à raffiner avec la liste exacte des codes ISO supportés

    async def translate(self, text, src, tgt, glossary=None, context=None) -> str:
        client = get_client()
        return await client.translate(text, src, tgt, provider="nllb")

    def estimate_cost(self, text: str) -> float:
        return 0.0
```

#### GPT-4o-mini (V3)

```python
# Site/backend/app/services/translation_providers/gpt_4o_mini.py
from .base import TranslationProvider
from ..openai_client import get_openai_client


class Gpt4oMiniProvider(TranslationProvider):
    MODEL_ID = "gpt-4o-mini"
    INPUT_PRICE_PER_M = 0.15  # USD
    OUTPUT_PRICE_PER_M = 0.60  # USD
    USD_TO_EUR = 0.92

    @property
    def id(self) -> str:
        return "gpt-4o-mini"

    @property
    def name(self) -> str:
        return "GPT-4o-mini (OpenAI)"

    @property
    def is_local(self) -> bool:
        return False

    async def is_available(self) -> bool:
        client = get_openai_client()
        return await client.is_configured()

    def supports_pair(self, src: str, tgt: str) -> bool:
        return True  # GPT supporte tout

    async def translate(self, text, src, tgt, glossary=None, context=None) -> str:
        client = get_openai_client()
        return await client.translate(text, src, tgt,
                                       model=self.MODEL_ID,
                                       glossary=glossary,
                                       context=context)

    def estimate_cost(self, text: str) -> float:
        # Estimation : ~1.5 token/mot, output similaire à input
        n_tokens = len(text.split()) * 1.5
        cost_usd = (n_tokens / 1_000_000) * (self.INPUT_PRICE_PER_M + self.OUTPUT_PRICE_PER_M)
        return cost_usd * self.USD_TO_EUR
```

#### GPT-4o (V3)

```python
# Site/backend/app/services/translation_providers/gpt_4o.py
from .gpt_4o_mini import Gpt4oMiniProvider


class Gpt4oProvider(Gpt4oMiniProvider):
    MODEL_ID = "gpt-4o"
    INPUT_PRICE_PER_M = 2.50
    OUTPUT_PRICE_PER_M = 10.00

    @property
    def id(self) -> str:
        return "gpt-4o"

    @property
    def name(self) -> str:
        return "GPT-4o (OpenAI, qualité max)"
```

#### LibreTranslate (V3, fallback)

```python
# Site/backend/app/services/translation_providers/libretranslate.py
import httpx
from .base import TranslationProvider
from ... import config


class LibreTranslateProvider(TranslationProvider):
    @property
    def id(self) -> str:
        return "libretranslate"

    @property
    def name(self) -> str:
        return "LibreTranslate (Hostinger, fallback)"

    @property
    def is_local(self) -> bool:
        return True

    async def is_available(self) -> bool:
        url = config.get("libretranslate_url", "http://localhost:5000")
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{url}/languages")
                return r.status_code == 200
        except Exception:
            return False

    def supports_pair(self, src: str, tgt: str) -> bool:
        # LibreTranslate supporte beaucoup de paires de base
        return True

    async def translate(self, text, src, tgt, glossary=None, context=None) -> str:
        url = config.get("libretranslate_url", "http://localhost:5000")
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(f"{url}/translate", json={
                "q": text, "source": src, "target": tgt,
            })
            r.raise_for_status()
            return r.json()["translatedText"]

    def estimate_cost(self, text: str) -> float:
        return 0.0
```

### Client OpenAI

```python
# Site/backend/app/services/openai_client.py
"""Wrapper OpenAI API pour traduction."""
import logging
from typing import Optional

from .. import config
from . import secrets as secrets_svc

log = logging.getLogger("voicebridge.openai")


class OpenAIClient:
    def __init__(self):
        self.api_key = secrets_svc.decrypt(config.get("openai_api_key_encrypted", ""))

    async def is_configured(self) -> bool:
        return bool(self.api_key)

    async def translate(
        self,
        text: str,
        src: str,
        tgt: str,
        model: str = "gpt-4o-mini",
        glossary: dict | None = None,
        context: list[dict] | None = None,
    ) -> str:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=self.api_key)

        system_prompt = _build_translation_prompt(src, tgt, glossary)
        messages = [{"role": "system", "content": system_prompt}]
        if context:
            messages.extend(context)
        messages.append({"role": "user", "content": text})

        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,  # déterministe pour traduction
            max_tokens=int(len(text.split()) * 3),
        )
        translated = response.choices[0].message.content.strip()
        return translated


def _build_translation_prompt(src: str, tgt: str, glossary: dict | None) -> str:
    LANG_NAMES = {"fr": "French", "en": "English", "de": "German",
                  "es": "Spanish", "it": "Italian", "ja": "Japanese",
                  "zh": "Chinese", "pt": "Portuguese", "nl": "Dutch"}
    src_name = LANG_NAMES.get(src, src)
    tgt_name = LANG_NAMES.get(tgt, tgt)

    prompt = f"""You are a professional translator. Translate from {src_name} to {tgt_name}.

Rules:
- Output ONLY the translated text, nothing else
- Preserve the original tone and register
- Keep proper nouns unchanged
- Keep numbers and dates in their original format
- For idioms, prefer equivalent idioms in the target language"""

    if glossary:
        glossary_str = "\n".join([f"- {k}: {v}" for k, v in glossary.items()])
        prompt += f"\n\nGlossary (translate exactly):\n{glossary_str}"

    return prompt


_client = None

def get_openai_client() -> OpenAIClient:
    global _client
    if _client is None:
        _client = OpenAIClient()
    return _client
```

## Endpoint backend `/api/translate`

```python
# Site/backend/app/routes/translate.py (étendu V3)

@router.get("/providers")
async def list_providers():
    """Liste les providers disponibles."""
    router_inst = get_router()
    return {"providers": router_inst.list_providers()}


@router.post("/translate")
async def translate(payload: TranslateRequest):
    """Traduction d'un texte avec provider sélectionné.
    
    body: {
      "text": "...",
      "src": "fr", "tgt": "en",
      "provider": "nllb" (optionnel, défaut = config user),
      "glossary": { "CODIR": "Executive Committee" } (optionnel, GPT only),
      "fallback": true
    }
    """
    router_inst = get_router()
    result = await router_inst.translate(
        text=payload.text,
        src=payload.src,
        tgt=payload.tgt,
        provider_id=payload.provider,
        glossary=payload.glossary,
        fallback=payload.fallback,
    )
    return result


@router.get("/warmup")
async def warmup(src: str, tgt: str, provider: str | None = None):
    """Pre-load le provider pour éviter la latence du premier appel."""
    router_inst = get_router()
    provider_id = provider or config.get("translation_default_provider", "opus-mt-cpu")
    p = router_inst._providers.get(provider_id)
    if not p:
        raise HTTPException(404, "provider_not_found")
    
    # Pour les providers GPU, déclenche un warmup RunPod
    # Pour OPUS-MT CPU, charge le modèle en RAM
    # ...
    return {"status": "ready", "provider": provider_id}
```

## UI frontend

### Sélecteur de provider dans le studio Live

```html
<!-- Visible uniquement si traduction activée -->
<div id="liveTranslateOptions" style="display:none">
  <div class="grid-2">
    <div class="field">
      <label for="liveTranslateTo">Traduire vers</label>
      <select id="liveTranslateTo">
        <option value="en">🇬🇧 Anglais</option>
        <option value="de">🇩🇪 Allemand</option>
        <option value="es">🇪🇸 Espagnol</option>
        <option value="it">🇮🇹 Italien</option>
        <option value="pt">🇵🇹 Portugais</option>
        <option value="ja">🇯🇵 Japonais</option>
        <option value="zh">🇨🇳 Chinois</option>
      </select>
    </div>
    <div class="field">
      <label for="liveTranslateProvider">Provider</label>
      <select id="liveTranslateProvider">
        <optgroup label="Souverain (gratuit)">
          <option value="opus-mt-cpu">OPUS-MT CPU (FR↔EN seul)</option>
          <option value="opus-mt-gpu">OPUS-MT GPU (FR↔EN/DE/ES/IT)</option>
          <option value="nllb" selected>NLLB-200 (200+ langues) ⭐</option>
          <option value="libretranslate">LibreTranslate (fallback)</option>
        </optgroup>
        <optgroup label="OpenAI (payant)">
          <option value="gpt-4o-mini">GPT-4o-mini (~0.04€/1000)</option>
          <option value="gpt-4o">GPT-4o (~0.40€/1000)</option>
        </optgroup>
      </select>
    </div>
  </div>
  <div class="hint" id="providerHint">
    💡 NLLB : qualité supérieure et 200+ langues, gratuit après infrastructure GPU.
  </div>
</div>
```

### Settings : provider par défaut

```html
<!-- settings.html, panel "Traduction" (NOUVEAU) -->
<div class="settings-panel" data-panel="translation">
  
  <div class="card">
    <div class="card-title">Provider de traduction par défaut</div>
    <div style="font-size:0.75rem;color:var(--text3);margin-bottom:0.75rem">
      Provider utilisé par défaut quand aucun n'est explicitement sélectionné.
    </div>
    <select id="defaultTranslationProvider" class="select-block">
      <option value="opus-mt-cpu">OPUS-MT CPU (gratuit, FR↔EN seul)</option>
      <option value="opus-mt-gpu">OPUS-MT GPU (gratuit, multi-paires)</option>
      <option value="nllb" selected>NLLB-200 (gratuit, 200+ langues)</option>
      <option value="gpt-4o-mini">GPT-4o-mini (payant)</option>
      <option value="gpt-4o">GPT-4o (payant)</option>
      <option value="libretranslate">LibreTranslate (fallback)</option>
    </select>
  </div>

  <div class="card">
    <div class="card-title">Glossaire métier (GPT uniquement)</div>
    <div style="font-size:0.75rem;color:var(--text3);margin-bottom:0.75rem">
      Termes à traduire de manière précise. Format : un terme par ligne, "FR | EN".
    </div>
    <textarea id="glossaryEditor" rows="8" placeholder="CODIR | Executive Committee
DSI | IT Department
NIS2 | NIS2 (untranslated)
PSSI | Information System Security Policy"></textarea>
    <button class="btn btn-primary" id="btnSaveGlossary">💾 Enregistrer</button>
  </div>

  <div class="card">
    <div class="card-title">Test de traduction</div>
    <div class="grid-2">
      <div class="field">
        <label>Texte source</label>
        <textarea id="testTranslateSrc" rows="3"
          placeholder="Le projet a démarré la semaine dernière."></textarea>
      </div>
      <div class="field">
        <label>De → Vers</label>
        <select id="testTranslateLangs">
          <option value="fr-en">FR → EN</option>
          <option value="fr-de">FR → DE</option>
          <option value="fr-es">FR → ES</option>
          <option value="fr-it">FR → IT</option>
          <option value="fr-ja">FR → JA</option>
          <option value="en-fr">EN → FR</option>
        </select>
      </div>
    </div>
    <div class="field">
      <label>Tester avec tous les providers disponibles</label>
      <button class="btn btn-secondary" id="btnTestAllProviders">🧪 Tester</button>
    </div>
    <div id="testResults" style="display:none">
      <!-- Tableau comparatif rempli par JS -->
    </div>
  </div>

</div>
```

### Compteur de coûts

Settings → Cloud → "Usage du mois" :

```
📊 Usage de mai 2026

┌─────────────────────────────────────────────┐
│  Traductions effectuées : 1 247             │
├─────────────────────────────────────────────┤
│  OPUS-MT CPU       :  450 trad   →    0€   │
│  OPUS-MT GPU       :  180 trad   →    0€   │
│  NLLB-200          :  342 trad   →    0€   │
│  GPT-4o-mini       :  225 trad   →  0.10€  │
│  GPT-4o            :   50 trad   →  0.20€  │
├─────────────────────────────────────────────┤
│  Total OpenAI                       0.30€  │
│  GPU RunPod (8h)                    2.72€  │
│  Volume RunPod                      3.50€  │
│  Hostinger                         16.00€  │
├─────────────────────────────────────────────┤
│  TOTAL ESTIMÉ MAI 2026             22.52€  │
└─────────────────────────────────────────────┘
```

## Use cases avancés

### Glossaire métier (GPT)

L'utilisateur définit un glossaire dans les Settings. Pour chaque appel GPT-4o ou GPT-4o-mini, le glossaire est injecté dans le system prompt :

```
Glossary (translate exactly):
- CODIR: Executive Committee
- DSI: IT Department
- NIS2: NIS2 (untranslated)
```

Cela évite que GPT-4o invente une traduction pour "CODIR" ou autre acronyme métier.

### Contexte conversationnel (GPT)

Pour les sessions live, on peut injecter le contexte des dernières phrases pour améliorer la cohérence (pronoms, références) :

```python
# routes/live.py — adaptation pour mode GPU
session_history = []

async def translate_with_context(text, src, tgt, provider):
    if provider in ("gpt-4o", "gpt-4o-mini"):
        # Inclure les 3 derniers tours pour contexte
        context = []
        for h in session_history[-3:]:
            context.append({"role": "user", "content": h["src"]})
            context.append({"role": "assistant", "content": h["tgt"]})
        result = await router.translate(text, src, tgt, provider, context=context)
    else:
        result = await router.translate(text, src, tgt, provider)
    
    session_history.append({"src": text, "tgt": result["translated"]})
    return result["translated"]
```

### Cache de traduction (optionnel)

Pour les présentations qui contiennent des phrases répétées, un cache LRU peut éviter des appels redondants :

```python
from functools import lru_cache

@lru_cache(maxsize=1000)
def cached_translate_key(text, src, tgt, provider, glossary_hash):
    # Note : ne peut pas wrapper async, utiliser un dict + lock
    ...
```

## Tests

```python
# tests/test_translation_router.py
import pytest
from app.services.translation_router import TranslationRouter


@pytest.mark.asyncio
async def test_router_default_provider():
    router = TranslationRouter()
    result = await router.translate("Bonjour", "fr", "en")
    assert "hello" in result["translated"].lower()
    assert result["provider"] in ("opus-mt-cpu", "nllb")


@pytest.mark.asyncio
async def test_router_unsupported_pair_fallback():
    router = TranslationRouter()
    # OPUS-MT CPU ne supporte pas FR→JA
    result = await router.translate("Bonjour", "fr", "ja", provider_id="opus-mt-cpu")
    assert result["provider"] != "opus-mt-cpu"  # fallback


@pytest.mark.asyncio
async def test_provider_estimate_cost():
    router = TranslationRouter()
    p_free = router._providers["opus-mt-cpu"]
    p_paid = router._providers["gpt-4o"]
    text = "Bonjour le monde"
    assert p_free.estimate_cost(text) == 0.0
    assert p_paid.estimate_cost(text) > 0.0
```

## Points de vigilance

### Licences

| Provider | Licence | Usage commercial OK ? |
|---|---|---|
| OPUS-MT (Helsinki-NLP) | Apache 2.0 | ✅ |
| NLLB-200 (Meta) | CC-BY-NC 4.0 | ⚠️ Non commercial |
| GPT-4o / GPT-4o-mini | OpenAI ToS | ✅ avec compte payant |
| LibreTranslate | AGPL | ✅ avec contraintes |

**NLLB pour usage commercial** : zone grise (licence CC-BY-NC interdit l'usage commercial). Pour usage personnel : OK. Mentionner dans l'UI.

### Privacy

| Provider | Données envoyées en externe |
|---|---|
| OPUS-MT CPU | Non (Hostinger) |
| OPUS-MT GPU | Oui → RunPod EU-FR-1 (France) |
| NLLB | Oui → RunPod EU-FR-1 (France) |
| GPT-4o-mini | Oui → OpenAI US |
| GPT-4o | Oui → OpenAI US |
| LibreTranslate | Non (Hostinger) |

L'utilisateur peut désactiver les providers cloud pour usage 100% souverain.

### Quotas et rate limits

OpenAI API a des rate limits par tier. À gérer côté `OpenAIClient` :

```python
# Retry avec backoff exponentiel sur 429
import time

async def translate_with_retry(text, src, tgt, max_retries=3):
    for attempt in range(max_retries):
        try:
            return await self._translate(text, src, tgt)
        except openai.RateLimitError:
            wait = 2 ** attempt
            log.warning("OpenAI rate limit, retry in %ds", wait)
            await asyncio.sleep(wait)
    raise
```

## Migration depuis V1

Le service `translation.py` V1 est conservé. Le nouveau `OpusMtCpuProvider` l'utilise comme backend. Aucune migration de données nécessaire.

L'ancien endpoint `/api/translate/warmup` continue de fonctionner. Le nouveau `/api/translate/providers` et `/api/translate/translate` sont ajoutés en parallèle.
