FROM python:3.11-slim

WORKDIR /app

COPY lib/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lib/app.py /app/app.py
RUN mkdir -p /app/templates /app/static
COPY index.html /app/templates/index.html
COPY stats.html /app/templates/stats.html
COPY data/airports.json /app/static/airports.json

EXPOSE 8080
CMD ["python", "-u", "app.py"]
