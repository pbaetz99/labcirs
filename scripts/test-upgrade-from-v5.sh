#!/bin/sh
# Proves the upgrade path from LabCIRS v5.2.1: builds a v5.2.1 installation (Python 2.7, Django 1.11,
# PostgreSQL 11) in containers, fills it with synthetic data, dumps it with pg_dump, restores the
# dump into PostgreSQL 17 and upgrades it with the new image, following docs/upgrade.md.
# Needs docker compose, git (with the tag v5.2.1) and openssl. Run from anywhere:
#   sh scripts/test-upgrade-from-v5.sh
# Ends with "UPGRADE OK" and exit status 0. Everything it creates is named labcirs-upgrade*
# and is removed at the end, also after a failure. Does not publish a port.
set -eu
cd "$(dirname "$0")/.."

export MSYS_NO_PATHCONV=1   # Git Bash on Windows must not rewrite /media or /dump arguments

ART=artifacts
OLD=labcirs-upgrade-old          # old stack: containers old-db and old-app, volumes -media and -dump
NEW=labcirs-upgrade              # new stack: compose project, network labcirs-upgrade_default
TAG=v5.2.1
t0=$(date +%s)

dc() { docker compose -f compose.yaml --env-file "$ART/upgrade.env" -p "$NEW" "$@"; }
step() { echo; echo "== $1 ($(($(date +%s) - t0)) s)"; }
fail() { echo "FAIL  $1" >&2; exit 1; }

cleanup() {
    rc=$?
    dc down -v --remove-orphans >/dev/null 2>&1 || true
    docker rm -f -v "$OLD-db" "$OLD-app" >/dev/null 2>&1 || true
    docker volume rm "$OLD-media" "$OLD-dump" >/dev/null 2>&1 || true
    docker network rm "$OLD" >/dev/null 2>&1 || true
    rm -f "$ART/upgrade.env"   # holds the throwaway secrets of this run
    exit $rc
}
trap cleanup EXIT
trap 'exit 130' INT TERM

git rev-parse --verify --quiet "$TAG^{commit}" >/dev/null || fail "tag $TAG not found (git fetch --tags)"
mkdir -p "$ART"

# Throwaway secrets, used for these containers only
SEED_PASSWORD="seed$(openssl rand -hex 12)"
OLD_DB_PASSWORD="old$(openssl rand -hex 12)"
NEW_DB_PASSWORD="new$(openssl rand -hex 12)"

# Remove leftovers of an aborted run
dc down -v --remove-orphans >/dev/null 2>&1 || true
docker rm -f -v "$OLD-db" "$OLD-app" >/dev/null 2>&1 || true
docker volume rm "$OLD-media" "$OLD-dump" >/dev/null 2>&1 || true
docker network rm "$OLD" >/dev/null 2>&1 || true

step "old stack: PostgreSQL 11 and LabCIRS $TAG on Python 2.7"
docker network create "$OLD" >/dev/null
docker volume create "$OLD-media" >/dev/null
docker volume create "$OLD-dump" >/dev/null
docker run -d --name "$OLD-db" --network "$OLD" \
    -e POSTGRES_DB=labcirs -e POSTGRES_USER=labcirs -e POSTGRES_PASSWORD="$OLD_DB_PASSWORD" \
    postgres:11 >/dev/null
docker run -d --name "$OLD-app" --network "$OLD" -v "$OLD-media:/media" -w /app \
    -e PYTHONIOENCODING=utf-8 -e PIP_DISABLE_PIP_VERSION_CHECK=1 -e PIP_NO_PYTHON_VERSION_WARNING=1 \
    python:2.7-slim sleep 3600 >/dev/null
git archive "$TAG" | docker exec -i "$OLD-app" tar -x -C /app
# requirements.txt of the tag, plus the database driver (v5.2.1 listed none)
docker exec "$OLD-app" pip install --no-cache-dir -q -r requirements.txt psycopg2-binary==2.8.6
docker exec -i "$OLD-app" sh -c 'cat > /app/labcirs/settings/local_config.json' <<EOF
{
    "SECRET_KEY": "old-stack-only",
    "ALLOWED_HOSTS": ["localhost"],
    "ROOT_URL": "",
    "DB_ENGINE": "django.db.backends.postgresql_psycopg2",
    "DB_NAME": "labcirs",
    "DB_USER": "labcirs",
    "DB_PASSWORD": "$OLD_DB_PASSWORD",
    "DB_HOST": "$OLD-db",
    "DB_PORT": "5432",
    "ORGANIZATION": "Synthetic organization",
    "TIME_ZONE": "Europe/Berlin",
    "EMAIL_HOST": "",
    "EMAIL_HOST_PASSWORD": "",
    "EMAIL_HOST_USER": "",
    "EMAIL_PORT": "",
    "LANGUAGES": {"en": "English", "de": "Deutsch"},
    "PARLER_DEFAULT_LANGUAGE_CODE": "en",
    "PARLER_LANGUAGES": ["en", "de"],
    "ALL_LANGUAGES_MANDATORY_DEFAULT": true,
    "SESSION_COOKIE_SECURE": true,
    "DEFAULT_FROM_EMAIL": "",
    "ADMINS": {}
}
EOF
until docker exec "$OLD-db" pg_isready -q -h 127.0.0.1 -U labcirs -d labcirs; do sleep 1; done
docker exec "$OLD-app" python manage.py migrate --noinput -v 0

