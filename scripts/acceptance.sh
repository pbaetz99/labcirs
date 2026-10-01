#!/bin/sh
# Acceptance stack: the production image behind nginx (TLS) with synthetic demo data, a
# mail catcher (mailpit) and the Playwright container. Everything runs through scripts/dc, in the
# Compose project labcirs-accept, so it never touches the development stack or another project.
#
#   sh scripts/acceptance.sh up      write artifacts/accept/*, start the stack, seed the demo data
#   sh scripts/acceptance.sh reset   down, then up: a fresh stack (the checks need one)
#   sh scripts/acceptance.sh run     the checks: scripts/sichtkontrolle.py, scripts/abnahme.py and the
#                                    checks that need the containers (settings, logs, database)
#   sh scripts/acceptance.sh dc ...  docker compose with the files of the stack (logs, exec, run, ...)
#   sh scripts/acceptance.sh down    remove the stack with its volumes
#
# The site is https://cirs.test inside the stack (a self-signed certificate) and
# https://localhost:18443 from the host. All output lands in artifacts/accept/ (ignored by git).
set -eu
cd "$(dirname "$0")/.."

export MSYS_NO_PATHCONV=1   # Git Bash on Windows must not rewrite /CN=... or /media/... arguments

OUT=artifacts/accept
DEMO_PORT=18443

# Compose reads the values of accept.env from the environment (they win over the defaults of
# scripts/dc, which would otherwise set a development database password).
load_env() {
    [ -f "$OUT/accept.env" ] || { echo "Run 'sh scripts/acceptance.sh up' first." >&2; exit 1; }
    set -a
    . "$OUT/accept.env"
    set +a
}
dc() { sh scripts/dc -f scripts/compose.accept.yaml -p labcirs-accept --profile e2e "$@"; }
# teed FILE COMMAND...: runs the command, shows and saves its output, returns its exit status
teed() { file=$1; shift; "$@" > "$file" 2>&1 && rc=0 || rc=$?; cat "$file"; return $rc; }
pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1" >&2; exit 1; }

write_files() {
    mkdir -p "$OUT/tls" "$OUT/branding" "$OUT/screens" "$OUT/sicht"
    if [ ! -f "$OUT/accept.env" ]; then
        umask 077
        cat > "$OUT/accept.env" <<EOF
POSTGRES_PASSWORD=accept$(openssl rand -hex 12)
LABCIRS_SECRET_KEY=accept$(openssl rand -hex 32)
LABCIRS_SERVER_NAME=cirs.test
TLS_CERT_DIR=./$OUT/tls
BRANDING_DIR=./$OUT/branding
HTTP_PORT=18080
HTTPS_PORT=$DEMO_PORT
RATE_CODES=600r/m
RATE_WRITES=600r/m
RATE_LOGIN=600r/m
EOF
        # (The rate limits are raised so that the scripted checks, which send many forms in a
        # minute, are not answered with 429. scripts/smoke-test.sh tests the limits.)
        # The passwords of the two demo accounts: made here, given to seed_demo_data and to the
        # Playwright container, never printed, never part of the repository.
        cat > "$OUT/accounts.env" <<EOF
DEMO_QM_PASSWORD=Qm$(openssl rand -hex 10)
DEMO_ADMIN_PASSWORD=Ad$(openssl rand -hex 10)
EOF
        umask 022
    fi
    if [ ! -f "$OUT/tls/fullchain.pem" ]; then
        openssl req -x509 -newkey rsa:2048 -nodes -days 3 -subj "/CN=cirs.test" \
            -addext "subjectAltName=DNS:cirs.test,DNS:localhost" \
            -keyout "$OUT/tls/privkey.pem" -out "$OUT/tls/fullchain.pem" 2>/dev/null
    fi
    # A plain logo and a theme that sets the brand tokens to their built-in values: the pages load
    # both through /branding/, which the Content-Security-Policy has to allow (same host).
    cat > "$OUT/branding/logo.svg" <<'EOF'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 32" width="96" height="32"><rect width="32" height="32" rx="6" fill="#00528c"/><circle cx="16" cy="16" r="7" fill="#fff"/><rect x="40" y="10" width="48" height="12" rx="3" fill="#00528c"/></svg>
EOF
    cat > "$OUT/branding/theme.css" <<'EOF'
:root {
  --ui-marke: #00528c;
  --ui-marke-dunkel: #003d6b;
  --ui-marke-hell: #e6eff6;
  --ui-marke-rand: #b7cfe3;
  --ui-marke-akzent: #8fc4ef;
}
EOF
}

case "${1:-}" in
reset)
    if [ -f "$OUT/accept.env" ]; then load_env; dc down -v --remove-orphans; fi
    exec sh "$0" up
    ;;
up)
    write_files
    load_env
    . "$OUT/accounts.env"
    dc up -d --build proxy mailpit
    i=0
    # Not -o /dev/null: curl on Windows fails with exit status 23 for responses with a body.
    until curl -sk -o "$OUT/probe.out" -H 'Host: cirs.test' "https://localhost:$HTTPS_PORT/login/"; do
        i=$((i + 1))
        [ "$i" -le 60 ] || { dc logs --tail 40 app; fail "the site did not answer within 120 s"; }
        sleep 2
    done
    pass "https://cirs.test answers (https://localhost:$HTTPS_PORT with Host cirs.test)"
    # 30 extra published cases: the list needs more than one page (25 a page)
    dc exec -T app python manage.py seed_demo_data --qm-password "$DEMO_QM_PASSWORD" \
        --admin-password "$DEMO_ADMIN_PASSWORD" --extra-published 30 | tee "$OUT/seed-output.txt"
    ;;
