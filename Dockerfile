FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Hosted Appium Inspector for recording mobile test steps, served at
# /inspector/. An empty APPIUM_INSPECTOR_VERSION, or a build without access to
# the npm registry, leaves it out; members then record with the desktop app.
ARG APPIUM_INSPECTOR_VERSION=2026.9.2
ARG NPM_REGISTRY=https://registry.npmjs.org
COPY scripts/fetch_appium_inspector.py /tmp/fetch_appium_inspector.py
RUN if [ -n "$APPIUM_INSPECTOR_VERSION" ]; then \
      python /tmp/fetch_appium_inspector.py "$APPIUM_INSPECTOR_VERSION" /opt/appium-inspector "$NPM_REGISTRY" \
      || echo "Appium Inspector web build not bundled"; \
    fi; \
    rm -f /tmp/fetch_appium_inspector.py
ENV EFP_APPIUM_INSPECTOR_DIR=/opt/appium-inspector

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

EXPOSE 8000
# forwarded-allow-ips: the pod is only reachable through ingress-nginx, so trust
# its X-Forwarded-For and let the uvicorn access log show the real client_addr
# instead of the ingress pod IP. Override with FORWARDED_ALLOW_IPS if the portal
# is ever exposed without a proxy in front of it.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS:-*}\""]