step "old stack: synthetic data"
docker exec -i -e SEED_PASSWORD="$SEED_PASSWORD" "$OLD-app" python - < scripts/upgrade/seed_v521.py | tee "$ART/upgrade-seed.out"
expected=$(sed -n 's/^EXPECTED //p' "$ART/upgrade-seed.out")
[ -n "$expected" ] || fail "the seed script printed no EXPECTED line"
# the photo is a file in the media volume, as on a real server, and it carries the EXIF marker
# that check_upgraded.py looks for afterwards
docker exec "$OLD-app" find /media -type f
docker exec "$OLD-app" grep -rl SEED-EXIF-MARKER /media/photos >/dev/null || fail "the seed photo has no EXIF marker"

step "dump with pg_dump 17 (custom format)"
docker run --rm --network "$OLD" -v "$OLD-dump:/dump" -e PGPASSWORD="$OLD_DB_PASSWORD" postgres:17 \
    pg_dump -Fc -h "$OLD-db" -U labcirs -f /dump/labcirs.dump labcirs
docker run --rm -v "$OLD-dump:/dump:ro" postgres:17 ls -l /dump/labcirs.dump

step "new stack: configuration, image, empty PostgreSQL 17"
cp .env.example "$ART/upgrade.env"
cat >> "$ART/upgrade.env" <<EOF
LABCIRS_ENV_FILE=$ART/upgrade.env
LABCIRS_SECRET_KEY=upgrade$(openssl rand -hex 32)
POSTGRES_PASSWORD=$NEW_DB_PASSWORD
LABCIRS_DB_PASSWORD=$NEW_DB_PASSWORD
LABCIRS_ALLOWED_HOSTS='["localhost"]'
LABCIRS_CSRF_TRUSTED_ORIGINS='["https://localhost"]'
LABCIRS_LANGUAGES='{"en": "English", "de": "Deutsch"}'
LABCIRS_PARLER_LANGUAGES='["en", "de"]'
LABCIRS_TIME_ZONE=Europe/Berlin
EOF
dc build app
dc up -d --wait db

step "restore with pg_restore --no-owner"
docker run --rm --network "${NEW}_default" -v "$OLD-dump:/dump:ro" -e PGPASSWORD="$NEW_DB_PASSWORD" postgres:17 \
    pg_restore --no-owner -h db -U labcirs -d labcirs /dump/labcirs.dump

step "migrate"
dc run --rm -T app python manage.py migrate --noinput

step "remove_stale_contenttypes"
dc run --rm -T app python manage.py remove_stale_contenttypes --noinput

step "photos: copy into the media volume, give them to the app user"
dc run --rm -T --user root -v "$OLD-media:/old:ro" app sh -c 'cp -a /old/. /media/ && chown -R labcirs /media'

step "strip_photo_metadata (twice: the second run must find nothing to do)"
dc run --rm -T app python manage.py strip_photo_metadata | tee "$ART/upgrade-strip.out"
grep -qx '1 cleaned, 0 already clean, 0 failed' "$ART/upgrade-strip.out" || fail "unexpected summary of strip_photo_metadata"
dc run --rm -T app python manage.py strip_photo_metadata | tee "$ART/upgrade-strip2.out"
grep -qx '0 cleaned, 1 already clean, 0 failed' "$ART/upgrade-strip2.out" || fail "second run of strip_photo_metadata did not leave the photo alone"

step "checks"
dc run --rm -T -e EXPECTED="$expected" -e SEED_PASSWORD="$SEED_PASSWORD" app python - < scripts/upgrade/check_upgraded.py | tee "$ART/upgrade-check.out"
# the last line of check_upgraded.py, printed only when every check passed
grep -qx 'all checks passed' "$ART/upgrade-check.out" || fail "check_upgraded.py did not finish with 'all checks passed'"

step "start: the entrypoint migrates (nothing to do) and gunicorn answers"
dc up -d app
i=0
until dc exec -T app python -c "import urllib.request as u; r = u.Request('http://127.0.0.1:8000/incidents/test/', headers={'Host': 'localhost'}); print(u.urlopen(r).status)" 2>/dev/null | grep -qx 200; do
    i=$((i + 1))
    [ "$i" -le 30 ] || { dc logs app | tail -n 30 >&2; fail "the app did not answer within 60 s"; }
    sleep 2
done

echo
echo "UPGRADE OK"
echo "total $(($(date +%s) - t0)) s"
