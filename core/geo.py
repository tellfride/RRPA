"""Consulta de CEP e localização no mapa.

Cadeia de consulta (cada resposta fica em GeoCache, então um CEP é consultado uma única vez):
  1. BrasilAPI v2  -> endereço + coordenadas do CEP
  2. ViaCEP        -> endereço (quando a BrasilAPI não conhece o CEP)
  3. Nominatim/OSM -> coordenadas da rua; se falhar, do bairro; se falhar, da cidade
"""
import logging
import math
import threading
import time
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .models import GeoCache
from .utils import normalizar_cep, sem_acentos, validar_cep

log = logging.getLogger(__name__)

TIMEOUT = 6
REPETIR_CEP_SEM_COORDENADAS_APOS = timedelta(days=7)

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


def _brasilapi(cep):
    dados = _get_json(f"https://brasilapi.com.br/api/cep/v2/{cep}")
    if not isinstance(dados, dict) or "cep" not in dados:
        return None
    coords = (dados.get("location") or {}).get("coordinates") or {}
    return {
        "logradouro": dados.get("street") or "",
        "bairro": dados.get("neighborhood") or "",
        "cidade": dados.get("city") or "",
        "uf": dados.get("state") or "",
        "latitude": _float(coords.get("latitude")),
        "longitude": _float(coords.get("longitude")),
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
        "latitude": None,
        "longitude": None,
    }


def _nominatim(**consulta):
    """Uma busca no Nominatim, respeitando o limite de 1 requisição por segundo da política de uso."""
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
    if not dados:
        return None
    lat, lng = _float(dados[0].get("lat")), _float(dados[0].get("lon"))
    return (lat, lng) if lat is not None and lng is not None else None


def _resolver(cep):
    """Endereço + coordenadas do CEP, ou None se o CEP não existe. Levanta ServicoIndisponivel em falha de rede."""
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
    if base["latitude"] is not None and base["longitude"] is not None:
        return {**base, "precisao": GeoCache.Precisao.ENDERECO, "fonte": "brasilapi"}

    tentativas = []
    if base["logradouro"] and base["cidade"]:
        tentativas.append((GeoCache.Precisao.ENDERECO, {"street": base["logradouro"], "city": base["cidade"], "state": base["uf"]}))
    if base["bairro"] and base["cidade"]:
        tentativas.append((GeoCache.Precisao.BAIRRO, {"q": f'{base["bairro"]}, {base["cidade"]}, {base["uf"]}, Brasil'}))
    if base["cidade"]:
        tentativas.append((GeoCache.Precisao.CIDADE, {"q": f'{base["cidade"]}, {base["uf"]}, Brasil'}))
    for precisao, consulta in tentativas:
        achado = _nominatim(**consulta)
        if achado:
            return {**base, "latitude": achado[0], "longitude": achado[1], "precisao": precisao, "fonte": "nominatim"}
    return {**base, "precisao": GeoCache.Precisao.NENHUMA, "fonte": "viacep"}


def consultar_cep(cep, forcar=False, somente_cache=False):
    """GeoCache do CEP (consultando as APIs só se necessário).

    Retorna None quando o CEP é inválido ou os serviços estão fora do ar e não há cache.
    Para um CEP inexistente retorna um GeoCache com `encontrado == False`.
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
    cache, _ = GeoCache.objects.update_or_create(pk=cep, defaults=dados)
    return cache


def _comparavel(texto):
    return sem_acentos(texto).casefold().strip()


def aplicar_localizacao(candidato, forcar=False, somente_cache=False):
    """Preenche endereço vazio e coordenadas do candidato a partir do CEP. Retorna True se localizou."""
    status = candidato.GeoStatus
    if not validar_cep(candidato.cep):
        candidato.geo_status = status.SEM_CEP
        candidato.save(update_fields=["geo_status"])
        return False
    cache = consultar_cep(candidato.cep, forcar=forcar, somente_cache=somente_cache)
    if cache is None:  # serviços fora do ar (ou CEP ainda não consultado): continua pendente
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
