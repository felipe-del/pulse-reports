FROM python:3.12-slim

LABEL maintainer="GP Reporting"
LABEL description="Servicio de reporting automático para campañas Gane Premios"

# Dependencias del sistema para psycopg2 y Pillow
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc libjpeg-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Volúmenes para output y logs
RUN mkdir -p output logs

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

CMD ["python", "scheduler.py"]
