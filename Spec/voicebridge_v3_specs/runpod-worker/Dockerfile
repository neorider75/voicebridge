# VoiceBridge Worker — RunPod Serverless
# Container unifié : Whisper STT + F5-TTS + NLLB + OPUS-MT + RVC
#
# Build : docker build -t voicebridge-worker:v3.0.0 .
# Push : docker push <username>/voicebridge-worker:v3.0.0

FROM --platform=linux/amd64 nvidia/cuda:12.1.0-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/runpod-volume/hf-cache \
    TRANSFORMERS_CACHE=/runpod-volume/hf-cache \
    HF_HUB_CACHE=/runpod-volume/hf-cache \
    RVC_VOLUME_PATH=/runpod-volume/rvc_models \
    LOG_LEVEL=INFO \
    PYTHONPATH=/app \
    TORCH_CUDA_ARCH_LIST="8.6;8.9;9.0"

# Système
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev \
        python3-pip \
        ffmpeg \
        git \
        wget curl \
        libsndfile1 \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Symlink python
RUN ln -sf /usr/bin/python3.11 /usr/bin/python && \
    ln -sf /usr/bin/python3.11 /usr/bin/python3

# Pip up to date
RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel

# PyTorch CUDA 12.1 en RUN dédié (suffixe +cu121 non géré par pip moderne)
RUN pip install --no-cache-dir \
      torch==2.4.0 torchaudio==2.4.0 \
      --index-url https://download.pytorch.org/whl/cu121

# Installation des autres deps Python (torch déjà en place)
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Désinstalle fbgemm_gpu + torchao (transitive deps incompat torch 2.4)
RUN pip uninstall -y fbgemm_gpu torchao 2>/dev/null || true

# Sanity check faster-whisper (affiché dans les logs Docker au build)
RUN python -c "import faster_whisper; print('✅ faster-whisper OK')" \
    || echo "⚠️  WARNING: faster-whisper unavailable, will fallback to transformers"

# Copie du code
COPY handler.py .
COPY models/ ./models/
COPY utils/ ./utils/

# Health check (optionnel — RunPod n'utilise pas mais utile pour test local)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
  CMD python -c "import torch; assert torch.cuda.is_available()" || exit 1

# Entrypoint
CMD ["python", "-u", "handler.py"]
