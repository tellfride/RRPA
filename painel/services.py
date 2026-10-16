"""Consultas do painel: números, quadro de vagas, vencimentos, funil de candidatos, radar e listas da gaveta."""
import math
from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from candidatos.models import Candidato
from core.constantes import VINCULO_ATIVO
from core.geo import haversine_km
from core.models import LogAtividade
from core.utils import abreviar_nome, rotulo_mes, somar_meses, texto_prazo
from vagas.models import Vaga
from vinculos import apresentacao
from vinculos.models import Vinculo
from vinculos.services import pendencias_checkin

LIMITE_LISTA = 100
FAIXAS_KM = [(0, 5, "até 5 km"), (5, 10, "5 a 10 km"), (10, 20, "10 a 20 km"), (20, None, "mais de 20 km")]
ETAPAS_FUNIL = [
    (Candidato.Status.PENDENTE, "Aguardando aprovação", "#86b6ef"),
    (Candidato.Status.APROVADO, "Aprovados", "#3987e5"),
    (Candidato.Status.EM_VAGA, "Em vaga", "#256abf"),
    (Candidato.Status.CONCLUIDO, "Já atuaram", "#184f95"),
]


# ------------------------------------------------------------------ escopo por perfil
def vagas_do_usuario(usuario):
    qs = Vaga.objects.all()
    if usuario is not None and usuario.eh_gestor:
        qs = qs.filter(responsaveis=usuario)
    return qs


def vinculos_do_usuario(usuario, vaga_id=None):
    qs = Vinculo.objects.select_related("candidato", "vaga")
    if usuario is not None and usuario.eh_gestor:
        qs = qs.filter(vaga__responsaveis=usuario)
    if vaga_id:
        qs = qs.filter(vaga_id=vaga_id)
    return qs


def nome_visivel(usuario, nome):
    """Visualizadores só veem 'Maria L.'."""
    if usuario is None or usuario.eh_visualizador:
        return abreviar_nome(nome)
    return nome


def _url_gaveta(**params):
    from urllib.parse import urlencode

    return f"{reverse('painel:detalhe')}?{urlencode(params)}"


# ------------------------------------------------------------------ quadro de vagas
def montar_quadro(usuario, config, hoje, vaga_id=None, anonimo=False, incluir_inativas=False):
    """Uma linha por vaga, com um 'assento' por vaga: ocupada, vencendo, livre (e vencidas ao final)."""
    vagas = vagas_do_usuario(usuario)
    if not incluir_inativas:
        vagas = vagas.filter(ativa=True)
    vagas = list(vagas.order_by("-ativa", "nome"))
    if vaga_id:
        vagas = [v for v in vagas if v.pk == vaga_id]
    ativos = (
        Vinculo.objects.filter(status=VINCULO_ATIVO, vaga__in=vagas)
        .select_related("candidato").order_by("-data_fim", "candidato__nome")
    )
    por_vaga = defaultdict(list)
    for vinculo in ativos:
        por_vaga[vinculo.vaga_id].append(vinculo)
    limite = hoje + timedelta(days=config.dias_alerta)

    linhas = []
    for vaga in vagas:
        todos = por_vaga.get(vaga.pk, [])
        dentro = [v for v in todos if v.data_fim >= hoje]
        vencidos = [v for v in todos if v.data_fim < hoje]
        vencendo = [v for v in dentro if v.data_fim <= limite]
        normais = [v for v in dentro if v.data_fim > limite]
        livres = max(vaga.quantidade - len(dentro), 0)

        def assento(tipo, vinculo=None, **extra):
            if vinculo is not None:
                prazo = f"até {vinculo.data_fim:%d/%m/%Y} ({texto_prazo((vinculo.data_fim - hoje).days)})"
                titulo = prazo if anonimo else f"{nome_visivel(usuario, vinculo.candidato.nome)} — {prazo}"
                if tipo == "vencido":
                    titulo = f"Prazo venceu em {vinculo.data_fim:%d/%m/%Y}" if anonimo else f"{nome_visivel(usuario, vinculo.candidato.nome)} — prazo venceu em {vinculo.data_fim:%d/%m/%Y}"
            else:
                titulo = extra.get("titulo", "")
            return {"tipo": tipo, "titulo": titulo, "url": extra.get("url", "")}

        assentos = []
        for v in normais:
            assentos.append(assento("ocupada", v, url=_url_gaveta(tipo="ocupantes", vaga=vaga.pk, destaque=v.pk)))
        for v in vencendo:
            assentos.append(assento("vencendo", v, url=_url_gaveta(tipo="ocupantes", vaga=vaga.pk, destaque=v.pk)))
        for _ in range(livres):
            assentos.append(assento("livre", titulo="Vaga disponível", url=_url_gaveta(tipo="disponiveis", vaga=vaga.pk)))
        if vencidos:
            assentos.append({"tipo": "separador", "titulo": "", "url": ""})
            for v in vencidos:
                assentos.append(assento("vencido", v, url=_url_gaveta(tipo="vencidos", vaga=vaga.pk)))

        linhas.append({
            "vaga": vaga,
            "n_ocupadas": len(dentro),
            "n_vencendo": len(vencendo),
            "n_vencidos": len(vencidos),
            "livres": livres,
            "pct": min(100, round(100 * len(dentro) / vaga.quantidade)) if vaga.quantidade else 0,
            "assentos": assentos,
            "compacto": vaga.quantidade + len(vencidos) > 30,
            "url_ocupantes": _url_gaveta(tipo="ocupantes", vaga=vaga.pk),
            "url_livres": _url_gaveta(tipo="disponiveis", vaga=vaga.pk),
            "url_vencendo": _url_gaveta(tipo="vencendo", vaga=vaga.pk),
        })
    return linhas, [v for v in ativos if v.data_fim >= hoje], [v for v in ativos if v.data_fim < hoje]


