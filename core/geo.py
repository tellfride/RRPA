"""Consulta de CEP e localização no mapa.

Cadeia de consulta (cada resposta fica em GeoCache, então um CEP é consultado uma única vez):
  1. BrasilAPI v2  -> endereço do CEP (ViaCEP quando a BrasilAPI não conhece o CEP)
  2. Nominatim/OSM -> limites do bairro (GeoArea), para conferir qualquer ponto antes de aceitá-lo
  3. AwesomeAPI    -> coordenadas do CEP; só valem se caírem dentro do bairro
  4. Nominatim/OSM -> a rua dentro do bairro; se não achar, o centro do bairro; se não, o centro da cidade

As coordenadas que a BrasilAPI manda junto com o endereço NÃO são usadas. Para os CEPs do Rio de Janeiro ela devolve
sempre o mesmo ponto (-22.90642, -43.18223, no Centro): numa amostra de 16 bairros todos caíram ali, a até 50 km do
lugar certo, e Guadalupe aparecia no centro da cidade. Por isso todo ponto é conferido contra os limites do bairro.
"""
import logging
import math
import threading
import time
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .models import Configuracao, GeoArea, GeoCache
from .utils import normalizar_cep, sem_acentos, validar_cep

log = logging.getLogger(__name__)

TIMEOUT = 6
REPETIR_CEP_SEM_COORDENADAS_APOS = timedelta(days=7)
REPETIR_AREA_DESCONHECIDA_APOS = timedelta(days=30)

KM_POR_GRAU = 111.2
MARGEM_KM = 0.7  # tolerância ao conferir se um ponto está no bairro: os limites do OSM são aproximados
MEIA_CAIXA_MINIMA_KM = 1.0  # lugares que o OSM só tem como ponto (sem contorno) ganham uma caixa deste tamanho

# Tipos de lugar do Nominatim (campo addresstype) aceitos como "o bairro" e "a cidade" procurados.
_TIPOS_BAIRRO = {"suburb", "neighbourhood", "quarter", "city_district", "borough", "district", "hamlet", "residential"}
_TIPOS_CIDADE = {"city", "town", "village", "municipality"}
_CHAVES_DE_CIDADE = ("city", "town", "village", "municipality")

_trava_nominatim = threading.Lock()
_ultimo_nominatim = 0.0


class ServicoIndisponivel(Exception):
    """Falha de rede ou serviço fora do ar: não deve ser gravada como 'CEP inexistente'."""


def haversine_km(lat1, lon1, lat2, lon2):
    """Distância em linha reta (km) entre dois pontos."""
    raio = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * raio * math.asin(math.sqrt(a))


