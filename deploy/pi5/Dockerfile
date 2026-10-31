FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY headend/requirements-lock.txt /app/requirements-lock.txt
RUN pip install --no-cache-dir --only-binary=:all: -r requirements-lock.txt
COPY headend /app/headend
COPY tools /app/tools
COPY docs/VOICE_PACKAGES.md /app/docs/VOICE_PACKAGES.md
USER 1000:1000
EXPOSE 8765
CMD ["python", "-m", "headend.server", "--lan"]
