from django.conf import settings
from django.contrib import messages
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.generic import ListView

from whatsapp.models import ConexaoWhatsApp

from . import geo, ratelimit
from .forms import ConfiguracaoForm
from .models import Configuracao, LogAtividade
from .permissoes import SOMENTE_ADMIN, PerfilRequeridoMixin, perfil_requerido
from .utils import base_local, gerar_token, ip_do_cliente, url_absoluta, validar_cep


@require_GET
def saude(request):
    """Verificação de saúde usada pelo Docker."""
    connection.ensure_connection()
    return JsonResponse({"status": "ok"})


@require_GET
def consultar_cep_api(request, cep):
    """Endereço (e coordenadas) de um CEP — alimenta o preenchimento automático dos formulários."""
    ip = ip_do_cliente(request) or "?"
    chave = f"cep:{ip}"
    if ratelimit.excedeu(chave, 60):
        return JsonResponse({"erro": "Muitas consultas seguidas. Aguarde um minuto."}, status=429)
    ratelimit.registrar(chave, 60)
    if not validar_cep(cep):
        return JsonResponse({"erro": "CEP inválido."}, status=400)
    cache = geo.consultar_cep(cep)
    if cache is None:
        return JsonResponse({"erro": "Não foi possível consultar o CEP agora. Preencha o endereço manualmente."}, status=503)
    if not cache.encontrado:
        return JsonResponse({"erro": "CEP não encontrado."}, status=404)
    return JsonResponse({
        "cep": cache.cep, "logradouro": cache.logradouro, "bairro": cache.bairro, "cidade": cache.cidade,
        "uf": cache.uf, "latitude": cache.latitude, "longitude": cache.longitude, "precisao": cache.precisao,
    })


@perfil_requerido(*SOMENTE_ADMIN)
def configuracoes(request):
    config = Configuracao.atual()
    if request.method == "POST":
        acao = request.POST.get("acao", "salvar")
        if acao == "gerar_link_publico":
            config.painel_publico_token = gerar_token(16)
            config.save(update_fields=["painel_publico_token"])
            LogAtividade.registrar(request.user, "config.painel_publico", "Gerou o link do painel compartilhável", request=request)
            messages.success(request, "Link do painel compartilhável criado.")
            return redirect("core:configuracoes")
        if acao == "revogar_link_publico":
            config.painel_publico_token = ""
            config.save(update_fields=["painel_publico_token"])
            LogAtividade.registrar(request.user, "config.painel_publico", "Desativou o link do painel compartilhável", request=request)
            messages.success(request, "O link do painel compartilhável foi desativado.")
            return redirect("core:configuracoes")
        form = ConfiguracaoForm(request.POST, instance=config)
        if form.is_valid():
            antigo_cep = config.cep_unidade
            config = form.save()
            if config.cep_unidade and (config.cep_unidade != antigo_cep or not config.tem_coordenadas):
                cache = geo.consultar_cep(config.cep_unidade)
                if cache and cache.latitude is not None:
                    config.latitude, config.longitude = cache.latitude, cache.longitude
                    partes = [cache.logradouro, cache.bairro, cache.cidade]
                    config.endereco_unidade = ", ".join(p for p in partes if p)[:200]
                    config.save(update_fields=["latitude", "longitude", "endereco_unidade"])
                else:
                    messages.warning(request, "Não consegui localizar o CEP da unidade agora; o mapa não calculará distâncias até isso funcionar.")
            elif not config.cep_unidade:
                config.latitude = config.longitude = None
                config.endereco_unidade = ""
                config.save(update_fields=["latitude", "longitude", "endereco_unidade"])
            LogAtividade.registrar(request.user, "config.salva", "Atualizou as configurações do sistema", request=request)
            messages.success(request, "Configurações salvas.")
            return redirect("core:configuracoes")
    else:
        form = ConfiguracaoForm(instance=config)
    url_painel = url_absoluta(request, reverse("painel:publico", args=[config.painel_publico_token])) if config.painel_publico_token else ""
    return render(request, "core/configuracoes.html", {
        "form": form, "config": config, "aviso_local": base_local(request), "url_painel": url_painel,
        "conexao_whatsapp": ConexaoWhatsApp.atual() if settings.RRPA_WHATSAPP_API else None,
    })


class AuditoriaView(PerfilRequeridoMixin, ListView):
    perfis_permitidos = SOMENTE_ADMIN
    model = LogAtividade
    template_name = "core/auditoria.html"
    paginate_by = 40
    context_object_name = "atividades"

    def get_queryset(self):
        qs = LogAtividade.objects.select_related("usuario")
        q = self.request.GET.get("q", "").strip()
        grupo = self.request.GET.get("grupo", "").strip()
        if q:
            qs = qs.filter(descricao__icontains=q)
        if grupo:
            qs = qs.filter(acao__startswith=f"{grupo}.")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["grupos"] = [("candidato", "Candidatos"), ("vinculo", "Vínculos"), ("checkin", "Check-ins"),
                         ("vaga", "Vagas"), ("mensagem", "Mensagens"), ("link", "Links de cadastro"),
                         ("usuario", "Usuários"), ("config", "Configurações")]
        if settings.RRPA_WHATSAPP_API:
            ctx["grupos"] += [("disparo", "Disparos de WhatsApp"), ("whatsapp", "Conexão do WhatsApp")]
        ctx["q"] = self.request.GET.get("q", "")
        ctx["grupo"] = self.request.GET.get("grupo", "")
        return ctx
