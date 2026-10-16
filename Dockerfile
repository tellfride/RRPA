# R-RPA — imagem única usada pelo site (gunicorn) e pelo worker (disparos de WhatsApp e geocodificação).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RRPA_DATA_DIR=/app/data

WORKDIR /app

# Dependências primeiro, para aproveitar o cache de camadas.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuário sem privilégios + arquivos estáticos prontos para o WhiteNoise.
RUN useradd --create-home --uid 10001 rrpa \
    && mkdir -p /app/data /app/staticfiles \
    && sed -i 's/\r$//' /app/docker/entrypoint.sh \
    && chmod +x /app/docker/entrypoint.sh \
    && DJANGO_SECRET_KEY=somente-para-o-build python manage.py collectstatic --noinput \
    && chown -R rrpa:rrpa /app
USER rrpa

EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "rrpa.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
