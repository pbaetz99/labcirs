#!/bin/sh
# Smoke test of the production stack: compose.yaml (without compose.dev.yaml) behind nginx.
# Needs docker compose, openssl and curl. Run from anywhere: sh scripts/smoke-test.sh
# Ports 8080/8443, project name labcirs-smoke. The stack and its volumes are removed at the end,
# also after a failure. Stops at the first FAIL with exit status 1.
set -eu
cd "$(dirname "$0")/.."

export MSYS_NO_PATHCONV=1   # Git Bash on Windows must not rewrite /CN=... or /media/... arguments

HTTP=http://localhost:8080
HTTPS=https://localhost:8443
ART=artifacts
JAR=$ART/smoke.cookies
HDR=$ART/smoke.headers

dc() { docker compose -f compose.yaml --env-file "$ART/smoke.env" -p labcirs-smoke "$@"; }
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1" >&2; exit 1; }
expect() { [ "$2" = "$3" ] && pass "$1" || fail "$1 (expected $2, got $3)"; }
# Not -o /dev/null: curl on Windows fails with exit status 23 for responses with a body.
status() { curl -sk -o "$ART/smoke.out" -w '%{http_code}' "$@"; }
# Fetches a page with the cookie jar. Leaves the response headers in $HDR and the body in $ART/smoke.body.
fetch() { curl -sk -b "$JAR" -c "$JAR" -D "$HDR" -o "$ART/smoke.body" "$@"; }
http_status() { sed -n '1s/^HTTP[^ ]* \([0-9]*\).*/\1/p' "$HDR"; }
header() { grep -qi "^$1:.*$2" "$HDR" || fail "header $1 with '$2' missing"; pass "header $1"; }
csrf_token() { sed -n 's/.*name="csrfmiddlewaretoken" value="\([^"]*\)".*/\1/p' "$ART/smoke.body" | head -n 1; }

cleanup() { rc=$?; dc down -v --remove-orphans >/dev/null 2>&1 || true; exit $rc; }
trap cleanup EXIT
trap 'exit 130' INT TERM

mkdir -p "$ART/tls" "$ART/branding"

# 1. Self-signed certificate for localhost
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost" \
    -keyout "$ART/tls/privkey.pem" -out "$ART/tls/fullchain.pem" 2>/dev/null

# 2. Test configuration: .env.example plus test values (the last value of a key wins)
SECRET="smoke$(openssl rand -hex 32)"
DBPASS="smoke$(openssl rand -hex 12)"
USERPASS="smoke$(openssl rand -hex 12)"
cp .env.example "$ART/smoke.env"
cat >> "$ART/smoke.env" <<EOF
LABCIRS_ENV_FILE=$ART/smoke.env
LABCIRS_SECRET_KEY=$SECRET
POSTGRES_PASSWORD=$DBPASS
LABCIRS_DB_PASSWORD=$DBPASS
LABCIRS_SERVER_NAME=localhost
LABCIRS_ALLOWED_HOSTS='["localhost"]'
LABCIRS_CSRF_TRUSTED_ORIGINS='["https://localhost:8443"]'
LABCIRS_BEHIND_PROXY=true
LABCIRS_LANGUAGES='{"en": "English", "de": "Deutsch"}'
LABCIRS_PARLER_LANGUAGES='["en", "de"]'
TLS_CERT_DIR=./$ART/tls
BRANDING_DIR=./$ART/branding
HTTP_PORT=8080
HTTPS_PORT=8443
EOF

# 3. Start the stack (fails without a proxy service)
dc config --services | grep -qx proxy || fail "compose.yaml has no proxy service"
dc down -v --remove-orphans >/dev/null 2>&1 || true
dc up -d --build
i=0
until [ "$(status "$HTTPS/")" = 200 ] || [ "$(status "$HTTPS/")" = 302 ]; do
    i=$((i + 1))
    [ "$i" -le 45 ] || fail "https://localhost:8443 did not answer within 90 s"
    sleep 2
done
pass "https answers"

# 4. Synthetic department with a reporter
dc exec -T app python manage.py shell -c "
from django.contrib.auth.models import User
from cirs.models import Department, Reporter
reporter = Reporter.objects.create(user=User.objects.create_user('smoke-reporter', password='$USERPASS'))
Department.objects.create(label='smoke', name='Smoke test', reporter=reporter, active=True)
"
dc exec -T app sh -c 'mkdir -p /media/smoke && echo ok > /media/smoke/probe.txt'

# 5. Checks
expect "http redirects to https" 301 "$(status "$HTTP/")"
expect "start page redirects" 302 "$(status "$HTTPS/")"