def _float(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _comparavel(texto):
    return " ".join(sem_acentos(texto).casefold().split())


def _get_json(url, params=None):
    try:
        resposta = requests.get(
            url, params=params, timeout=TIMEOUT,
            headers={"User-Agent": settings.RRPA_GEOCODER_USER_AGENT, "Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise ServicoIndisponivel(str(exc)) from exc
    if resposta.status_code in (400, 404):
        return None
    if resposta.status_code >= 500 or resposta.status_code == 429:
        raise ServicoIndisponivel(f"HTTP {resposta.status_code} em {url}")
    try:
        return resposta.json()
    except ValueError:
        return None


# ---------------------------------------------------------------- fontes de endereço
def _brasilapi(cep):
    # O "location" desta resposta é ignorado de propósito (veja o texto no topo do arquivo).
    dados = _get_json(f"https://brasilapi.com.br/api/cep/v2/{cep}")
    if not isinstance(dados, dict) or "cep" not in dados:
        return None
    return {
        "logradouro": dados.get("street") or "",
        "bairro": dados.get("neighborhood") or "",
        "cidade": dados.get("city") or "",
        "uf": dados.get("state") or "",
    }


def _viacep(cep):
    dados = _get_json(f"https://viacep.com.br/ws/{cep}/json/")
    if not isinstance(dados, dict) or dados.get("erro"):
        return None
    return {
        "logradouro": dados.get("logradouro") or "",
        "bairro": dados.get("bairro") or "",
        "cidade": dados.get("localidade") or "",
        "uf": dados.get("uf") or "",
    }


# ---------------------------------------------------------------- fontes de coordenadas
def _no_brasil(lat, lng):
    return lat is not None and lng is not None and -34.0 <= lat <= 6.0 and -74.5 <= lng <= -28.0


def _awesomeapi(cep):
    """Coordenadas que a AwesomeAPI tem para o CEP, ou None. É uma fonte extra: se falhar, a busca segue sem ela."""
    dados = _get_json(f"https://cep.awesomeapi.com.br/json/{cep}")
    if not isinstance(dados, dict):
        return None
    lat, lng = _float(dados.get("lat")), _float(dados.get("lng"))
    return (lat, lng) if _no_brasil(lat, lng) else None


def _nominatim_busca(**consulta):
    """Uma busca no Nominatim (lista de resultados), respeitando o limite de 1 requisição por segundo da política de uso."""
    global _ultimo_nominatim
    with _trava_nominatim:
        espera = 1.1 - (time.monotonic() - _ultimo_nominatim)
        if espera > 0:
            time.sleep(espera)
        try:
            dados = _get_json(
                "https://nominatim.openstreetmap.org/search",
                {"format": "jsonv2", "limit": 1, "countrycodes": "br", **consulta},
            )
        finally:
            _ultimo_nominatim = time.monotonic()
    return dados if isinstance(dados, list) else []


def _nominatim(**consulta):
    """Primeiro ponto (lat, lng) que o Nominatim acha para a busca, ou None."""
    for achado in _nominatim_busca(**consulta):
        lat, lng = _float(achado.get("lat")), _float(achado.get("lon"))
        if lat is not None and lng is not None:
            return lat, lng
    return None


# ---------------------------------------------------------------- limites do bairro / da cidade
def _chave_area(bairro, cidade, uf):
    return "|".join((uf.upper(), _comparavel(cidade), _comparavel(bairro)))


def _eh_o_lugar(achado, bairro, cidade, uf):
    """O resultado do Nominatim é mesmo o bairro (ou a cidade) procurado, e não um homônimo de outra cidade ou estado?

    "Centro", por exemplo, existe em quase todas as cidades: sem esta conferência a busca devolvia o de outra.
    """
    endereco = achado.get("address") or {}
    if achado.get("addresstype") not in (_TIPOS_BAIRRO if bairro else _TIPOS_CIDADE):
        return False
    if _comparavel(achado.get("name")) != _comparavel(bairro or cidade):
        return False
    iso = endereco.get("ISO3166-2-lvl4")
    if uf and iso and iso.upper() != f"BR-{uf.upper()}":
        return False
    if bairro:
        return any(_comparavel(endereco.get(chave)) == _comparavel(cidade) for chave in _CHAVES_DE_CIDADE)
    return True


def _alargar_caixa(sul, norte, oeste, leste, lat, lng):
    """Garante um tamanho mínimo à caixa de lugares que o OSM só tem como ponto."""
    meia_lat = MEIA_CAIXA_MINIMA_KM / KM_POR_GRAU
    meia_lng = MEIA_CAIXA_MINIMA_KM / (KM_POR_GRAU * max(math.cos(math.radians(lat)), 0.2))
    if norte - sul < 2 * meia_lat:
        sul, norte = min(sul, lat - meia_lat), max(norte, lat + meia_lat)
    if leste - oeste < 2 * meia_lng:
        oeste, leste = min(oeste, lng - meia_lng), max(leste, lng + meia_lng)
    return sul, norte, oeste, leste


def _buscar_area(bairro, cidade, uf):
    """Limites do bairro (ou da cidade, se `bairro` vier vazio) segundo o OSM, ou None. Levanta ServicoIndisponivel."""
    consulta = ", ".join(parte for parte in (bairro, cidade, uf, "Brasil") if parte)
    for achado in _nominatim_busca(q=consulta, limit=8, addressdetails=1):
        if not _eh_o_lugar(achado, bairro, cidade, uf):
            continue
        try:
            sul, norte, oeste, leste = (float(x) for x in achado["boundingbox"])
            lat, lng = float(achado["lat"]), float(achado["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        sul, norte, oeste, leste = _alargar_caixa(sul, norte, oeste, leste, lat, lng)
        return {"sul": sul, "norte": norte, "oeste": oeste, "leste": leste, "latitude": lat, "longitude": lng}
    return None


def _area_conhecida(bairro, cidade, uf):
    """GeoArea do bairro (ou da cidade) se o OSM a conhece; consulta o OSM só na primeira vez e guarda a resposta."""
    chave = _chave_area(bairro, cidade, uf)
    area = GeoArea.objects.filter(pk=chave).first()
    if area and (area.encontrado or timezone.now() - area.atualizado_em < REPETIR_AREA_DESCONHECIDA_APOS):
        return area if area.encontrado else None
    achada = _buscar_area(bairro, cidade, uf)
    campos = {"sul": None, "norte": None, "oeste": None, "leste": None, "latitude": None, "longitude": None}
    area, _ = GeoArea.objects.update_or_create(pk=chave, defaults={
        "tipo": GeoArea.Tipo.BAIRRO if bairro else GeoArea.Tipo.CIDADE, "nome": bairro, "cidade": cidade,
        "uf": uf.upper()[:2], "encontrado": achada is not None, **campos, **(achada or {}),
    })
    return area if area.encontrado else None


def _area(bairro, cidade, uf):
    """Área contra a qual conferir os pontos do CEP: o bairro; se o OSM não o conhece, a cidade; senão None."""
    if not cidade:
        return None
    for nome in ((bairro, "") if bairro else ("",)):
        area = _area_conhecida(nome, cidade, uf)
        if area:
            return area
    return None


def _caixa_com_margem(area):
    """(sul, norte, oeste, leste) da área, alargada pela margem de tolerância."""
    lat_ref = area.latitude if area.latitude is not None else (area.sul + area.norte) / 2
    margem_lat = MARGEM_KM / KM_POR_GRAU
    margem_lng = MARGEM_KM / (KM_POR_GRAU * max(math.cos(math.radians(lat_ref)), 0.2))
    return area.sul - margem_lat, area.norte + margem_lat, area.oeste - margem_lng, area.leste + margem_lng


def _no_lugar(ponto, area):
    """O ponto cai dentro da área? Sem área conhecida não há como contestá-lo."""
    if area is None or area.sul is None:
        return True
    sul, norte, oeste, leste = _caixa_com_margem(area)
    lat, lng = ponto
    return sul <= lat <= norte and oeste <= lng <= leste


# ---------------------------------------------------------------- resolução do CEP
def _rua_no_osm(base, area):
    """Ponto da rua do CEP no OSM. Com o bairro conhecido a busca fica restrita a ele: 'Avenida Brasil' existe em dezenas."""
    consulta = {"street": base["logradouro"], "city": base["cidade"], "state": base["uf"]}
    if area is None or area.tipo != GeoArea.Tipo.BAIRRO:
        return _nominatim(**consulta)
    # Primeiro estritamente dentro do bairro; só se a rua não estiver lá, com a folga dos limites aproximados do OSM
    # (senão um CEP de Deodoro cairia no trecho da mesma avenida que já é de Guadalupe).
    for sul, norte, oeste, leste in ((area.sul, area.norte, area.oeste, area.leste), _caixa_com_margem(area)):
        ponto = _nominatim(**consulta, viewbox=f"{oeste},{norte},{leste},{sul}", bounded=1)
        if ponto:
            return ponto
    return None


def _melhor_ponto(cep, base, area):
    """((lat, lng), fonte) do melhor ponto que cai dentro do bairro, ou (None, ""). Levanta ServicoIndisponivel se o OSM falhar."""
    try:
        ponto = _awesomeapi(cep)
    except ServicoIndisponivel as exc:  # fonte extra: sem ela a rua no OpenStreetMap ainda resolve
        log.info("AwesomeAPI indisponível (%s): %s", cep, exc)
        ponto = None
    if ponto and _no_lugar(ponto, area):
        return ponto, "awesomeapi"
    if ponto:
        log.info("CEP %s: ponto %s da AwesomeAPI cai fora de %s; procurando a rua no OpenStreetMap", cep, ponto, area)
    if base["logradouro"] and base["cidade"]:
        ponto = _rua_no_osm(base, area)
        if ponto and _no_lugar(ponto, area):
            return ponto, "nominatim"
    return None, ""


def _resolver(cep):
    """Endereço + coordenadas do CEP, ou None se o CEP não existe. Levanta ServicoIndisponivel em falha de rede.

    Se o endereço veio mas as coordenadas não puderam ser conferidas agora (OpenStreetMap fora do ar), devolve o endereço
    com "provisorio": True; quem chama não grava isso, para tentar de novo depois.
    """
    base, falhas = None, 0
    for fonte in (_brasilapi, _viacep):  # se uma fonte estiver fora do ar, tenta a outra
        try:
            base = fonte(cep)
        except ServicoIndisponivel:
            falhas += 1
            continue
        if base:
            break
    if base is None and falhas:
        raise ServicoIndisponivel("nenhuma fonte de CEP respondeu")
    if base is None:
        return None

    Precisao = GeoCache.Precisao
    try:
        area = _area(base["bairro"], base["cidade"], base["uf"])
        ponto, fonte = _melhor_ponto(cep, base, area)
    except ServicoIndisponivel as exc:
        log.warning("CEP %s: não deu para localizar agora (%s)", cep, exc)
        return {**base, "latitude": None, "longitude": None, "precisao": Precisao.NENHUMA, "fonte": "indisponivel",
                "provisorio": True}

    if ponto:  # CEP sem rua (de cidade ou bairro inteiro) não tem precisão de rua, mesmo com um ponto dentro do bairro
        precisao = Precisao.ENDERECO if base["logradouro"] else Precisao.BAIRRO if base["bairro"] else Precisao.CIDADE
        return {**base, "latitude": ponto[0], "longitude": ponto[1], "precisao": precisao, "fonte": fonte}
    if area is not None and area.latitude is not None:  # a rua não foi achada: centro do bairro ou da cidade
        precisao = Precisao.BAIRRO if area.tipo == GeoArea.Tipo.BAIRRO else Precisao.CIDADE
        return {**base, "latitude": area.latitude, "longitude": area.longitude, "precisao": precisao, "fonte": "nominatim"}
    return {**base, "latitude": None, "longitude": None, "precisao": Precisao.NENHUMA, "fonte": "viacep"}


def consultar_cep(cep, forcar=False, somente_cache=False):
    """GeoCache do CEP (consultando as APIs só se necessário).

    Retorna None quando o CEP é inválido ou os serviços estão fora do ar e não há cache.
    Para um CEP inexistente retorna um GeoCache com `encontrado == False`.
    Se só o endereço foi obtido (coordenadas indisponíveis agora) retorna um GeoCache `provisorio`, que não é gravado.
    Com somente_cache=True nunca acessa a rede (usado ao salvar cadastros, para não atrasar a tela).
    """
    cep = normalizar_cep(cep)
    if not validar_cep(cep):
        return None
    cache = GeoCache.objects.filter(pk=cep).first()
    if somente_cache:
        return cache
    if cache and not forcar:
        if cache.precisao != GeoCache.Precisao.NENHUMA:
            return cache
        if timezone.now() - cache.atualizado_em < REPETIR_CEP_SEM_COORDENADAS_APOS:
            return cache
    try:
        dados = _resolver(cep)
    except ServicoIndisponivel as exc:
        log.warning("Serviço de CEP indisponível (%s): %s", cep, exc)
        return cache
    if dados is None:
        dados = {"logradouro": "", "bairro": "", "cidade": "", "uf": "", "latitude": None,
                 "longitude": None, "precisao": GeoCache.Precisao.NENHUMA, "fonte": "inexistente"}
    if dados.pop("provisorio", False):
        cache = GeoCache(cep=cep, **dados)
        cache.provisorio = True
        return cache
    cache, _ = GeoCache.objects.update_or_create(pk=cep, defaults=dados)
    return cache


def aplicar_localizacao(candidato, forcar=False, somente_cache=False):
    """Preenche endereço vazio e coordenadas do candidato a partir do CEP. Retorna True se localizou."""
    status = candidato.GeoStatus
    if not validar_cep(candidato.cep):
        candidato.geo_status = status.SEM_CEP
        candidato.save(update_fields=["geo_status"])
        return False
    cache = consultar_cep(candidato.cep, forcar=forcar, somente_cache=somente_cache)
    if cache is None or cache.provisorio:  # serviços fora do ar (ou CEP ainda não consultado): continua pendente
        return False
    if somente_cache and cache.precisao == cache.Precisao.NENHUMA and cache.encontrado:
        return False  # já tentamos e não há coordenadas; deixa o worker decidir quando repetir
    campos = ["geo_status"]
    if cache.encontrado:
        # O ponto no mapa vem do CEP. Se a cidade digitada não é a do CEP, o bairro/cidade/UF passam a ser os do CEP;
        # senão o candidato ficaria agrupado num bairro e desenhado em outro lugar.
        conflito = bool(cache.cidade and candidato.cidade and _comparavel(candidato.cidade) != _comparavel(cache.cidade))
        for campo in ("logradouro", "bairro", "cidade", "uf"):
            novo = getattr(cache, campo)
            if novo and (not getattr(candidato, campo) or (conflito and campo != "logradouro")):
                setattr(candidato, campo, novo)
                campos.append(campo)
    if cache.latitude is not None and cache.longitude is not None:
        candidato.latitude, candidato.longitude = cache.latitude, cache.longitude
        candidato.precisao_geo = cache.precisao
        candidato.geo_status = status.OK
        campos += ["latitude", "longitude", "precisao_geo"]
    else:
        candidato.geo_status = status.FALHOU
    candidato.save(update_fields=campos)
    return candidato.geo_status == status.OK


def localizar_unidade(config=None, forcar=False):
    """Põe na unidade (Configuracao) o ponto do CEP dela. Retorna True se a unidade ficou com o ponto do CEP.

    O ponto da unidade é uma cópia do que o GeoCache sabe sobre o CEP. O worker chama isto de tempos em tempos para
    mantê-la igual: se o cache for refeito (atualização do sistema, "Relocalizar"), a cópia se acerta sozinha. Sem isso a
    unidade ficava para sempre no ponto antigo (o Centro do Rio) e, com ela, os anéis de 5/10/20 km, o filtro por distância
    e a distância de cada candidato.
    """
    config = config or Configuracao.atual()
    if not validar_cep(config.cep_unidade):
        return False
    cache = consultar_cep(config.cep_unidade, forcar=forcar)
    if cache is None or cache.provisorio or cache.latitude is None or cache.longitude is None:
        return False
    if (config.latitude, config.longitude) != (cache.latitude, cache.longitude):
        log.info("Unidade: ponto do CEP %s mudou de (%s, %s) para (%s, %s)", config.cep_unidade,
                 config.latitude, config.longitude, cache.latitude, cache.longitude)
        config.latitude, config.longitude = cache.latitude, cache.longitude
        config.endereco_unidade = ", ".join(p for p in (cache.logradouro, cache.bairro, cache.cidade) if p)[:200]
        config.save(update_fields=["latitude", "longitude", "endereco_unidade"])
    return True
