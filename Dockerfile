# PDF -> Word service (the static UI is separate, see .github/workflows/pages.yml)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    ALLOWED_ORIGINS=*

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# CPU only torch keeps the image ~1 GB smaller than the default wheels
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
      torch torchvision && \
    pip install --no-cache-dir -r requirements.txt

COPY pipeline ./pipeline
COPY server.py ./

EXPOSE 8000
CMD ["python", "server.py"]