run)
    load_env
    mkdir -p "$OUT/logs" "$OUT/files"
    status=0
    # 1. the visual check: it proves first that it can fail, then looks at every public page
    teed "$OUT/sichtkontrolle-selftest.txt" sh "$0" dc run --rm -T playwright \
        python scripts/sichtkontrolle.py --selftest --out artifacts/sicht-selftest || status=1
    teed "$OUT/sichtkontrolle.txt" sh "$0" dc run --rm -T playwright python scripts/sichtkontrolle.py || status=1
    # 2. every point of the function list, in the browser
    teed "$OUT/abnahme-result.txt" sh "$0" dc run --rm -T playwright python scripts/abnahme.py || status=1
    # 3. what only the containers can show
    teed "$OUT/container-checks.txt" sh "$0" checks || status=1
    [ "$status" = 0 ] && pass "ALL CHECKS PASSED" || fail "at least one check failed (see the files in $OUT)"
    ;;
checks)
    load_env
    mkdir -p "$OUT/logs" "$OUT/files"
    # Settings of the running app container (the production settings module)
    settings_out=$(dc exec -T app python manage.py shell -c "
from django.conf import settings
from parler import appsettings
print('DEBUG =', settings.DEBUG)
print('ALLOWED_HOSTS =', settings.ALLOWED_HOSTS)
print('SESSION_COOKIE_SECURE =', settings.SESSION_COOKIE_SECURE, ' CSRF_COOKIE_SECURE =', settings.CSRF_COOKIE_SECURE)
print('SECURE_PROXY_SSL_HEADER =', settings.SECURE_PROXY_SSL_HEADER)
print('PARLER_ENABLE_CACHING =', appsettings.PARLER_ENABLE_CACHING)
print('staticfiles storage =', settings.STORAGES['staticfiles']['BACKEND'])
assert settings.DEBUG is False, 'DEBUG is on'
assert settings.ALLOWED_HOSTS == ['cirs.test'], 'ALLOWED_HOSTS is not set to the site'
assert settings.SECRET_KEY and not settings.SECRET_KEY.startswith('dev-only'), 'development secret key'
" 2>&1) || { echo "$settings_out"; fail "settings check"; }
    echo "$settings_out" | grep -v "objects imported automatically" | grep -v '^$'
    pass "DEBUG is off, ALLOWED_HOSTS is set to the site, the secret key is not the development one"

    # The language switch only with more than one language: the same page, two configurations.
    # (The Django test client in a one-off container; the second one starts with a single language.)
    switch='
from django.conf import settings
from django.test import Client
page = Client().get("/incidents/demo/", HTTP_HOST="cirs.test", secure=True).content.decode()
print("languages:", len(settings.LANGUAGES), " language buttons on the page:", page.count("name=\"language\""))
'
    dc run --rm -T app python manage.py shell -c "$switch" 2>&1 | grep "^languages" \
        | tee "$OUT/files/A5-switch-two-languages.txt"
    dc run --rm -T -e LABCIRS_LANGUAGES='{"de": "Deutsch"}' -e LABCIRS_PARLER_LANGUAGES='["de"]' \
        -e LABCIRS_PARLER_DEFAULT_LANGUAGE_CODE=de app \
        python manage.py shell -c "$switch" 2>&1 | grep "^languages" \
        | tee "$OUT/files/A5-switch-one-language.txt"
    grep -q "languages: 2  language buttons on the page: 2" "$OUT/files/A5-switch-two-languages.txt" \
        && grep -q "languages: 1  language buttons on the page: 0" "$OUT/files/A5-switch-one-language.txt" \
        || fail "the language switch does not follow the number of languages"
    pass "A5: two buttons with two languages, none with one language"

    # No client address and no user agent in the logs of the proxy and the app. The bind and loopback
    # addresses are removed from the lines first; the startup notice of nginx names the kernel version.
    dc logs --no-color --no-log-prefix proxy > "$OUT/logs/proxy.log" 2>&1
    dc logs --no-color --no-log-prefix app > "$OUT/logs/app.log" 2>&1
    requests=$(grep -c '"GET \|"POST ' "$OUT/logs/proxy.log" || true)
    echo "proxy log: $(wc -l < "$OUT/logs/proxy.log") lines, $requests of them requests;" \
        "app log: $(wc -l < "$OUT/logs/app.log") lines"
    [ "$requests" -gt 50 ] || fail "the proxy log shows too few requests to prove anything"
    leaks=$(cat "$OUT/logs/proxy.log" "$OUT/logs/app.log" | grep -v '\[notice\] .* OS: ' \
        | sed -E 's/\b(0\.0\.0\.0|127\.0\.0\.1)\b//g' \
        | grep -E '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' || true)
    [ -z "$leaks" ] || { echo "$leaks" | head -n 5 >&2; fail "IP address in the logs"; }
    pass "no IP address in the logs of proxy and app ($requests requests logged)"
    agents=$(cat "$OUT/logs/proxy.log" "$OUT/logs/app.log" \
        | grep -ciE 'mozilla|chrome|headless|playwright|python-requests' || true)
    [ "$agents" = 0 ] || fail "user agent in the logs"
    pass "no user agent in the logs"
    grep -q 'Listening at' "$OUT/logs/app.log" && ! grep -q ' "GET \| "POST ' "$OUT/logs/app.log" \
        || fail "the app log holds access lines"
    pass "gunicorn writes no access log (only startup and warnings)"
    ;;
dc)
    load_env
    shift
    dc "$@"
    ;;
down)
    load_env
    dc down -v --remove-orphans
    ;;
*)
    sed -n '2,/^set -eu/p' "$0" | grep -v '^set -eu' | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