def totais_do_quadro(linhas):
    quantidade = sum(l["vaga"].quantidade for l in linhas)
    ocupadas = sum(l["n_ocupadas"] for l in linhas)
    return {
        "vagas": len(linhas),
        "quantidade": quantidade,
        "ocupadas": ocupadas,
        "livres": sum(l["livres"] for l in linhas),
        "vencendo": sum(l["n_vencendo"] for l in linhas),
        "vencidos": sum(l["n_vencidos"] for l in linhas),
        "pct_ocupacao": min(100, round(100 * ocupadas / quantidade)) if quantidade else 0,
    }


# ------------------------------------------------------------------ vencimentos
def vencimentos_por_mes(dentro, vencidos, config, hoje, anonimo=False):
    """Colunas: vencidos (se houver) + um mês por coluna, do mês atual até o fim do prazo configurado."""
    contagem = Counter((v.data_fim.year, v.data_fim.month) for v in dentro)
    colunas = []
    if vencidos:
        colunas.append({
            "rotulo": "Vencidos", "total": len(vencidos), "tom": "vencido",
            "url": "" if anonimo else _url_gaveta(tipo="vencidos"),
            "descricao": f"{len(vencidos)} com prazo vencido, aguardando baixa",
        })
    primeiro = hoje.replace(day=1)
    for i in range(config.prazo_meses + 1):
        mes = somar_meses(primeiro, i)
        total = contagem.get((mes.year, mes.month), 0)
        colunas.append({
            "rotulo": rotulo_mes(mes), "total": total,
            "tom": "zero" if total == 0 else ("aviso" if i == 0 else ""),
            "url": "" if anonimo or total == 0 else _url_gaveta(tipo="mes", mes=f"{mes:%Y-%m}"),
            "descricao": f"{total} {'colaborador' if total == 1 else 'colaboradores'} com prazo até {rotulo_mes(mes, longo=True)}",
        })
    maximo = max([c["total"] for c in colunas] + [1])
    for c in colunas:  # a barra ocupa no máximo 78% da coluna; o resto é do número em cima dela
        c["altura"] = round(78 * c["total"] / maximo) if c["total"] else 0
    return colunas


