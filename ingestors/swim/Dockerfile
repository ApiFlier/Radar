FROM python:3.11-slim

RUN apt-get update && apt-get install -y stunnel4 && rm -rf /var/lib/apt/lists/*
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ingestor.py .

RUN mkdir -p /etc/stunnel

RUN printf '%s\n' \
    'pid = /tmp/stunnel.pid' \
    '[faa-ems1]' \
    'client = yes' \
    'accept = 127.0.0.1:55003' \
    'connect = ems1.swim.faa.gov:55443' \
    'verify = 0' \
    'sni = ems1.swim.faa.gov' \
    '' \
    '[faa-ems2]' \
    'client = yes' \
    'accept = 127.0.0.1:55004' \
    'connect = ems2.swim.faa.gov:55443' \
    'verify = 0' \
    'sni = ems2.swim.faa.gov' \
    > /etc/stunnel/stunnel.conf

CMD ["sh", "-c", "stunnel4 /etc/stunnel/stunnel.conf && sleep 2 && python -u ingestor.py"]
