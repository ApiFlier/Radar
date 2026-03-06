FROM python:3.11-slim
RUN apt-get update && apt-get install -y stunnel4 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN mkdir -p /app/samples

RUN echo "pid = /tmp/stunnel.pid" > /etc/stunnel/stunnel.conf && \
    echo "[faa-swim]" >> /etc/stunnel/stunnel.conf && \
    echo "client = yes" >> /etc/stunnel/stunnel.conf && \
    echo "accept = 127.0.0.1:55003" >> /etc/stunnel/stunnel.conf && \
    echo "connect = ems1.swim.faa.gov:55443" >> /etc/stunnel/stunnel.conf && \
    echo "verify = 0" >> /etc/stunnel/stunnel.conf && \
    echo "sni = ems1.swim.faa.gov" >> /etc/stunnel/stunnel.conf

COPY ingestor.py .
CMD ["sh", "-c", "stunnel4 /etc/stunnel/stunnel.conf && sleep 2 && python -u ingestor.py"]