# ------------------------------------------------------------------ candidatos
def funil_candidatos():
    contagem = dict(Candidato.objects.values_list("status").annotate(n=Count("id")))
    etapas = [{"status": s.value, "rotulo": r, "cor": cor, "total": contagem.get(s, 0)} for s, r, cor in ETAPAS_FUNIL]
    maximo = max([e["total"] for e in etapas] + [1])
    for e in etapas:
        e["largura"] = round(100 * e["total"] / maximo)
        e["url"] = _url_gaveta(tipo="status", status=e["status"])
    fora = contagem.get(Candidato.Status.REPROVADO, 0) + contagem.get(Candidato.Status.DESISTENTE, 0)
    return etapas, fora


def ranking_indicadores(limite=6):
    """Quem mais forneceu links de cadastro (e quantos desses cadastros avançaram)."""
    avancou = [Candidato.Status.APROVADO, Candidato.Status.EM_VAGA, Candidato.Status.CONCLUIDO]
    linhas = (
        Candidato.objects.exclude(indicado_por=None)
        .values("indicado_por", "indicado_por__first_name", "indicado_por__last_name", "indicado_por__username")
        .annotate(total=Count("id"), avancaram=Count("id", filter=Q(status__in=avancou)))
        .order_by("-total")[:limite]
    )
    resultado = []
    maximo = max([l["total"] for l in linhas] + [1])
    for l in linhas:
        nome = f'{l["indicado_por__first_name"]} {l["indicado_por__last_name"]}'.strip() or l["indicado_por__username"]
        resultado.append({
            "id": l["indicado_por"], "nome": nome, "total": l["total"], "avancaram": l["avancaram"],
            "largura": round(100 * l["total"] / maximo),
        })
    return resultado


def anotar_distancia(candidatos, config):
    """Coloca .distancia_km em cada candidato localizado (ou None)."""
    for c in candidatos:
        c.distancia_km = None
        if config.tem_coordenadas and c.localizado:
            c.distancia_km = round(haversine_km(config.latitude, config.longitude, c.latitude, c.longitude), 1)
    return candidatos


def _pool_localizado():
    return list(
        Candidato.objects.filter(
            status__in=[Candidato.Status.PENDENTE, Candidato.Status.APROVADO, Candidato.Status.EM_VAGA],
            latitude__isnull=False, longitude__isnull=False,
        ).only("id", "nome", "bairro", "cidade", "uf", "latitude", "longitude", "status")
    )


def radar_de_moradia(config):
    """Candidatos ao redor da unidade, em anéis de 5, 10 e 20 km (sem depender de mapa externo)."""
    if not config.tem_coordenadas:
        return None
    pool = _pool_localizado()
    k_lat, k_lng = 110.57, 111.32 * math.cos(math.radians(config.latitude))
    escala, raio_max = 3.0, 30.0  # 3 unidades do desenho por km; desenho cobre 30 km
    pontos, contagem = [], Counter()
    for c in pool:
        km = haversine_km(config.latitude, config.longitude, c.latitude, c.longitude)
        for i, (de, ate, _) in enumerate(FAIXAS_KM):
            if km >= de and (ate is None or km < ate):
                contagem[i] += 1
                break
        if km <= raio_max:
            pontos.append({
                "x": round((c.longitude - config.longitude) * k_lng * escala, 1),
                "y": round(-(c.latitude - config.latitude) * k_lat * escala, 1),
            })
    faixas = []
    for i, (de, ate, rotulo) in enumerate(FAIXAS_KM):
        faixas.append({
            "rotulo": rotulo, "total": contagem.get(i, 0),
            "url": _url_gaveta(tipo="faixa", de=de, ate=ate if ate is not None else ""),
        })
    bairros = Counter((c.bairro, c.cidade) for c in pool if c.bairro)
    territorios = [
        {"bairro": b, "cidade": cid, "total": n, "url": f"{reverse('mapa:mapa')}?territorio={b}|{cid}"}
        for (b, cid), n in bairros.most_common(4)
    ]
    return {
        "pontos": pontos, "faixas": faixas, "territorios": territorios, "total": len(pool),
        "aneis": [{"km": km, "r": km * escala, "r_neg": -km * escala} for km in (5, 10, 20)],
        "sem_localizacao": Candidato.objects.filter(
            status__in=[Candidato.Status.PENDENTE, Candidato.Status.APROVADO],
        ).exclude(geo_status=Candidato.GeoStatus.OK).count(),
    }


