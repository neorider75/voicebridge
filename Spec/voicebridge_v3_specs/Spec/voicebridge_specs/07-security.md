# 07 - Sécurité

## Principe directeur

**Toute la sécurité est implémentée dès la V1.** Pas de "on verra plus tard". Le VPS est exposé sur internet, les données sont sensibles (biométrie vocale).

## Authentification front web

### Login
- Mot de passe unique (configuré à l'installation)
- Hashé en bcrypt avec coût 12
- Stocké dans `config.json` champ `password_hash`
- Comparaison avec `passlib.hash.bcrypt.verify()`

### Sessions
- Cookie `vb_session` sécurisé :
  - `HttpOnly` (inaccessible depuis JavaScript)
  - `Secure` (HTTPS uniquement)
  - `SameSite=Strict`
- Durée : 8h d'inactivité, puis déconnexion automatique
- Stockage serveur : signed via `itsdangerous`
- Pas de JWT (plus simple et plus sécurisé pour un usage perso)

### Reset password
- **Pas d'interface** "mot de passe oublié"
- Reset uniquement via CLI SSH :
  ```bash
  python /var/voicebridge/manage.py reset-password
  ```

## Anti brute-force

### Rate limiting (slowapi)

| Endpoint | Limite |
|---|---|
| `/api/auth/login` | 5 tentatives / 15 min par IP |
| `/api/voices` POST | 10 / min par session |
| `/api/tts/generate` | 60 / min par session |
| `/api/detection/analyze` | 20 / min par session |

### Délai progressif

Après échec login :
- Tentative 1-2 : aucun délai
- Tentative 3 : 2s avant réponse
- Tentative 4 : 5s
- Tentative 5 : 10s

### Lockout IP
- Après 10 échecs sur 1h : IP bloquée 1h
- Stockage en mémoire (dict avec timestamp)
- Reset automatique à l'expiration

### Logs
- Chaque tentative login (succès ou échec) loggée avec :
  - IP
  - Timestamp ISO 8601
  - Résultat (success/failed)
  - User-Agent

## Authentification API (VoiceBridge.app)

### Token Bearer
- Format : `sk-{32_caractères_hex}`
- Génération : `secrets.token_hex(16)` préfixé par `sk-`
- Stockage : hash SHA-256 dans `config.json`
- Header HTTP : `Authorization: Bearer sk-...`

### Génération
- Au lancement de l'app, le bash d'installation génère une première clé
- Affichée à l'utilisateur dans le terminal pendant l'installation
- Régénérable depuis Réglages → API
- Affichée en clair **une seule fois** lors de la génération

### Révocation
- Génération nouvelle clé → ancienne immédiatement invalide
- WebSocket VoiceBridge.app déconnecté instantanément
- L'app demande la nouvelle clé à l'utilisateur

## Protection des routes

### Middleware FastAPI

```python
@app.middleware("http")
async def auth_middleware(request, call_next):
    if request.url.path.startswith("/api/auth/login"):
        return await call_next(request)
    if request.url.path == "/api/system/status":
        return await call_next(request)  # public pour le polling
    
    # Vérifier session OU bearer token
    if not is_authenticated(request):
        return RedirectResponse("/login")  # ou 401 selon le contexte
    
    return await call_next(request)
```

### Toutes les routes protégées
- `/voices`, `/recordings`, `/detection`, `/settings` → redirection login si non authentifié
- `/api/*` (sauf exceptions ci-dessus) → 401 JSON

## Protection contre injections

### SQL injection
- **Aucune BDD SQL** → impossible par construction
- Métadonnées via JSON parsé par bibliothèques standards (json.loads)

### XSS
- Échappement automatique sur tout contenu utilisateur (jinja2 par défaut)
- Pas d'`innerHTML` côté JS, uniquement `textContent`
- CSP strict :
  ```
  default-src 'self'; 
  style-src 'self' fonts.googleapis.com; 
  font-src 'self' fonts.gstatic.com;
  script-src 'self';
  img-src 'self' data:;
  media-src 'self' blob:;
  connect-src 'self' wss://[domain];
  ```

### CSRF
- Tokens CSRF sur tous les formulaires POST
- Bibliothèque : `fastapi-csrf-protect`
- Header `X-CSRF-Token` obligatoire pour toute requête mutative

### Path traversal
- Validation stricte des noms de fichiers
- Whitelist : `^[a-zA-Z0-9_-]+$` pour les IDs
- Refus de `..` ou `/` dans les paths utilisateur
- Utilisation systématique de `pathlib.Path` avec `.resolve()`

## Protection fichiers

### Listing dossiers désactivé
Nginx config :
```nginx
autoindex off;
```

### Fichiers servis via FastAPI uniquement
- Jamais directement par Nginx
- Vérification de session avant chaque servir
- Routes : `/api/voices/{id}/audio`, `/api/recordings/{id}/audio`

### Upload validation
- Type MIME vérifié côté serveur (pas seulement l'extension)
- Taille max enforcée :
  - Voix : 10 Mo
  - Détection : 50 Mo
- Magic bytes vérifiés via `python-magic`
- Sanitization du nom : remplacé par UUID interne

## HTTPS forcé

### Nginx config
```nginx
server {
    listen 80;
    server_name {DOMAIN};
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name {DOMAIN};
    
    ssl_certificate /etc/letsencrypt/live/{DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/{DOMAIN}/privkey.pem;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'self'; style-src 'self' fonts.googleapis.com; font-src 'self' fonts.gstatic.com" always;
    
    autoindex off;
    server_tokens off;
    
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
```

### Certbot Let's Encrypt
- Renouvellement automatique via cron
- Vérifié à l'installation

## Stockage des secrets

### config.json
```json
{
  "domain": "voicebridge.example.com",
  "password_hash": "$2b$12$...",
  "api_token_hash": "sha256_hash_here",
  "api_token_created_at": "2026-04-26T14:30:00Z",
  "session_secret": "random_64_chars_for_signing",
  "default_retention": "session",
  "model_unload_after_minutes": 15
}
```

- Permissions fichier : `chmod 600` (lecture user uniquement)
- Owner : utilisateur `voicebridge` créé par le script d'installation
- Jamais lu par Nginx (ne sort pas du processus FastAPI)

## Logs (privacy by design)

### Loggés
- Tentatives login (IP + résultat)
- Génération nouvelle clé API
- Suppressions (voix, enregistrements)
- Erreurs serveur
- Modifications config

### Jamais loggés
- Mots de passe (même hashés)
- Clés API (même hashées)
- Contenu des textes synthétisés
- Contenu audio
- Contenu des transcriptions STT

### Rotation
- logrotate hebdomadaire
- Conservation 30 jours
- Compression gzip après rotation

## Dépendances et CVE

### Mises à jour
- À l'installation : `pip install --upgrade` toutes les dépendances
- Recommander à l'utilisateur de relancer le script tous les 3 mois pour les mises à jour de sécurité

### Audit
- Inclure `pip-audit` dans les dev dependencies
- Vérification automatique au build :
  ```bash
  pip-audit -r requirements.txt
  ```

## Backup et restauration

### Quoi backuper
- `config.json` (config et secrets)
- `voices/` (audio + .pt encodés + metadata.json)

### Quoi NE PAS backuper
- `audio/` (fichiers générés temporaires)
- `models/` (téléchargeables à nouveau)
- `logs/`

### Recommandation utilisateur
- Backup manuel via SSH/scp dans `/var/voicebridge/data/voices/` et `/var/voicebridge/data/config.json`
- Pas de système de backup automatique en V1

## Tests de sécurité

### À faire avant mise en production
- ☑️ Login : vérifier rate limit
- ☑️ Login : vérifier délai progressif
- ☑️ Login : vérifier lockout IP
- ☑️ Routes API protégées : tester sans auth → 401
- ☑️ XSS : tenter d'injecter `<script>` dans nom voix
- ☑️ Path traversal : tenter `../../../etc/passwd` dans paths
- ☑️ CSRF : tenter requête POST sans token
- ☑️ Upload : tenter fichier non audio renommé
- ☑️ HTTPS : vérifier redirection HTTP → HTTPS
- ☑️ Headers : vérifier CSP, HSTS, X-Frame
- ☑️ SSL : grade A sur SSL Labs
- ☑️ Listing dossier : vérifier 403 sur `/data/`, `/voices/`, etc.

## Checklist installation

Le script bash doit s'assurer de :
- ☑️ Création utilisateur Linux dédié `voicebridge`
- ☑️ Permissions strictes sur `config.json` (600)
- ☑️ Permissions strictes sur `voices/` (700)
- ☑️ Firewall UFW configuré (22, 80, 443 only)
- ☑️ fail2ban configuré pour SSH
- ☑️ Nginx avec headers de sécurité
- ☑️ Let's Encrypt certificat valide
- ☑️ systemd service avec restrictions :
  - `ProtectSystem=strict`
  - `ProtectHome=true`
  - `NoNewPrivileges=true`
  - `PrivateTmp=true`
