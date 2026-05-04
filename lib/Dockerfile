FROM python:3.11-slim

WORKDIR /app

COPY lib/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lib/app.py /app/app.py
RUN mkdir -p /app/templates /app/static
COPY index.html /app/templates/index.html
COPY stats.html /app/templates/stats.html
COPY health.html /app/templates/health.html
COPY airports.html /app/templates/airports.html
COPY ground.html /app/templates/ground.html
COPY aircraft.html /app/templates/aircraft.html
COPY alerts.html /app/templates/alerts.html
COPY settings.html /app/templates/settings.html
COPY data/airports.json /app/static/airports.json

EXPOSE 8080
CMD ["python", "-u", "app.py"]
