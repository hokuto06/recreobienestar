#!/bin/bash
set -e

echo "Aplicando migraciones..."
python manage.py migrate --noinput

echo "Recolectando estáticos..."
python manage.py collectstatic --noinput

echo "Iniciando Gunicorn..."
# Single worker: this app shares a 1GB t2.micro with several other
# containers. Raise --workers only after confirming headroom (see
# deliverables doc for the memory measurements this was set against).
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:${APP_PORT:-8100} \
    --workers 1 \
    --threads 2 \
    --timeout 60
