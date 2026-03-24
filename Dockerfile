FROM python:3.11-slim

ARG TELEGRAM_TOKEN
ARG DEEPSEEK_API_KEY
ENV TELEGRAM_TOKEN=$TELEGRAM_TOKEN
ENV DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY

WORKDIR /app

# Обновляем список пакетов, но не устанавливаем ничего лишнего
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код
COPY . .

# Запускаем бота
CMD ["python", "bot.py"]
