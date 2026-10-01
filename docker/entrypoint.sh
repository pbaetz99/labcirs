#!/bin/sh
# Entrypoint of the app image. With a command (docker compose run app python manage.py ...)
# it runs that command. Without one it migrates the database and starts gunicorn.
# gunicorn writes no access log, so no client address is logged. Its control socket is not used.
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

python manage.py migrate --noinput
exec gunicorn labcirs.wsgi:application \
    --bind "${GUNICORN_BIND:-0.0.0.0:8000}" \
    --workers "${GUNICORN_WORKERS:-3}" \
    --no-control-socket
