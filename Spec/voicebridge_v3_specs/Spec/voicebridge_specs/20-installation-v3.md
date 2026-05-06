# 20 - Installation V3 (extensions)

> **Document V3 nouveau.** Détail des modifications du script `install.sh` pour V3.
>
> Le doc `08-installation.md` (V1) reste valable pour les phases 1-14 existantes. La phase 15 est nouvelle.

## Vue d'ensemble

V3 ajoute une phase 15 optionnelle "Cloud config" qui permet de configurer RunPod et OpenAI au moment de l'installation (ou plus tard via Settings UI).

Le reste de l'install (phases 1-14) reste inchangé.

## Modifications de `install.sh`

### Renumérotation

| Phase | V1 (actuel) | V3 |
|---|---|---|
| 1 | Vérifications | Vérifications (inchangé) |
| 2 | Questions interactives | Questions interactives (inchangé) |
| 3 | Récap | Récap (inchangé) |
| 4 | Paquets système | Paquets système (inchangé) |
| 5 | Code applicatif | Code applicatif (inchangé) |
| 6 | Modèles ML | Modèles ML (inchangé) |
| 7 | Voix par défaut | Voix par défaut (inchangé) |
| 8 | Config | Config (inchangé) |
| 9 | macOS app | macOS app (inchangé) |
| 10 | Nginx | Nginx (inchangé) |
| 11 | Systemd | Systemd (inchangé) |
| 12 | Firewall | Firewall (inchangé) |
| 13 | Cron | Cron (inchangé) |
| 14 | Récap final | Récap final (devient phase 15) |
| 15 | — | **NOUVEAU** : Cloud config |

Donc :
- Phases 1-13 inchangées
- Phase 14 (récap final) → renommée Phase 15
- Phase 14 nouvelle : Cloud config

### Phase 14 nouvelle : Cloud config

```bash
# install.sh - phase 14 nouvelle

phase14_cloud() {
  banner "Phase 14 / 15 — Configuration Cloud (optionnel)"
  
  if [[ "${SKIP_CLOUD:-no}" == "yes" ]]; then
    info "Mode --skip-cloud : phase Cloud sautée"
    return
  fi
  
  echo ""
  info "VoiceBridge V3 supporte le mode Live multilingue via RunPod (GPU cloud)"
  info "et la traduction GPT-4o via OpenAI (optionnel)."
  echo ""
  info "Tu peux configurer ces services maintenant ou plus tard via"
  info "https://${DOMAIN}/settings → Cloud."
  echo ""
  
  # Demander si l'utilisateur veut configurer maintenant
  read -p "Configurer RunPod maintenant ? [o/N] " configure_runpod
  if [[ "${configure_runpod,,}" == "o" ]]; then
    configure_runpod_now
  fi
  
  echo ""
  read -p "Configurer OpenAI maintenant ? [o/N] " configure_openai
  if [[ "${configure_openai,,}" == "o" ]]; then
    configure_openai_now
  fi
}


configure_runpod_now() {
  echo ""
  info "Configuration RunPod"
  info "==================="
  echo ""
  info "Pré-requis :"
  info "  1. Créer un compte sur https://runpod.io (5\$ offerts)"
  info "  2. Settings → API Keys → Create API Key"
  info "  3. Storage → Network Volumes → Create (50 Go, EU-FR-1)"
  info "  4. Build et push de l'image Docker (cf. runpod-worker/README.md)"
  info "  5. Serverless → New Endpoint avec ton image et le Volume monté"
  echo ""
  
  read -p "Clé API RunPod (rpa_...) : " RUNPOD_API_KEY
  read -p "Endpoint ID : " RUNPOD_ENDPOINT_ID
  read -p "Network Volume ID : " RUNPOD_VOLUME_ID
  
  if [[ -z "$RUNPOD_API_KEY" || -z "$RUNPOD_ENDPOINT_ID" ]]; then
    warn "Clé ou endpoint manquant, configuration annulée"
    return
  fi
  
  # Test de connexion
  info "Test de connexion RunPod ..."
  RESULT=$(curl -s -m 10 \
    -H "Authorization: Bearer $RUNPOD_API_KEY" \
    "https://api.runpod.ai/v2/$RUNPOD_ENDPOINT_ID/health" \
    || echo "ERROR")
  
  if [[ "$RESULT" == "ERROR" ]] || [[ "$RESULT" == *"error"* ]]; then
    warn "Test de connexion échoué. Vérifie tes credentials."
    warn "Tu peux retenter plus tard via Settings."
    return
  fi
  
  info "✓ Connexion RunPod OK"
  
  # Stocker via l'API VoiceBridge (chiffré côté backend)
  # On utilise un appel direct au manage.py pour éviter de dépendre de
  # l'API HTTP (qui peut ne pas être lancée à ce stade)
  sudo -u "$VB_USER" "$VB_VENV/bin/python" "$VB_APP_DIR/Site/backend/manage.py" \
    set-runpod-config \
    --api-key "$RUNPOD_API_KEY" \
    --endpoint-id "$RUNPOD_ENDPOINT_ID" \
    --volume-id "$RUNPOD_VOLUME_ID"
  
  info "✓ RunPod configuré et chiffré dans config.json"
}


configure_openai_now() {
  echo ""
  info "Configuration OpenAI"
  info "===================="
  echo ""
  info "Pré-requis :"
  info "  1. Créer un compte sur https://platform.openai.com"
  info "  2. Ajouter une méthode de paiement"
  info "  3. API Keys → Create new secret key"
  echo ""
  
  read -p "Clé API OpenAI (sk-...) : " OPENAI_API_KEY
  
  if [[ -z "$OPENAI_API_KEY" ]]; then
    warn "Clé OpenAI vide, configuration annulée"
    return
  fi
  
  # Test simple
  info "Test de connexion OpenAI ..."
  RESULT=$(curl -s -m 10 \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    "https://api.openai.com/v1/models" \
    || echo "ERROR")
  
  if [[ "$RESULT" == "ERROR" ]] || [[ ! "$RESULT" == *"gpt-4"* ]]; then
    warn "Test de connexion échoué. Vérifie ta clé."
    return
  fi
  
  info "✓ Connexion OpenAI OK"
  
  sudo -u "$VB_USER" "$VB_VENV/bin/python" "$VB_APP_DIR/Site/backend/manage.py" \
    set-openai-config \
    --api-key "$OPENAI_API_KEY"
  
  info "✓ OpenAI configuré et chiffré dans config.json"
}


# Phase 15 (ex-14) — Récap final
phase15_recap() {
  banner "Phase 15 / 15 — Installation terminée"
  echo ""
  info "🎉 VoiceBridge V3 est prêt !"
  info ""
  info "URL : https://${DOMAIN}"
  info "Login : ${ADMIN_PASSWORD_HINT}"
  info ""
  info "📚 Premiers pas :"
  info "  1. Connecte-toi sur https://${DOMAIN}"
  info "  2. Studio → Live → choisis ton mode (CPU ou GPU)"
  info "  3. Modèles RVC → prépare un enregistrement (~25 min)"
  info "  4. Suis le tutoriel Kaggle pour entraîner ton modèle"
  info ""
  info "📖 Doc : ${VB_APP_DIR}/Spec/voicebridge_specs/"
  info "📥 Guide RVC PDF : https://${DOMAIN}/api/rvc/guide.pdf"
  info ""
  info "Pour mettre à jour plus tard :"
  info "  cd ${VB_APP_DIR}"
  info "  sudo -u ${VB_USER} git pull origin main"
  info "  sudo systemctl restart voicebridge"
}


# ─── Modification du main() ───────────────────────────────
main() {
  # ... phases 1-13 inchangées ...
  
  run_phase phase14 phase14_cloud   # NOUVEAU
  
  phase15_recap                      # ex-phase14
}
```

