"""Mapa territorial: onde moram os candidatos, por bairro, ponto a ponto ou por concentração (a partir do CEP)."""
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from candidatos.models import Candidato
from contas.models import Usuario
from core import geo
from core.constantes import VINCULO_ATIVO
from core.models import Configuracao, GeoArea, GeoCache, GeoEndereco, LogAtividade
from core.permissoes import EQUIPE_RECRUTAMENTO, SOMENTE_ADMIN, perfil_requerido
from core.utils import validar_cep
from painel import services as painel

from .dados import agregar

STATUS_PADRAO = [Candidato.Status.PENDENTE, Candidato.Status.APROVADO, Candidato.Status.EM_VAGA]
LIMITE_LINHAS = 60_000
LIMITE_CANDIDATOS = 150
TRAVA_SEGUNDOS = 120
LOTE_AGORA = 12

_OSM = '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>'
GRADIENTE_CALOR = {"0.2": "#cde2fb", "0.45": "#6da7ec", "0.7": "#256abf", "1.0": "#0d366b"}


def _int(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _fila_de_localizacao():
    pendentes = Candidato.objects.filter(geo_status=Candidato.GeoStatus.PENDENTE).count()
    falharam = Candidato.objects.filter(geo_status=Candidato.GeoStatus.FALHOU).count()
    return {"pending": pendentes, "failed": falharam, "total": pendentes + falharam}


@require_GET
def mapa(request):
    config = Configuracao.atual()
    usuario = request.user
    configuracao_js = {
        "dataUrl": reverse("mapa:dados"), "candidatosApi": reverse("mapa:candidatos"), "geoUrl": reverse("mapa:geocodificar"), "relocUrl": reverse("mapa:relocalizar"),
        "candidatosUrl": reverse("candidatos:lista"), "sprite": static("icons/sprite.svg"),
        "podeVer": usuario.pode_recrutar, "podeLocalizar": usuario.pode_recrutar, "ehAdmin": usuario.eh_admin,
        "modes": [{"value": "bairros", "label": "Bairros"}, {"value": "pontos", "label": "Pontos"}, {"value": "calor", "label": "Calor"}],
        "defaultMode": "bairros", "defaultBasemap": "ruas",
        "basemaps": {
            "ruas": {"label": "Ruas", "url": settings.RRPA_MAPA_TILES_URL, "attribution": settings.RRPA_MAPA_ATRIBUICAO, "maxZoom": 19},
            "claro": {"label": "Claro", "url": settings.RRPA_MAPA_TILES_URL, "attribution": settings.RRPA_MAPA_ATRIBUICAO, "maxZoom": 19,
                      "className": "rrpa-tiles-claro"},
        },
        "center": [-14.235, -51.925], "zoom": 4, "fitMaxZoom": 15, "focusZoom": 14,
        "heatGradient": GRADIENTE_CALOR, "heatOptions": {"radius": 24, "blur": 20, "maxZoom": 16, "minOpacity": 0.35},
        "clusterOptions": {"maxClusterRadius": 52},
        "unidade": {"nome": config.nome_unidade, "lat": config.latitude, "lng": config.longitude},
        "rotulos": dict(Candidato.Status.choices),
    }
    return render(request, "mapa/mapa.html", {
        "vagas": painel.vagas_do_usuario(usuario).order_by("nome"),
        "status_opcoes": list(Candidato.Status.choices), "status_padrao": [s.value for s in STATUS_PADRAO],
        "config_mapa": configuracao_js, "fila": _fila_de_localizacao() if usuario.pode_recrutar else None,
        "pode_localizar": usuario.pode_recrutar, "eh_admin": usuario.eh_admin,
    })


def _candidatos_filtrados(request, config):
    """Candidatos do mapa no escopo de quem pede: quem está na vaga E quem se candidatou a ela (vagas de interesse)."""
    usuario = request.user
    status = [s for s in request.GET.get("status", "").split(",") if s in Candidato.Status.values] or [s.value for s in STATUS_PADRAO]
    qs = Candidato.objects.filter(status__in=status)
    if usuario.eh_gestor:  # responsável do setor: só quem está nas vagas dele ou se candidatou a elas
        qs = qs.filter(
            Q(vinculos__vaga__responsaveis=usuario, vinculos__status=VINCULO_ATIVO) | Q(vagas_interesse__responsaveis=usuario)
        )
    vaga_id = _int(request.GET.get("vaga"))
    if vaga_id:
        qs = qs.filter(Q(vagas_interesse=vaga_id) | Q(vinculos__vaga=vaga_id, vinculos__status=VINCULO_ATIVO))
    busca = request.GET.get("q", "").strip()
    if busca:
        filtro = Q(bairro__icontains=busca) | Q(cidade__icontains=busca)
        if usuario.pode_recrutar:  # a equipe também busca pelo nome da pessoa e pela vaga
            filtro |= Q(nome__icontains=busca) | Q(vagas_interesse__nome__icontains=busca)
        qs = qs.filter(filtro)
    return qs.distinct()


def _dentro_do_raio(request, config, lat, lng):
    ate_km = request.GET.get("ate_km", "")
    if not (ate_km.replace(".", "", 1).isdigit() and config.tem_coordenadas):
        return True
    return lat is not None and lng is not None and geo.haversine_km(config.latitude, config.longitude, lat, lng) <= float(ate_km)


@never_cache
@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def candidatos(request):
    """Quem são os candidatos de um bairro (ou um ponto) do mapa, com a vaga a que se candidataram. Só a equipe de recrutamento."""
    config = Configuracao.atual()
    qs = _candidatos_filtrados(request, config)
    unico = _int(request.GET.get("id"))
    if unico:
        qs = qs.filter(pk=unico)
    elif "bairro" in request.GET:
        qs = qs.filter(bairro=request.GET.get("bairro", "").strip(), cidade=request.GET.get("cidade", "").strip())
    else:
        return JsonResponse({"erro": "Informe o bairro ou o candidato."}, status=400)
    itens = []
    for c in qs.prefetch_related("vagas_interesse").order_by("nome"):
        if not _dentro_do_raio(request, config, c.latitude, c.longitude):
            continue
        vinculo = c.vinculo_ativo
        itens.append({
            "id": c.pk, "nome": c.nome, "status": c.status, "situacao": c.get_status_display(), "telefone": c.telefone_formatado,
            "whatsapp_ok": c.whatsapp_ok, "vagas": [v.nome for v in c.vagas_interesse.all()],
            "na_vaga": vinculo.vaga.nome if vinculo else "", "url": c.get_absolute_url(), "localizado": c.localizado,
        })
    return JsonResponse({"total": len(itens), "candidatos": itens[:LIMITE_CANDIDATOS]})


@never_cache
@require_GET
def dados(request):
    """Dados agregados (bairros, pontos, limites) no escopo de quem pede. Sem nomes, CPFs nem telefones."""
    config = Configuracao.atual()
    usuario = request.user
    qs = _candidatos_filtrados(request, config)
    linhas = list(qs.values(
        "id", "latitude", "longitude", "bairro", "cidade", "uf", "status", "whatsapp_ok", "indicado_por_id", "precisao_geo",
    )[:LIMITE_LINHAS])
    for linha in linhas:
        linha["precisao"] = linha.pop("precisao_geo")

    linhas = [l for l in linhas if _dentro_do_raio(request, config, l["latitude"], l["longitude"])]

    ids = {l["indicado_por_id"] for l in linhas if l["indicado_por_id"]}
    nomes = {u.pk: u.nome_exibicao for u in Usuario.objects.filter(pk__in=ids)}
    carga = agregar(linhas, nomes, pode_ver_candidatos=usuario.pode_recrutar)
    carga["unidade"] = {"nome": config.nome_unidade, "lat": config.latitude, "lng": config.longitude}
    return JsonResponse(carga)


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def geocodificar(request):
    """Localiza agora alguns candidatos pendentes (o processo em segundo plano faz o resto)."""
    if not cache.add("mapa:geocodificar", 1, TRAVA_SEGUNDOS):
        return JsonResponse({"erro": "Já existe uma localização em andamento. Aguarde alguns instantes."}, status=429)
    try:
        geo.localizar_unidade()  # o ponto da unidade acompanha o CEP dela (não consulta a rede se já estiver certo)
        # Devolve à fila os que falharam antes (o serviço de CEP pode ter estado fora do ar)
        Candidato.objects.filter(geo_status=Candidato.GeoStatus.FALHOU).exclude(cep="").update(geo_status=Candidato.GeoStatus.PENDENTE)
        lote = list(Candidato.objects.filter(geo_status=Candidato.GeoStatus.PENDENTE).order_by("id")[:LOTE_AGORA])
        localizados = sum(bool(geo.aplicar_localizacao(c)) for c in lote)
    finally:
        cache.delete("mapa:geocodificar")
    fila = _fila_de_localizacao()
    LogAtividade.registrar(request.user, "mapa.localizar", f"Localizou {localizados} de {len(lote)} candidatos no mapa", request=request)
    return JsonResponse({"processados": len(lote), "localizados": localizados, "restantes": fila["pending"], "falharam": fila["failed"]})


@require_POST
@perfil_requerido(*SOMENTE_ADMIN)
def relocalizar(request):
    """Manda todos os candidatos com CEP de volta à fila e esquece as consultas antigas de CEP (inclusive a da unidade)."""
    ceps = [c for c in Candidato.objects.exclude(cep="").values_list("cep", flat=True).distinct() if validar_cep(c)]
    unidade = Configuracao.atual().cep_unidade
    esquecer = ceps + ([unidade] if validar_cep(unidade) else [])  # o worker acerta o ponto da unidade logo em seguida
    GeoCache.objects.filter(cep__in=esquecer).exclude(fonte="demo").delete()
    GeoArea.objects.filter(encontrado=False).delete()  # bairros que o OpenStreetMap não achou: perguntar de novo
    GeoEndereco.objects.all().delete()  # endereços com número (da unidade): procurar de novo
    total = Candidato.objects.filter(cep__in=ceps).update(geo_status=Candidato.GeoStatus.PENDENTE)
    LogAtividade.registrar(request.user, "mapa.relocalizar", f"Mandou {total} candidatos para relocalização", request=request)
    return JsonResponse({"recolocados": total})
