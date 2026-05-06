# 16 - Progress UX Pattern (barres de progression systématiques)

> **Document V3 nouveau.** Pattern UX systématique pour toutes les opérations backend > 1 seconde.

## Principe

**Toute opération backend qui prend plus d'1 seconde DOIT afficher une barre de progression** dans l'UI. C'est une règle non négociable pour la V3 : l'utilisateur ne doit jamais se demander "ça marche ou pas ?".

## Catalogue des opérations concernées

| Opération | Durée typique | Méthode |
|---|---|---|
| Préchauffage GPU RunPod (cold start) | 10-30s | Indicateur animé + statut texte |
| Retraitement audio dataset RVC | ~5 min | WebSocket /ws/progress |
| Upload .pth vers RunPod Volume | 30s-3min | XHR upload progress + WebSocket |
| Test rapide d'un modèle RVC | 5-10s | Polling /api/rvc/test/status |
| Téléchargement modèles HF (premier appel) | Variable (~5 min) | Polling logs + estimation |
| Génération TTS fichier (texte long) | 10-60s | Polling /api/tts/status |
| STT fichier | 3-10s | Polling /api/stt/status |
| Détection deepfake | 3-10s | Polling /api/detection/status |
| Encodage voix (.pt) | ~30s | Polling /api/voices/{id}/status |
| Traduction d'un long texte (>500 mots) | 1-5s | Spinner simple |
| Test connexion RunPod/OpenAI | <2s | Spinner simple |
| Export ZIP dataset RVC | 5-15s | XHR download progress |
| Génération PDF guide | 1-3s | Spinner simple |

## Format standard de progression

### Format JSON émis par le backend

```json
{
  "task_id": "uuid-1234",
  "status": "running",
  "progress_percent": 42,
  "current_step": "Découpage en clips (3/6)",
  "elapsed_seconds": 23,
  "estimated_remaining_seconds": 120,
  "logs": [
    "[09:14:23] VAD detection terminée : 47 régions",
    "[09:14:25] Démarrage segmentation"
  ],
  "result": null
}
```

### Statuts possibles

- `queued` : tâche en attente
- `running` : en cours d'exécution
- `done` : terminée avec succès, `result` rempli
- `error` : échec, `error` contient le message
- `cancelled` : annulée par l'utilisateur

## Méthodes de communication backend → frontend

### Méthode 1 : WebSocket `/ws/progress/{task_id}` (recommandée)

Pour les tâches longues (> 10s) avec mise à jour fréquente.

#### Backend