def atividade_recente(limite=8):
    return list(LogAtividade.objects.select_related("usuario")[:limite])


# ------------------------------------------------------------------ o que fazer agora
def tarefas_do_dia(usuario, totais, pendencias, config, vaga_id=None):
    """Lista do que pede ação, da mais urgente para a menos, só com o que o perfil pode fazer."""
    filtro = f"&vaga={vaga_id}" if vaga_id else ""
    gaveta = reverse("painel:detalhe")
    pode_recrutar = usuario is not None and usuario.pode_recrutar
    pode_checkin = usuario is not None and usuario.pode_checkin
    tarefas = []

    def nova(tom, numero, titulo, texto, rotulo, url, gaveta_lateral=False):
        tarefas.append({"tom": tom, "numero": numero, "titulo": titulo, "texto": texto, "rotulo": rotulo, "url": url, "gaveta": gaveta_lateral})

    if totais["vencidos"]:
        n = totais["vencidos"]
        nova("bad", n, f"{'Prazo vencido' if n == 1 else 'Prazos vencidos'}", "Registre a saída para liberar a vaga.", "Ver quem são",
             f"{gaveta}?tipo=vencidos{filtro}", True)
    if pendencias["total"] and pode_checkin:
        n = pendencias["total"]
        nova("warn", n, "Check-in pendente" if n == 1 else "Check-ins pendentes",
             f"{pendencias['atrasadas']} de meses que já terminaram." if pendencias["atrasadas"] else "Confirme se o RPA foi cumprido no mês.",
             "Fazer check-ins", reverse("checkins:lista"))
    if totais["vencendo"]:
        n = totais["vencendo"]
        nova("warn", n, f"{'Vence' if n == 1 else 'Vencem'} em {config.dias_alerta} dias", "Prepare a troca ou o encerramento.", "Ver quem são",
             f"{gaveta}?tipo=vencendo{filtro}", True)
    if pode_recrutar:
        aprovados = Candidato.objects.filter(status=Candidato.Status.APROVADO).count()
        pendentes = Candidato.objects.filter(status=Candidato.Status.PENDENTE).count()
        if pendentes:
            nova("info", pendentes, "Candidato aguardando aprovação" if pendentes == 1 else "Candidatos aguardando aprovação",
                 "Aprove quem pode ser chamado.", "Ver candidatos", reverse("candidatos:lista") + "?status=pendente")
        if totais["livres"] and aprovados:
            nova("good", min(totais["livres"], aprovados), "Vagas livres com candidato aprovado esperando", "Chame alguém para ocupar a vaga.",
                 "Ver aprovados", reverse("candidatos:lista") + "?status=aprovado")
    return tarefas


# ------------------------------------------------------------------ painel completo
def painel_completo(usuario, config, hoje, vaga_id=None, anonimo=False):
    """Tudo que a tela inicial precisa. `anonimo=True` (painel compartilhado) devolve só números, sem nomes."""
    linhas, dentro, vencidos = montar_quadro(usuario, config, hoje, vaga_id, anonimo)
    totais = totais_do_quadro(linhas)
    pendencias = pendencias_checkin(usuario, hoje, vaga_id=vaga_id, todas=True)
    dados = {
        "quadro": linhas,
        "totais": totais,
        "pendencias": {"total": len(pendencias), "atrasadas": sum(1 for p in pendencias if p["atrasado"])},
        "meses": vencimentos_por_mes(dentro, vencidos, config, hoje, anonimo),
        "vagas_filtro": [(v.pk, v.nome) for v in vagas_do_usuario(usuario).filter(ativa=True).order_by("nome")],
        "tarefas": [], "proximos": [], "funil": None, "indicadores": None, "radar": None, "atividade": None,
    }
    if anonimo:
        return dados

    dados["tarefas"] = tarefas_do_dia(usuario, totais, dados["pendencias"], config, vaga_id)
    pendentes_ids = {p["vinculo"].pk for p in pendencias}
    urgentes = sorted(vencidos, key=lambda v: v.data_fim) + sorted(dentro, key=lambda v: v.data_fim)
    proximos = apresentacao.preparar(urgentes[:7], config, hoje, com_chips=False)
    for v in proximos:
        v.tem_pendencia = v.pk in pendentes_ids
    dados["proximos"] = proximos

    if usuario is None or usuario.perfil != "gestor":
        etapas, fora = funil_candidatos()
        dados["funil"] = {"etapas": etapas, "fora": fora, "links": usuario is not None and usuario.pode_recrutar}
        dados["radar"] = radar_de_moradia(config)
    if usuario is not None and usuario.pode_recrutar:
        dados["indicadores"] = ranking_indicadores()
        dados["atividade"] = atividade_recente()
    return dados


