#!/bin/sh
# Prepara o banco e depois inicia o processo pedido (gunicorn, por padrão).
set -e

if [ "${RRPA_PREPARAR:-1}" = "1" ]; then
  echo "[rrpa] aguardando o banco de dados..."
  python manage.py wait_for_db
  echo "[rrpa] aplicando migrações..."
  python manage.py migrate --noinput
  python manage.py createcachetable
  echo "[rrpa] preparando dados iniciais..."
  python manage.py bootstrap
fi

exec "$@"