```python
# Site/backend/app/routes/progress.py
"""Routes de progression de tâches longues."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import auth as auth_mod

router = APIRouter(tags=["progress"])
log = logging.getLogger("voicebridge.progress")


@dataclass
class TaskProgress:
    task_id: str
    status: str = "queued"  # queued | running | done | error | cancelled
    progress_percent: int = 0
    current_step: str = ""
    elapsed_seconds: int = 0
    estimated_remaining_seconds: int = 0
    started_at: float = 0
    logs: list[str] = field(default_factory=list)
    result: Any = None
    error: str | None = None


# Registre global des tâches actives (au sens "in-process")
_tasks: dict[str, TaskProgress] = {}
_subscribers: dict[str, list[WebSocket]] = defaultdict(list)


def create_task() -> str:
    """Crée une nouvelle tâche et retourne son ID."""
    task_id = str(uuid.uuid4())
    _tasks[task_id] = TaskProgress(
        task_id=task_id,
        status="queued",
        started_at=time.time(),
    )
    return task_id


def update_task(task_id: str, **kwargs) -> None:
    """Met à jour une tâche et notifie les subscribers WebSocket."""
    task = _tasks.get(task_id)
    if not task:
        log.warning("update_task: unknown task_id=%s", task_id)
        return
    
    for k, v in kwargs.items():
        setattr(task, k, v)
    task.elapsed_seconds = int(time.time() - task.started_at)
    
    # Push aux subscribers
    payload = asdict(task)
    for ws in list(_subscribers[task_id]):
        try:
            asyncio.create_task(ws.send_json(payload))
        except Exception:
            pass


def append_log(task_id: str, line: str) -> None:
    """Ajoute une ligne de log à une tâche."""
    task = _tasks.get(task_id)
    if not task:
        return
    timestamp = time.strftime("%H:%M:%S")
    task.logs.append(f"[{timestamp}] {line}")
    # Garde seulement les 50 derniers logs (mémoire)
    if len(task.logs) > 50:
        task.logs = task.logs[-50:]


def complete_task(task_id: str, result: Any = None) -> None:
    update_task(task_id, status="done", progress_percent=100, result=result)


def fail_task(task_id: str, error: str) -> None:
    update_task(task_id, status="error", error=error)


def cancel_task(task_id: str) -> None:
    update_task(task_id, status="cancelled")


def cleanup_old_tasks(max_age_seconds: int = 3600) -> int:
    """Supprime les tâches terminées depuis plus de max_age_seconds."""
    now = time.time()
    n = 0
    for task_id in list(_tasks.keys()):
        task = _tasks[task_id]
        if task.status in ("done", "error", "cancelled"):
            if now - task.started_at > max_age_seconds:
                del _tasks[task_id]
                n += 1
    return n


@router.websocket("/ws/progress/{task_id}")
async def progress_ws(ws: WebSocket, task_id: str):
    if not (auth_mod._has_valid_session(ws) or auth_mod._has_valid_bearer(ws)):
        await ws.close(code=4401)
        return
    await ws.accept()
    
    task = _tasks.get(task_id)
    if not task:
        await ws.send_json({"error": "task_not_found", "task_id": task_id})
        await ws.close()
        return
    
    _subscribers[task_id].append(ws)
    
    try:
        # Envoyer l'état initial
        await ws.send_json(asdict(task))
        
        # Si déjà terminé, on close direct
        if task.status in ("done", "error", "cancelled"):
            await ws.close()
            return
        
        # Sinon, attendre les notifications (heartbeat toutes les 30s)
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=30)
            except asyncio.TimeoutError:
                # Heartbeat
                if task.status in ("done", "error", "cancelled"):
                    break
                await ws.send_json(asdict(task))
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _subscribers[task_id]:
            _subscribers[task_id].remove(ws)
        try:
            await ws.close()
        except Exception:
            pass


@router.post("/api/tasks/{task_id}/cancel")
async def cancel(task_id: str):
    """Annule une tâche (best-effort, le backend doit checker)."""
    cancel_task(task_id)
    return {"ok": True, "task_id": task_id}
```

#### Utilisation dans une tâche backend (exemple : retraitement audio)

```python
# Site/backend/app/routes/recording_session.py
from .. import progress as progress_mod
from ..services import audio_dataset_processor


@router.post("/{session_id}/process")
async def process_dataset(session_id: str, payload: ProcessRequest):
    task_id = progress_mod.create_task()
    
    # Lance la tâche en background (asyncio task)
    asyncio.create_task(_run_processing(session_id, task_id, payload))
    
    return {"task_id": task_id, "status": "queued"}


async def _run_processing(session_id, task_id, payload):
    progress_mod.update_task(task_id, status="running",
                              current_step="Initialisation")
    
    def progress_cb(percent, step, details):
        progress_mod.update_task(
            task_id,
            progress_percent=percent,
            current_step=step,
        )
    
    try:
        result = await asyncio.to_thread(
            audio_dataset_processor.process_session,
            session_dir=...,
            progress_cb=progress_cb,
            denoise_strength=payload.denoise_strength,
        )
        progress_mod.complete_task(task_id, result=result)
    except Exception as e:
        log.exception("processing failed")
        progress_mod.fail_task(task_id, error=str(e))
```

#### Frontend (consommation)

```javascript
// Site/frontend/js/progress.js
/**
 * Helper réutilisable pour s'abonner aux événements de progression.
 */
class ProgressSubscriber {
  constructor(taskId, options = {}) {
    this.taskId = taskId;
    this.options = options;
    this.ws = null;
    this.handlers = {
      update: options.onUpdate || (() => {}),
      done: options.onDone || (() => {}),
      error: options.onError || (() => {}),
    };
  }
  
  subscribe() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    this.ws = new WebSocket(`${proto}//${location.host}/ws/progress/${this.taskId}`);
    
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      
      if (data.error) {
        this.handlers.error(data.error);
        this.close();
        return;
      }
      
      this.handlers.update(data);
      
      if (data.status === 'done') {
        this.handlers.done(data.result);
        this.close();
      } else if (data.status === 'error') {
        this.handlers.error(data.error);
        this.close();
      } else if (data.status === 'cancelled') {
        this.close();
      }
    };
    
    this.ws.onerror = (e) => {
      console.error('Progress WS error', e);
      this.handlers.error('connection_error');
    };
  }
  
  cancel() {
    fetch(`/api/tasks/${this.taskId}/cancel`, {method: 'POST'});
    this.close();
  }
  
  close() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

