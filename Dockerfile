FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 TZ=UTC
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
COPY alfabot ./alfabot
COPY run_web.py run_bot.py backtest.py config.example.yaml ./
COPY scripts ./scripts
CMD ["python", "run_web.py", "--config", "config.yaml"]
