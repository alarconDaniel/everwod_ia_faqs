FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FAQ_LLM_DEVICE=cpu \
    FAQ_LLM_TORCH_DTYPE=auto

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-no-torch.txt .

RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -r requirements-no-torch.txt

COPY . .

EXPOSE 8003

CMD ["uvicorn", "suggestion_service:app", "--host", "0.0.0.0", "--port", "8003"]