window.ProgressSubscriber = ProgressSubscriber;
```

#### Composant UI réutilisable

```javascript
// Site/frontend/js/progress-ui.js
/**
 * Render une barre de progression standardisée.
 */
class ProgressBarUI {
  constructor(containerEl, options = {}) {
    this.container = containerEl;
    this.options = {
      showLogs: false,  // mode debug
      cancelable: false,
      ...options,
    };
    this.render();
  }
  
  render() {
    this.container.innerHTML = `
      <div class="progress-container" role="progressbar" aria-valuemin="0" aria-valuemax="100">
        <div class="progress-header">
          <span class="progress-step">Initialisation...</span>
          <span class="progress-percent">0%</span>
        </div>
        <div class="progress-bar"><div class="progress-fill"></div></div>
        <div class="progress-meta">
          <span class="progress-elapsed">0s</span>
          <span class="progress-eta"></span>
        </div>
        ${this.options.cancelable ? '<button class="progress-cancel">Annuler</button>' : ''}
        ${this.options.showLogs ? '<details class="progress-logs"><summary>Logs</summary><pre></pre></details>' : ''}
      </div>
    `;
    
    this.elStep = this.container.querySelector('.progress-step');
    this.elPercent = this.container.querySelector('.progress-percent');
    this.elFill = this.container.querySelector('.progress-fill');
    this.elElapsed = this.container.querySelector('.progress-elapsed');
    this.elEta = this.container.querySelector('.progress-eta');
    this.elLogs = this.container.querySelector('.progress-logs pre');
  }
  
  update(data) {
    this.elStep.textContent = data.current_step || '...';
    this.elPercent.textContent = `${data.progress_percent}%`;
    this.elFill.style.width = `${data.progress_percent}%`;
    this.elElapsed.textContent = this.formatDuration(data.elapsed_seconds);
    this.elEta.textContent = data.estimated_remaining_seconds > 0
      ? `~${this.formatDuration(data.estimated_remaining_seconds)} restant`
      : '';
    
    // ARIA
    const pb = this.container.querySelector('[role=progressbar]');
    pb.setAttribute('aria-valuenow', data.progress_percent);
    
    if (this.elLogs && data.logs) {
      this.elLogs.textContent = data.logs.join('\n');
    }
  }
  
