"""Agregação dos dados do mapa (lógica pura, sem banco).

Recebe as linhas mínimas dos candidatos JÁ filtradas pelo escopo de quem olha e devolve totais, bairros (com o
centro de cada um), pontos e limites. Privacidade: nomes, CPFs e telefones NUNCA entram aqui; os pontos levam
apenas id e coordenadas aproximadas (nível de CEP/rua).

Cada linha é um dict: id, latitude, longitude, precisao, bairro, cidade, uf, status, whatsapp_ok, indicado_por_id.
"""
import math
import re
from collections import Counter

from .escala import construir_escala, cor_para, raio_para

LIMITE_PONTOS = 20_000
TOP_INDICADORES = 3
BAIRRO_DESCONHECIDO = "Bairro não informado"
_ESPACOS = re.compile(r"\s+")


def coordenadas_validas(lat, lng):
    if lat is None or lng is None or isinstance(lat, bool) or isinstance(lng, bool):
        return False
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return False
    return -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


def limpar(texto):
    return _ESPACOS.sub(" ", str(texto or "")).strip()


def chave_do_bairro(bairro, cidade):
    return f"{bairro}\x1f{cidade}"


def amostrar(itens, limite):
    """Amostra uniforme e determinística (índices igualmente espaçados), preservando a ordem."""
    if limite <= 0:
        return []
    if len(itens) <= limite:
        return list(itens)
    passo = len(itens) / limite
    return [itens[int(i * passo)] for i in range(limite)]


def agregar(linhas, nomes_indicadores, pode_ver_candidatos, limite_pontos=LIMITE_PONTOS):
    grupos, localizados = {}, []
    total = aproximados = 0
    min_lat = min_lng = math.inf
    max_lat = max_lng = -math.inf

    for linha in linhas:
        total += 1
        bairro, cidade = limpar(linha.get("bairro")), limpar(linha.get("cidade"))
        grupo = grupos.setdefault(chave_do_bairro(bairro, cidade), {
            "nome": bairro, "cidade": cidade, "uf": limpar(linha.get("uf")), "total": 0, "mapeados": 0,
            "soma_lat": 0.0, "soma_lng": 0.0, "aptos": 0, "em_vaga": 0, "aprovados": 0, "indicadores": Counter(),
        })
        grupo["total"] += 1
        grupo["aptos"] += linha.get("whatsapp_ok") is not False
        grupo["em_vaga"] += linha.get("status") == "em_vaga"
        grupo["aprovados"] += linha.get("status") == "aprovado"
        grupo["indicadores"][linha.get("indicado_por_id")] += 1

        lat, lng = linha.get("latitude"), linha.get("longitude")
        if coordenadas_validas(lat, lng):
            lat, lng = float(lat), float(lng)
            grupo["mapeados"] += 1
            grupo["soma_lat"] += lat
            grupo["soma_lng"] += lng
            aproximados += linha.get("precisao") not in ("ENDERECO", "")
            min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
            min_lng, max_lng = min(min_lng, lng), max(max_lng, lng)
            localizados.append((linha.get("id"), lat, lng, linha.get("status")))

    bairros, sem_geo = [], []
    for chave, g in grupos.items():
        base = {"key": chave, "name": g["nome"] or BAIRRO_DESCONHECIDO, "known": bool(g["nome"]), "city": g["cidade"],
                "uf": g["uf"], "count": g["total"]}
        if g["mapeados"] == 0:
            sem_geo.append(base)
            continue
        indicadores = [
            {"name": nomes_indicadores.get(pk) or "Cadastro direto", "count": n}
            for pk, n in sorted(g["indicadores"].items(), key=lambda par: (-par[1], str(par[0])))
            if pk is not None
        ][:TOP_INDICADORES]
        bairros.append({
            **base, "mapped": g["mapeados"], "lat": round(g["soma_lat"] / g["mapeados"], 6),
            "lng": round(g["soma_lng"] / g["mapeados"], 6), "whatsapp": g["aptos"], "em_vaga": g["em_vaga"],
            "aprovados": g["aprovados"], "top_indicadores": indicadores,
        })
    ordem = lambda b: (-b["count"], b["name"].lower(), b["city"])  # noqa: E731
    bairros.sort(key=ordem)
    sem_geo.sort(key=ordem)

    maximo = max((b["count"] for b in bairros), default=0)
    faixas = construir_escala(maximo)
    for b in bairros:
        b["color"], b["radius"] = cor_para(b["count"], faixas), raio_para(b["count"], maximo)

    amostra = amostrar(localizados, limite_pontos)
    return {
        "totals": {"candidatos": total, "with_geo": len(localizados), "without_geo": total - len(localizados),
                   "approximate": aproximados},
        "neighborhoods": bairros,
        "neighborhoods_without_geo": sem_geo,
        # O id só vai para quem pode abrir o cadastro; os demais veem apenas posição e situação.
        "points": [{"id": pk if pode_ver_candidatos else None, "lat": round(lat, 5), "lng": round(lng, 5), "status": st}
                   for pk, lat, lng, st in amostra],
        "truncated": len(localizados) > limite_pontos,
        "bounds": [[round(min_lat, 6), round(min_lng, 6)], [round(max_lat, 6), round(max_lng, 6)]] if localizados else None,
        "legend": {"max_count": maximo, "bins": faixas},
        "can_view_candidates": bool(pode_ver_candidatos),
    }
