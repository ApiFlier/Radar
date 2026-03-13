FROM python:3.11-slim

WORKDIR /app

COPY lib/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lib/app.py /app/app.py
RUN mkdir -p /app/templates
COPY index.html /app/templates/index.html

EXPOSE 8080
CMD ["python", "-u", "app.py"]
