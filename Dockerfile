# The Appium Inspector web build that Portal serves at /inspector/ for
# recording mobile test steps. npm fetches it, so a registry mirror, a proxy,
# or an .npmrc apply the way they do for any npm install:
#   --build-arg NPM_REGISTRY=https://nexus.example.com/repository/npm-proxy
#   --build-arg HTTPS_PROXY=http://proxy.example.com:3128 --build-arg NO_PROXY=nexus.example.com
#   --secret id=npmrc,src=$HOME/.npmrc        (a registry that needs a login)
# An empty APPIUM_INSPECTOR_VERSION leaves the Inspector out, and members then
# record with the desktop Inspector; any other failure fails the build.
# Needs BuildKit (the default since Docker 23; otherwise DOCKER_BUILDKIT=1).
ARG APPIUM_INSPECTOR_VERSION=2026.9.2

FROM node:22-alpine AS inspector
ARG APPIUM_INSPECTOR_VERSION
ARG NPM_REGISTRY=
WORKDIR /inspector
RUN --mount=type=secret,id=npmrc,target=/root/.npmrc \
    mkdir -p dist && if [ -n "$APPIUM_INSPECTOR_VERSION" ]; then \
      if [ -n "$NPM_REGISTRY" ]; then export npm_config_registry="$NPM_REGISTRY"; fi; \
      npm pack "appium-inspector-plugin@$APPIUM_INSPECTOR_VERSION" \
      && mkdir -p unpacked && tar -xzf appium-inspector-plugin-*.tgz -C unpacked \
      && cp -a unpacked/package/dist-browser/. dist/ \
      && test -f dist/index.html; \
    fi

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --from=inspector /inspector/dist /opt/appium-inspector
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