  formatDuration(sec) {
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m ${s}s`;
  }
  
  setError(error) {
    this.elStep.textContent = `❌ Erreur : ${error}`;
    this.elFill.style.background = '#dc2626';
  }
  
  setDone() {
    this.elStep.textContent = '✅ Terminé';
    this.elPercent.textContent = '100%';
    this.elFill.style.width = '100%';
  }
}

window.ProgressBarUI = ProgressBarUI;
```

#### Exemple d'utilisation complète

```javascript
// Lancer une tâche et afficher la progression
async function processDataset(sessionId) {
  const res = await fetch(`/api/recording_session/${sessionId}/process`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({denoise_strength: 0.7}),
  });
  const {task_id} = await res.json();
  
  // Render UI
  const container = document.getElementById('processProgress');
  const ui = new ProgressBarUI(container, {
    cancelable: true,
    showLogs: false,  // ou true pour debug
  });
  
  // Subscribe
  const sub = new ProgressSubscriber(task_id, {
    onUpdate: (data) => ui.update(data),
    onDone: (result) => {
      ui.setDone();
      notify.success(`Dataset traité : ${result.clips_count} clips, score ${result.score}/100`);
      setTimeout(() => location.href = `/recording-session/${sessionId}/validate`, 1000);
    },
    onError: (err) => {
      ui.setError(err);
      notify.error(`Erreur : ${err}`);
    },
  });
  sub.subscribe();
}
```

### Méthode 2 : Polling REST `/api/.../status`

Pour les tâches courtes (1-30s) ou quand le WebSocket est overkill.

#### Backend

```python
# Réutilise progress_mod du dessus, mais expose /api/tasks/{id}/status
@router.get("/api/tasks/{task_id}/status")
async def get_task_status(task_id: str):
    task = progress_mod._tasks.get(task_id)
    if not task:
        raise HTTPException(404, "task_not_found")
    return asdict(task)
```

#### Frontend

```javascript
async function pollProgress(taskId, ui, intervalMs = 500) {
  while (true) {
    const res = await fetch(`/api/tasks/${taskId}/status`);
    if (!res.ok) {
      ui.setError('connection_lost');
      return;
    }
    const data = await res.json();
    ui.update(data);
    
    if (data.status === 'done') {
      ui.setDone();
      return data.result;
    } else if (data.status === 'error') {
      ui.setError(data.error);
      return null;
    }
    
    await new Promise(r => setTimeout(r, intervalMs));
  }
}
```

### Méthode 3 : XHR upload progress (pour uploads >5 Mo)

Native browser API, pas de WebSocket nécessaire.

```javascript
async function uploadWithProgress(file, url, ui) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        const percent = Math.round((e.loaded / e.total) * 100);
        ui.update({
          progress_percent: percent,
          current_step: `Upload (${formatBytes(e.loaded)} / ${formatBytes(e.total)})`,
          elapsed_seconds: 0,
          estimated_remaining_seconds: 0,
        });
      }
    });
    xhr.addEventListener('load', () => {
      if (xhr.status === 200) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        reject(new Error(xhr.statusText));
      }
    });
    xhr.addEventListener('error', () => reject(new Error('Upload failed')));
    xhr.open('POST', url);
    
    const formData = new FormData();
    formData.append('file', file);
    xhr.send(formData);
  });
}
```

### Méthode 4 : Spinner simple (opérations < 2s)

Pas de barre, juste un indicateur d'activité.

```html
<button class="btn btn-primary" id="testButton">
  <span class="btn-text">Tester</span>
  <span class="spinner hidden"></span>
</button>
```

```css
.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  display: inline-block;
}
.spinner.hidden { display: none; }
@keyframes spin {
  to { transform: rotate(360deg); }
}
```

```javascript
async function testButton() {
  const btn = document.getElementById('testButton');
  const text = btn.querySelector('.btn-text');
  const spin = btn.querySelector('.spinner');
  
  text.classList.add('hidden');
  spin.classList.remove('hidden');
  btn.disabled = true;
  
  try {
    const result = await fetch('/api/cloud/test', {method: 'POST'});
    // ...
  } finally {
    text.classList.remove('hidden');
    spin.classList.add('hidden');
    btn.disabled = false;
  }
}
```

## CSS standard pour les barres de progression

```css
/* Site/frontend/css/progress.css */

.progress-container {
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1rem;
  font-family: 'DM Mono', monospace;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
  font-size: 0.85rem;
}

.progress-step {
  font-weight: 500;
  color: var(--text);
}

.progress-percent {
  font-weight: 600;
  color: var(--accent);
}

.progress-bar {
  height: 14px;
  background: var(--surface3);
  border-radius: 7px;
  overflow: hidden;
  position: relative;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent2, var(--accent)));
  transition: width 300ms ease-out, background 200ms;
  width: 0%;
  position: relative;
}

/* Animation "shimmer" pour bien indiquer "ça travaille" */
.progress-fill::after {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(90deg,
    transparent 0%,
    rgba(255,255,255,0.3) 50%,
    transparent 100%
  );
  animation: shimmer 1.5s infinite;
}

@keyframes shimmer {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}

.progress-meta {
  display: flex;
  justify-content: space-between;
  font-size: 0.7rem;
  color: var(--text3);
  margin-top: 0.5rem;
}

.progress-cancel {
  margin-top: 0.75rem;
  padding: 0.4rem 0.8rem;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text3);
  font-size: 0.75rem;
  cursor: pointer;
}
.progress-cancel:hover {
  background: var(--surface3);
  color: var(--text);
}

.progress-logs {
  margin-top: 0.5rem;
  font-size: 0.7rem;
}
.progress-logs summary {
  cursor: pointer;
  color: var(--text3);
}
.progress-logs pre {
  background: var(--surface3);
  border-radius: 4px;
  padding: 0.5rem;
  margin-top: 0.5rem;
  max-height: 200px;
  overflow-y: auto;
  white-space: pre-wrap;
  font-size: 0.65rem;
}
```

## Cas d'usage concrets

### Préchauffage GPU

```javascript
async function warmupGPU() {
  const btn = document.getElementById('btnWarmupGPU');
  const container = document.getElementById('warmupProgress');
  container.style.display = 'block';
  
  const res = await fetch('/api/cloud/runpod/warmup', {method: 'POST'});
  const {task_id} = await res.json();
  
  const ui = new ProgressBarUI(container, {cancelable: false});
  
  // Estimation cold start : 30s. On affiche une barre simulée.
  const sub = new ProgressSubscriber(task_id, {
    onUpdate: (data) => ui.update(data),
    onDone: () => {
      btn.textContent = '✅ GPU prêt';
      setTimeout(() => container.style.display = 'none', 2000);
    },
    onError: (e) => {
      ui.setError(e);
    },
  });
  sub.subscribe();
}
```

### Upload .pth RVC

```javascript
async function uploadRvcModel(file) {
  const ui = new ProgressBarUI(
    document.getElementById('uploadProgress'),
    {cancelable: false}
  );
  
  // Phase 1 : upload Hostinger
  ui.update({progress_percent: 0, current_step: 'Upload vers le serveur...'});
  const formData = new FormData();
  formData.append('pth_file', file);
  formData.append('name', document.getElementById('modelName').value);
  
  // Upload avec XHR pour avoir progress
  const result = await new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 50);  // 0-50% pour upload
        ui.update({
          progress_percent: pct,
          current_step: `Upload (${Math.round(e.loaded/1e6)}/${Math.round(e.total/1e6)} Mo)`,
        });
      }
    });
    xhr.addEventListener('load', () => resolve(JSON.parse(xhr.responseText)));
    xhr.addEventListener('error', () => reject(new Error('Upload failed')));
    xhr.open('POST', '/api/rvc/upload');
    xhr.send(formData);
  });
  
  // Phase 2 : push RunPod via WebSocket de progression
  ui.update({progress_percent: 50, current_step: 'Push vers RunPod Volume...'});
  
  const sub = new ProgressSubscriber(result.task_id, {
    onUpdate: (data) => {
      // Mapper 0-100 du backend → 50-100 dans l'UI globale
      ui.update({
        ...data,
        progress_percent: 50 + Math.round(data.progress_percent / 2),
      });
    },
    onDone: () => {
      ui.setDone();
      notify.success(`Modèle "${result.name}" importé avec succès`);
      setTimeout(() => location.href = '/rvc', 1500);
    },
  });
  sub.subscribe();
}
```

## Tests

```python
# tests/test_progress.py
def test_create_and_update_task():
    from app.routes import progress
    task_id = progress.create_task()
    assert progress._tasks[task_id].status == "queued"
    
    progress.update_task(task_id, status="running", progress_percent=50)
    assert progress._tasks[task_id].progress_percent == 50


def test_complete_task():
    from app.routes import progress
    task_id = progress.create_task()
    progress.complete_task(task_id, result={"foo": "bar"})
    assert progress._tasks[task_id].status == "done"
    assert progress._tasks[task_id].progress_percent == 100


def test_cleanup_old_tasks():
    from app.routes import progress
    import time
    task_id = progress.create_task()
    progress.complete_task(task_id)
    progress._tasks[task_id].started_at = time.time() - 7200  # 2h ago
    n = progress.cleanup_old_tasks(max_age_seconds=3600)
    assert n == 1
```

## Cleanup automatique

Ajouter un job APScheduler dans `main.py` :

```python
_scheduler.add_job(
    lambda: progress_mod.cleanup_old_tasks(3600),
    "interval",
    minutes=15,
    id="progress_cleanup",
)
```

## Récapitulatif des routes

| Route | Méthode | Description |
|---|---|---|
| `/ws/progress/{task_id}` | WebSocket | Stream des updates |
| `/api/tasks/{task_id}/status` | GET | Polling fallback |
| `/api/tasks/{task_id}/cancel` | POST | Demande d'annulation |

## Résumé pattern

1. **Toute route backend longue** crée un task_id et lance la tâche en arrière-plan
2. La route retourne immédiatement `{"task_id": "..."}`
3. Le frontend ouvre un WebSocket `/ws/progress/{task_id}` (ou poll `/api/tasks/{task_id}/status`)
4. Le frontend affiche un `ProgressBarUI` qui se met à jour en temps réel
5. Quand la tâche est `done`, le frontend récupère le résultat et navigue/notifie

Cette architecture est uniforme pour TOUTES les opérations longues.
