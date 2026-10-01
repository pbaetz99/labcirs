#!/usr/bin/env bash
# Creates .env from .env.example, fills the empty secrets with random values and asks for the
# SMTP password. Values that are already set are never overwritten, so the script can run again.
# Secrets are never printed. Usage: bash scripts/setup.sh
set -eu
cd "$(dirname "$0")/.."
umask 077

command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
[ -f .env.example ] || { echo ".env.example not found" >&2; exit 1; }

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example"
fi
chmod 600 .env

# Value of KEY in .env (empty if the key is missing or empty)
get() { sed -n "s/^$1=//p" .env | head -n 1; }

# Sets KEY to VALUE if the line "KEY=" is empty. Returns 1 if it is not. The value passes through
# the environment, so awk does not interpret backslashes in it.
fill() {
    grep -qx "$1=" .env || return 1
    VALUE=$2 awk -v key="$1" '$0 == key "=" { print key "=" ENVIRON["VALUE"]; next } { print }' .env > .env.tmp
    mv .env.tmp .env
}

# Random letters and digits of the given length. Always contains a letter: the app decodes
# values as JSON, so a value of pure digits would become a number.
random() {
    local value
    while :; do
        value=$(openssl rand -base64 96 | tr -dc 'A-Za-z0-9' | cut -c1-"$1")
        case $value in
            *[A-Za-z]*) [ "${#value}" -eq "$1" ] && { printf '%s' "$value"; return; } ;;
        esac
    done
}

if fill LABCIRS_SECRET_KEY "$(random 64)"; then echo "Generated LABCIRS_SECRET_KEY"; fi
if fill POSTGRES_PASSWORD "$(random 32)"; then echo "Generated POSTGRES_PASSWORD"; fi
# The app has to use the password of the bundled database.
db_password=$(get POSTGRES_PASSWORD)
if [ -n "$db_password" ] && fill LABCIRS_DB_PASSWORD "$db_password"; then
    echo "Set LABCIRS_DB_PASSWORD to the value of POSTGRES_PASSWORD"
fi

if grep -qx 'LABCIRS_EMAIL_HOST_PASSWORD=' .env; then
    printf 'SMTP password (input is hidden, Enter to skip): ' >&2
    read -rs smtp_password || smtp_password=
    echo >&2
    case $smtp_password in
        '') ;;
        *\'* | *\\* | *[[:cntrl:]]*)
            echo "The password contains a single quote, a backslash or a control character, which .env cannot hold safely." >&2
            echo "Enter LABCIRS_EMAIL_HOST_PASSWORD in .env yourself." >&2 ;;
        *)
            # Stored as a JSON string, so that a password like 12345678 stays text
            json=$(printf '%s' "$smtp_password" | sed 's/"/\\"/g')
            fill LABCIRS_EMAIL_HOST_PASSWORD "'\"$json\"'" && echo "Stored LABCIRS_EMAIL_HOST_PASSWORD" ;;
    esac
fi

chmod 600 .env
echo "Done. Edit .env (LABCIRS_SERVER_NAME, LABCIRS_ALLOWED_HOSTS, LABCIRS_CSRF_TRUSTED_ORIGINS,"
echo "LABCIRS_DEFAULT_FROM_EMAIL, TLS_CERT_DIR), then start with: docker compose up -d --build"
