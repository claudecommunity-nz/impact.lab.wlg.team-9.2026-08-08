FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY jobs ./jobs
COPY db.py run.py ./

ENV ENRICHMENT_SCHEDULE="classify:30,geolocate:30,corroborate:60"
CMD ["python", "run.py"]
