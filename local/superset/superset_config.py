"""Superset config for the local strata stack.

Mounted into the container at /app/pythonpath/superset_config.py and
selected via SUPERSET_CONFIG_PATH. Tuned to allow the Angular UI at
http://localhost:4200 to embed Superset dashboards in an iframe.

This is local-dev-only. In production you'd use the Embedded SDK with
guest tokens instead of blanket-disabling frame protection.
"""
import os

SECRET_KEY = os.environ.get(
    "SUPERSET_SECRET_KEY", "local-dev-not-for-production-please-change"
)

# ----- iframe embedding ---------------------------------------------------- #
# Talisman is what sets the default `X-Frame-Options: SAMEORIGIN` and a
# strict CSP with `frame-ancestors 'self'`. Turning Talisman off lets us
# override both with the HTTP_HEADERS dict below.
TALISMAN_ENABLED = False
HTTP_HEADERS = {"X-Frame-Options": "ALLOWALL"}

# ----- Reverse-proxy awareness ------------------------------------------- #
# We run Superset behind the Angular nginx on :4200. Without ENABLE_PROXY_FIX,
# Superset constructs URLs (and the bootstrap_data the React app reads) using
# its own listening host (`superset:8088`) instead of the client-visible
# `localhost:4200`, which makes the in-iframe "+ DASHBOARD" / "+ CHART"
# buttons navigate to bad URLs (missing port → localhost:80 → refused).
#
# With this on, Superset trusts X-Forwarded-Host / Proto / Port from nginx
# and emits links that include the right scheme + host + port.
ENABLE_PROXY_FIX = True

# Cookie tuning for same-origin iframe embedding on localhost.
# We proxy Superset through the Angular nginx, so the iframe and the
# parent page share http://localhost:4200 — that's a same-site request,
# and SameSite=Lax cookies *are* sent on same-site iframe loads.
#
# Why not SameSite=None? Chrome rejects SameSite=None unless Secure=True,
# and Secure isn't possible on plain-HTTP localhost. Using Lax keeps the
# cookie alive without requiring HTTPS.
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = False  # localhost is http; flip to True in prod
SESSION_COOKIE_HTTPONLY = True

# ----- CORS --------------------------------------------------------------- #
ENABLE_CORS = True
CORS_OPTIONS = {
    "supports_credentials": True,
    "allow_headers": ["*"],
    "resources": ["*"],
    "origins": [
        "http://localhost:4200",
        "http://localhost:4300",
        "http://localhost:8088",
    ],
}

# ----- Public-role read access to published dashboards -------------------- #
# Without this, an iframe with no Superset cookie shows the login page.
# Granting "Gamma" on the Public role lets an anonymous viewer read any
# published dashboard. Leave OFF in production.
PUBLIC_ROLE_LIKE = "Gamma"

# ----- Embedded SDK feature flag (for future guest-token use) ------------- #
FEATURE_FLAGS = {
    "EMBEDDED_SUPERSET": True,
    "DASHBOARD_RBAC": True,
}