# ------------------------------------------------------------------ gaveta de detalhes
def _int(valor, padrao=None):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return padrao


def lista_detalhe(usuario, config, params, hoje=None):
    """Conteúdo da gaveta lateral. Devolve um dict para o template painel/_detalhe.html."""
    hoje = hoje or timezone.localdate()
    tipo = params.get("tipo", "")
    vaga_id = _int(params.get("vaga"))
    vaga = vagas_do_usuario(usuario).filter(pk=vaga_id).first() if vaga_id else None
    nome_vaga = f"{vaga.nome} · " if vaga else ""
    sufixo_vaga = f"&vaga={vaga.pk}" if vaga else ""
    base = {"modo": "vinculos", "destaque": _int(params.get("destaque")), "pagina": "", "itens": [], "sub": "", "vazio": "Nenhum registro."}

    def vinculos(qs, ordem="data_fim"):
        return apresentacao.preparar(qs.order_by(ordem, "candidato__nome").prefetch_related("checkins")[:LIMITE_LISTA], config, hoje)

    if tipo == "ocupantes":
        qs = vinculos_do_usuario(usuario, vaga_id).ocupando(hoje)
        itens = vinculos(qs)
        base.update(
            titulo=f"{nome_vaga}colaboradores em vaga",
            sub=f"{len(itens)} dentro do prazo de {config.prazo_texto}" if itens else "",
            pagina=f"{reverse('vinculos:lista')}?situacao=ativo{sufixo_vaga}", itens=itens,
            vazio="Ninguém está ocupando esta vaga agora.",
        )
    elif tipo == "vencendo":
        itens = vinculos(vinculos_do_usuario(usuario, vaga_id).vencendo(hoje, config.dias_alerta))
        base.update(
            titulo=f"{nome_vaga}vencem em até {config.dias_alerta} dias",
            sub="Prepare a troca de colaborador ou o encerramento." if itens else "",
            pagina=f"{reverse('vinculos:lista')}?situacao=vencendo{sufixo_vaga}", itens=itens,
            vazio=f"Nenhum prazo vence nos próximos {config.dias_alerta} dias.",
        )
    elif tipo == "vencidos":
        itens = vinculos(vinculos_do_usuario(usuario, vaga_id).vencidos(hoje))
        base.update(
            titulo=f"{nome_vaga}prazo vencido",
            sub="Passaram do limite. Registre a saída para liberar a vaga." if itens else "",
            pagina=f"{reverse('vinculos:lista')}?situacao=vencido{sufixo_vaga}", itens=itens,
            vazio="Nenhum colaborador com prazo vencido.",
        )
    elif tipo == "mes":
        try:
            ano, mes = (int(p) for p in params.get("mes", "").split("-"))
        except ValueError:
            ano = mes = 0
        qs = vinculos_do_usuario(usuario, vaga_id).ocupando(hoje).filter(data_fim__year=ano, data_fim__month=mes) if ano else Vinculo.objects.none()
        itens = vinculos(qs)
        base.update(
            titulo=f"{nome_vaga}prazo termina em {rotulo_mes(hoje.replace(year=ano, month=mes, day=1), longo=True) if ano else '—'}",
            pagina=f"{reverse('vinculos:lista')}?situacao=ativo&vence={ano:04d}-{mes:02d}{sufixo_vaga}" if ano else "",
            itens=itens, vazio="Ninguém tem prazo terminando neste mês.",
        )
    elif tipo == "checkins":
        pendencias = pendencias_checkin(usuario, hoje, vaga_id=vaga_id, todas=True)
        for p in pendencias:
            p["vinculo"].sit_rotulo = ""
        base.update(
            modo="checkins", titulo=f"{nome_vaga}check-ins pendentes",
            sub=f"{sum(1 for p in pendencias if p['atrasado'])} de meses que já terminaram" if pendencias else "",
            pagina=reverse("checkins:lista"), itens=pendencias[:LIMITE_LISTA], vazio="Todos os check-ins estão em dia.",
        )
    elif tipo == "disponiveis" and vaga:
        livres = vaga.disponiveis(hoje)
        candidatos = list(
            Candidato.objects.filter(status=Candidato.Status.APROVADO, vagas_interesse=vaga).distinct()[:LIMITE_LISTA]
        ) if usuario is not None and usuario.pode_recrutar else []
        anotar_distancia(candidatos, config)
        candidatos.sort(key=lambda c: (c.distancia_km is None, c.distancia_km or 0, c.nome))
        base.update(
            modo="candidatos", titulo=f"{vaga.nome} · vagas disponíveis",
            sub=f"{livres} {'vaga livre' if livres == 1 else 'vagas livres'} de {vaga.quantidade}. Aprovados que querem esta vaga, do mais perto para o mais longe.",
            pagina=f"{reverse('candidatos:lista')}?status=aprovado&vaga={vaga.pk}", itens=candidatos, vaga_chamar=vaga if livres else None,
            vazio="Nenhum candidato aprovado para esta vaga ainda. Aprove candidatos ou envie um link de cadastro.",
        )
    elif tipo == "disponiveis":
        linhas, _, _ = montar_quadro(usuario, config, hoje)
        com_livres = [l for l in linhas if l["livres"]]
        base.update(
            modo="vagas_livres", titulo="Vagas disponíveis", sub=f"{sum(l['livres'] for l in com_livres)} vagas livres em {len(com_livres)} cargos",
            pagina=reverse("vagas:lista"), itens=com_livres, vazio="Todas as vagas estão ocupadas.",
        )
    elif tipo in ("status", "faixa", "territorio") and usuario is not None and usuario.pode_recrutar:
        qs = Candidato.objects.all()
        if tipo == "status":
            status = params.get("status", "")
            rotulo = dict(Candidato.Status.choices).get(status, "Candidatos")
            qs = qs.filter(status=status)
            titulo, pagina = rotulo, f"{reverse('candidatos:lista')}?status={status}"
        elif tipo == "territorio":
            bairro, cidade = params.get("bairro", ""), params.get("cidade", "")
            qs = qs.filter(bairro=bairro, cidade=cidade, status__in=[Candidato.Status.PENDENTE, Candidato.Status.APROVADO, Candidato.Status.EM_VAGA])
            titulo = f"{bairro or 'Sem bairro'} — {cidade}"
            pagina = f"{reverse('candidatos:lista')}?bairro={bairro}&cidade={cidade}"
        else:
            qs = qs.filter(latitude__isnull=False, status__in=[Candidato.Status.PENDENTE, Candidato.Status.APROVADO, Candidato.Status.EM_VAGA])
            titulo, pagina = "Distância até a unidade", reverse("candidatos:lista")
        candidatos = anotar_distancia(list(qs[:2000]), config)
        if tipo == "faixa":
            de, ate = _int(params.get("de"), 0), _int(params.get("ate"))
            candidatos = [c for c in candidatos if c.distancia_km is not None and c.distancia_km >= de and (ate is None or c.distancia_km < ate)]
            rotulo_faixa = next((r for d, a, r in FAIXAS_KM if d == de and a == ate), "")
            titulo = f"Moram {rotulo_faixa} da unidade"
        candidatos.sort(key=lambda c: (c.distancia_km is None, c.distancia_km or 0, c.nome))
        base.update(modo="candidatos", titulo=titulo, sub=f"{len(candidatos)} candidatos", pagina=pagina,
                    itens=candidatos[:LIMITE_LISTA], vazio="Nenhum candidato nesta situação.")
    else:
        base.update(titulo="Detalhes", vazio="Não há nada para mostrar aqui.")
    base["total_itens"] = len(base["itens"])
    return base
