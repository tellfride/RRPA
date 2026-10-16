"""Configurações do R-RPA. Tudo que muda entre ambientes vem de variáveis de ambiente (.env.example)."""
import os
import secrets
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("RRPA_DATA_DIR") or BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None or valor.strip() == "":
        return padrao
    return valor.strip().lower() in {"1", "true", "t", "yes", "y", "sim", "s", "on"}


def env_lista(nome, padrao=""):
    return [item.strip() for item in os.environ.get(nome, padrao).split(",") if item.strip()]


def _chave_secreta():
    """Usa DJANGO_SECRET_KEY; sem ela, gera uma chave única e a guarda no volume de dados."""
    chave = os.environ.get("DJANGO_SECRET_KEY", "").strip()
    if chave:
        return chave
    arquivo = DATA_DIR / "secret_key"
    try:
        fd = os.open(arquivo, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(64))
    for _ in range(40):  # outro processo pode estar escrevendo a chave neste instante
        conteudo = arquivo.read_text().strip()
        if conteudo:
            return conteudo
        time.sleep(0.05)
    raise RuntimeError(f"Não foi possível ler a chave secreta em {arquivo}")


SECRET_KEY = _chave_secreta()
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_lista("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
for _interno in ("localhost", "127.0.0.1"):  # a verificação de saúde do Docker chama o site por dentro do container
    if _interno not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_interno)
CSRF_TRUSTED_ORIGINS = env_lista("DJANGO_CSRF_TRUSTED_ORIGINS")

# ---------------------------------------------------------------- aplicações
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "contas",
    "vagas",
    "candidatos",
    "vinculos",
    "mensagens",
    "whatsapp",
    "painel",
    "mapa",
]

AUTH_USER_MODEL = "contas.Usuario"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.LoginObrigatorioMiddleware",
    "core.middleware.CabecalhosSegurancaMiddleware",
]

ROOT_URLCONF = "rrpa.urls"
WSGI_APPLICATION = "rrpa.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.rrpa",
            ],
            # Disponíveis em todo template, sem {% load %}.
            "builtins": [
                "core.templatetags.rrpa",
                "django.contrib.humanize.templatetags.humanize",
                "django.templatetags.l10n",
            ],
        },
    },
]

# ---------------------------------------------------------------- banco de dados
if os.environ.get("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "rrpa"),
            "USER": os.environ.get("POSTGRES_USER", "rrpa"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
            "HOST": os.environ["POSTGRES_HOST"],
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:  # desenvolvimento local sem Docker
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": DATA_DIR / "db.sqlite3",
        }
    }

# Cache em banco: contadores de limite de tentativas valem para todos os processos.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "rrpa_cache",
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- autenticação
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "contas:login"
LOGIN_REDIRECT_URL = "painel:inicio"
LOGOUT_REDIRECT_URL = "contas:login"
SESSION_COOKIE_AGE = 60 * 60 * 12
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------- idioma e hora
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- arquivos estáticos
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_ROOT.mkdir(exist_ok=True)  # o WhiteNoise reclama se a pasta não existir (ex.: antes do collectstatic)
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
WHITENOISE_USE_FINDERS = DEBUG
WHITENOISE_AUTOREFRESH = DEBUG

# Planilhas enviadas são lidas na memória; limite de 5 MB.
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---------------------------------------------------------------- segurança
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
RRPA_CONFIAR_PROXY = env_bool("RRPA_CONFIAR_PROXY", False)
if env_bool("RRPA_HTTPS", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("RRPA_HSTS_SEGUNDOS", "0") or 0)
    RRPA_CONFIAR_PROXY = True

# ---------------------------------------------------------------- R-RPA
RRPA_VERSAO = "1.1.2"
RRPA_BASE_URL = os.environ.get("RRPA_BASE_URL", "").strip().rstrip("/")
# Disparo automático de WhatsApp por API (app whatsapp + "Disparos"). Desligado por padrão: o envio normal abre o
# WhatsApp com a mensagem pronta (wa.me). Com 0 as telas, o webhook e o envio da fila ficam inativos.
RRPA_WHATSAPP_API = env_bool("RRPA_WHATSAPP_API", False)
RRPA_DEMO = env_bool("RRPA_SEED_DEMO", False)
RRPA_DEMO_SENHA = os.environ.get("RRPA_DEMO_SENHA", "demo1234")
RRPA_MAPA_TILES_URL = os.environ.get("RRPA_MAPA_TILES_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
RRPA_MAPA_ATRIBUICAO = os.environ.get(
    "RRPA_MAPA_ATRIBUICAO", '&copy; <a href="https://www.openstreetmap.org/copyright">Colaboradores do OpenStreetMap</a>'
)
_contato = os.environ.get("RRPA_GEOCODER_EMAIL", "").strip()
RRPA_GEOCODER_USER_AGENT = "R-RPA/1.0 (sistema interno de recrutamento)" + (f" {_contato}" if _contato else "")

# ---------------------------------------------------------------- logs
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simples": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simples"}},
    "root": {"handlers": ["console"], "level": os.environ.get("RRPA_LOG_NIVEL", "INFO")},
}
