# Web UI cho Trợ lý Lab Hệ nhúng (IT4210) — Flask, dùng API provider.
# Image này CHỦ ĐÍCH không gồm llama-cpp-python / model GGUF (provider local)
# để nhẹ; chạy với DEFAULT_PROVIDER=openai hoặc google.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEFAULT_PROVIDER=google

WORKDIR /app

# curl chỉ để HEALTHCHECK. --no-install-recommends giữ image gọn.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Cài deps trước (tận dụng cache layer khi code đổi mà deps không đổi).
COPY requirements-web.txt .
RUN pip install -r requirements-web.txt

# Copy mã nguồn (.dockerignore đã loại .env, *.gguf, logs, tests, docs...).
COPY . .

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:5000/ || exit 1

# Flask dev server có threaded=True -> đủ cho SSE + nhiều request của lab/demo.
CMD ["python", "-m", "webapp.app", "--host", "0.0.0.0", "--port", "5000"]
