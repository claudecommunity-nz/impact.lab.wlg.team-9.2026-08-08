FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common ./common
COPY sources ./sources
COPY run.py .

# SOURCES picks which collectors this container runs, e.g. SOURCES=rss,geonet
ENV SOURCES=rss SCRAPE_INTERVAL_SECONDS=600
CMD ["python", "run.py"]
