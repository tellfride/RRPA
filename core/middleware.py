from django.conf import settings
from django.contrib.auth.views import redirect_to_login

# Caminhos que funcionam sem login: tela de acesso, cadastro por link, consulta de CEP,
# painel compartilhado, webhook do WhatsApp (o provedor chama; a URL tem token secreto e o corpo é assinado),
# verificação de saúde e arquivos estáticos.
PREFIXOS_PUBLICOS = (
    "/login/",
    "/cadastro/",
    "/api/cep/",
    "/painel/publico/",
    "/webhook/",
    "/saude/",
    "/static/",
    "/favicon.ico",
)


class LoginObrigatorioMiddleware:
    """Seguro por padrão: qualquer tela nova exige login, a menos que esteja na lista acima."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not request.path.startswith(PREFIXOS_PUBLICOS):
            return redirect_to_login(request.get_full_path(), login_url=settings.LOGIN_URL)
        return self.get_response(request)


class CabecalhosSegurancaMiddleware:
    """Content-Security-Policy restritiva: só scripts do próprio sistema."""

    POLITICA = "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resposta = self.get_response(request)
        resposta.headers.setdefault("Content-Security-Policy", self.POLITICA)
        resposta.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        if request.path.startswith(("/cadastro/", "/painel/publico/", "/webhook/")):
            # O endereço contém um token secreto: não repassar em nenhum cabeçalho Referer.
            resposta.headers["Referrer-Policy"] = "no-referrer"
            resposta.headers.setdefault("Cache-Control", "no-store")
        return resposta
