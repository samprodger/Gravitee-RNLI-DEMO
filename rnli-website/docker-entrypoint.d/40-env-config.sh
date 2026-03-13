#!/bin/sh
# Inject environment variables into config.js at container startup.
# Falls back to localhost defaults if env vars are not set.

AGENT_CARD_URL="${AGENT_CARD_URL:-http://localhost:8082/stations-agent/.well-known/agent-card.json}"
OIDC_URL="${OIDC_URL:-http://localhost:8092/gravitee/oidc/.well-known/openid-configuration}"
OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-rnli-lifeboat}"
REDIRECT_URI="${REDIRECT_URI:-http://localhost:8002/}"
VISITED_STATIONS_URL="${VISITED_STATIONS_URL:-http://localhost:8082/visited-stations/history}"

export AGENT_CARD_URL OIDC_URL OIDC_CLIENT_ID REDIRECT_URI VISITED_STATIONS_URL

envsubst < /usr/share/nginx/html/config.js.template \
         > /usr/share/nginx/html/config.js

echo "config.js generated:"
cat /usr/share/nginx/html/config.js
