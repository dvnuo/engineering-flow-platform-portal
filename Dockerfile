FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

EXPOSE 8000
# forwarded-allow-ips: the pod is only reachable through ingress-nginx, so trust
# its X-Forwarded-For and let the uvicorn access log show the real client_addr
# instead of the ingress pod IP. Override with FORWARDED_ALLOW_IPS if the portal
# is ever exposed without a proxy in front of it.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-*}\""]