### Nouvelles options du script

```bash
# install.sh - en tête, gestion des arguments

case "$1" in
  --minimal)
    SKIP_ML=yes
    ;;
  --with-ufw)
    ENABLE_UFW=yes
    ;;
  --fresh)
    rm -rf /var/voicebridge/.install_state/
    ;;
  --skip-cloud)
    SKIP_CLOUD=yes
    ;;
  --help|-h)
    cat <<'HELP'
Usage: ./install.sh [OPTIONS]

Options:
  --minimal       Saute le téléchargement des modèles ML (test rapide)
  --with-ufw      Active UFW (off par défaut)
  --fresh         Efface le checkpoint et repart de zéro
  --skip-cloud    Saute la phase Cloud config (configurable plus tard)
HELP
    exit 0
    ;;
esac
```

## Modifications de `manage.py`

Ajouter les commandes CLI pour configurer Cloud depuis l'install :

```python
# Site/backend/manage.py

import argparse


def cmd_set_runpod_config(args):
    """Configure RunPod credentials (chiffrés)."""
    from app import config
    from app.services import secrets
    
    config.set_many({
        "runpod_api_key_encrypted": secrets.encrypt(args.api_key),
        "runpod_endpoint_id": args.endpoint_id,
        "runpod_volume_id": args.volume_id or "",
        "runpod_datacenter": args.datacenter or "EU-FR-1",
    })
    print("✓ RunPod config saved (encrypted)")


def cmd_set_openai_config(args):
    from app import config
    from app.services import secrets
    
    config.set_many({
        "openai_api_key_encrypted": secrets.encrypt(args.api_key),
    })
    print("✓ OpenAI config saved (encrypted)")


def cmd_test_runpod(args):
    """Test RunPod connection."""
    import asyncio
    from app.services.runpod_client import get_client
    
    client = get_client()
    result = asyncio.run(client.health())
    if result.get("ok"):
        print(f"✓ RunPod OK · ping {result.get('ping_ms', '?')}ms")
    else:
        print(f"✗ RunPod KO : {result.get('error')}")
        exit(1)


def cmd_test_openai(args):
    """Test OpenAI connection."""
    import asyncio
    from app.services.openai_client import get_openai_client
    
    client = get_openai_client()
    if not asyncio.run(client.is_configured()):
        print("✗ OpenAI not configured")
        exit(1)
    
    result = asyncio.run(client.translate("Hello", "en", "fr"))
    print(f"✓ OpenAI OK · test : 'Hello' → '{result}'")


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    
    # V1 commands (existantes)
    sub.add_parser("reset-password")
    sub.add_parser("regenerate-api-key")
    
    # V3 commands (nouvelles)
    p_runpod = sub.add_parser("set-runpod-config")
    p_runpod.add_argument("--api-key", required=True)
    p_runpod.add_argument("--endpoint-id", required=True)
    p_runpod.add_argument("--volume-id")
    p_runpod.add_argument("--datacenter")
    
    p_openai = sub.add_parser("set-openai-config")
    p_openai.add_argument("--api-key", required=True)
    
    sub.add_parser("test-runpod")
    sub.add_parser("test-openai")
    
    args = parser.parse_args()
    
    if args.cmd == "set-runpod-config":
        cmd_set_runpod_config(args)
    elif args.cmd == "set-openai-config":
        cmd_set_openai_config(args)
    elif args.cmd == "test-runpod":
        cmd_test_runpod(args)
    elif args.cmd == "test-openai":
        cmd_test_openai(args)
    # ... commandes V1 ...


if __name__ == "__main__":
    main()
```