# server_tokens off at http level: no version on port 80 and 443
for url in "$HTTP/" "$HTTPS/"; do
    fetch "$url"
    expect "Server header without version ($url)" nginx "$(sed -n 's/^[Ss]erver: *//p' "$HDR" | tr -d '\r')"
done

# Versions that require a login for reporting need a session; newer ones let anonymous users in
# and refuse the reporter login, so the result of the login is not checked.
fetch "$HTTPS/login/"
curl -sk -b "$JAR" -c "$JAR" -e "$HTTPS/login/" -o "$ART/smoke.out" \
    --data-urlencode "csrfmiddlewaretoken=$(csrf_token)" \
    --data-urlencode "username=smoke-reporter" --data-urlencode "password=$USERPASS" "$HTTPS/login/"

fetch "$HTTPS/incidents/smoke/"
expect "incident list" 200 "$(http_status)"
header content-security-policy "default-src 'self'"
header strict-transport-security "max-age="
header x-content-type-options "nosniff"
grep -qi "^content-security-policy:.*unsafe-inline" "$HDR" && fail "the policy of the public pages allows unsafe-inline"
pass "public pages: no unsafe-inline"

css=$(grep -o '/static/[^"]*\.css' "$ART/smoke.body" | head -n 1)
[ -n "$css" ] || fail "incident list links no stylesheet"
expect "static file via whitenoise" 200 "$(status "$HTTPS$css")"
expect "media file from the media volume" 200 "$(status "$HTTPS/media/smoke/probe.txt")"

# /admin/ gets the same policy as the public pages (the admin pages use no inline styles)
fetch "$HTTPS/admin/"
header content-security-policy "default-src 'self'"
header strict-transport-security "max-age="
header x-content-type-options "nosniff"
grep -qi "^content-security-policy:.*unsafe-inline" "$HDR" && fail "the policy of /admin/ allows unsafe-inline"
pass "/admin/: no unsafe-inline"

fetch "$HTTPS/incidents/smoke/search/"
expect "code page" 200 "$(http_status)"
token=$(csrf_token)
[ -n "$token" ] || fail "code page has no CSRF token"
post_code() {
    curl -sk -b "$JAR" -c "$JAR" -e "$HTTPS/incidents/smoke/search/" -o "$ART/smoke.out" -w '%{http_code}\n' \
        --data-urlencode "csrfmiddlewaretoken=$token" --data-urlencode "incident_code=nosuchcode" \
        "$HTTPS/incidents/smoke/search/"
}
expect "code check POST passes CSRF" 200 "$(post_code)"

n=0
while [ "$n" -lt 20 ]; do
    [ "$(status -b "$JAR" "$HTTPS/incidents/smoke/search/")" = 429 ] && fail "GET of the code page was rate limited"
    n=$((n + 1))
done
pass "GET is not rate limited"

limited=0
n=0
while [ "$n" -lt 45 ]; do
    [ "$(post_code)" = 429 ] && limited=$((limited + 1))
    n=$((n + 1))
done
[ "$limited" -ge 1 ] && pass "45 fast POSTs: $limited answered 429" || fail "no 429 after 45 fast POSTs"

# Django logs to stderr: a missing page leaves a warning in the app log
expect "missing page" 404 "$(status "$HTTPS/no-such-page/")"
dc logs app | grep -q 'WARNING django.request Not Found: /no-such-page/' || fail "app log lacks the warning for the missing page"
pass "django.request warnings reach the app log"

# The logs must show requests, but no client address. The bind and loopback addresses 0.0.0.0 and
# 127.0.0.1 are removed from the lines first, so a line with such an address and a client address
# still counts. The startup notice of nginx names the kernel version, which can have four numbers.
# Only IPv4 is checked, Docker runs without IPv6 here.
# A search term stays out of the proxy log: only the path is written, never the query string.
status "$HTTPS/incidents/smoke/?q=smoke-search-term" > /dev/null
dc logs proxy | grep -q '"GET ' || fail "proxy log shows no requests"
dc logs proxy | grep -q 'smoke-search-term' && fail "a search term reached the proxy log"
leaks=$(dc logs proxy app | grep -v '\[notice\] .* OS: ' \
    | sed -E 's/\b(0\.0\.0\.0|127\.0\.0\.1)\b//g' \
    | grep -E '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' || true)
[ -z "$leaks" ] || { echo "$leaks" >&2; fail "IP address in the logs"; }
pass "no IP addresses in the proxy and app logs"

echo "SMOKE TEST PASSED"
