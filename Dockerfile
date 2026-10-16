FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --system bot \
    && useradd --system --gid bot --home-dir /app bot

COPY --chown=bot:bot . .

USER bot

CMD ["python", "main.py"]