## Modifications de `config.py` côté backend

Ajouter les nouvelles clés :

```python
# Site/backend/app/config.py
DEFAULT_CONFIG = {
    # V1 existants
    "domain": "",
    "password_hash": "",
    "api_token_hash": "",
    "default_retention": "session",
    "model_unload_after_minutes": 15,
    "default_tts_engine": "neutts",
    
    # V3 ajouts
    "runpod_api_key_encrypted": "",
    "runpod_endpoint_id": "",
    "runpod_volume_id": "",
    "runpod_datacenter": "EU-FR-1",
    "openai_api_key_encrypted": "",
    "default_live_mode": "gpu-clone",
    "default_translation_provider": "nllb",
    "translation_glossary": {},  # {"FR": "EN", ...}
    "libretranslate_url": "",
    "master_key": "",  # générée au premier boot
}
```

## Génération de la master_key

La master_key utilisée pour Fernet est générée à la première initialisation (phase 8) et persistée dans config.json.

```python
# config.py - modification de la fonction load()
def load() -> dict:
    if not _CONFIG_PATH.exists():
        # First boot : générer la master_key
        DEFAULT_CONFIG["master_key"] = secrets.token_urlsafe(32)
        _save(DEFAULT_CONFIG)
    
    with _CONFIG_PATH.open() as f:
        return json.load(f)
```

⚠️ **Important** : la master_key n'est PAS un secret ultime (elle est dans le même fichier que les données chiffrées). Le chiffrement Fernet protège contre la lecture accidentelle, pas contre une compromission complète du serveur. Pour V3.5, considérer un keyring système ou Vault.

## Tests d'install

| Scénario | Commande | Expected |
|---|---|---|
| Install fresh complet | `./install.sh` | Phases 1-15, demande Cloud à phase 14 |
| Install sans Cloud | `./install.sh --skip-cloud` | Phase 14 sautée |
| Reprise après échec phase 14 | `./install.sh` | Reprend à phase 14 |
| Test RunPod après install | `manage.py test-runpod` | ✓ ou ✗ avec erreur claire |
| Test OpenAI après install | `manage.py test-openai` | ✓ ou ✗ avec erreur claire |

## Coûts récap (pour le récap final)

```
Coûts indicatifs V3 par profil d'usage :

  Mode CPU seul (V1 fonctionnement)
    Hostinger Paris       : 16 €/mois
    TOTAL                 : 16 €/mois

  V3 usage modéré (Live multilingue 8h/mois)
    Hostinger Paris       : 16 €/mois
    RunPod Volume         :  3.5 €/mois
    RunPod GPU            : ~3 €/mois
    OpenAI (optionnel)    :  0.5 €/mois
    TOTAL                 : ~23 €/mois

  V3 usage intensif (Live multilingue 30h/mois + GPT-4o)
    Hostinger Paris       : 16 €/mois
    RunPod Volume         :  3.5 €/mois
    RunPod GPU            : ~10 €/mois
    OpenAI                : ~5 €/mois
    TOTAL                 : ~35 €/mois
```

## Notes pour Claude Code

L'install est **idempotent** : si l'utilisateur relance après une install, les phases déjà faites (avec checkpoint dans `/var/voicebridge/.install_state/`) sont skippées.

Pour la phase 14 Cloud :
- Si déjà passée → sautée
- Si l'utilisateur veut reconfigurer → utiliser Settings UI ou commande `manage.py`

Le checkpoint phase14 est posé dès qu'on entre dans la phase, même si l'utilisateur skip RunPod et OpenAI (sinon on demanderait à chaque relance).
