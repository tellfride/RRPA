from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import CreateView, UpdateView

from candidatos.models import Candidato, LinkCadastro
from core.models import Configuracao, LogAtividade
from core.permissoes import EQUIPE_RECRUTAMENTO, PerfilRequeridoMixin, perfil_requerido
from core.utils import formatar_telefone, normalizar_telefone, telefone_whatsapp, validar_telefone
from vinculos.models import Vinculo
from whatsapp import registro
from whatsapp.ativacao import api_ligada
from whatsapp.models import ConexaoWhatsApp

from . import disparos
from . import render as mensagem
from .forms import ModeloMensagemForm
from .models import EnvioMensagem, ModeloMensagem


def _int(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ modelos
@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def modelos(request):
    config = Configuracao.atual()
    contexto = mensagem.montar_contexto(request.user, config, nome="Maria Souza Lima")
    grupos = {}
    for modelo in ModeloMensagem.objects.all():
        texto, _ = mensagem.renderizar(modelo.texto, {**mensagem.EXEMPLOS, **contexto, "nome": "Maria Souza Lima", "primeiro_nome": "Maria"})
        modelo.exemplo = texto
        grupos.setdefault(modelo.get_categoria_display(), []).append(modelo)
    return render(request, "mensagens/modelos.html", {"grupos": grupos, "total": sum(len(g) for g in grupos.values())})


class _FormModeloMixin(PerfilRequeridoMixin):
    perfis_permitidos = EQUIPE_RECRUTAMENTO
    model = ModeloMensagem
    form_class = ModeloMensagemForm
    template_name = "mensagens/modelo_form.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["campos"] = mensagem.CAMPOS
        ctx["exemplos"] = mensagem.EXEMPLOS
        return ctx

    def get_success_url(self):
        return reverse("mensagens:modelos")


class ModeloCriarView(_FormModeloMixin, CreateView):
    def form_valid(self, form):
        form.instance.criado_por = self.request.user
        resposta = super().form_valid(form)
        LogAtividade.registrar(self.request.user, "mensagem.modelo", f"Criou o modelo de mensagem “{self.object.nome}”", self.object, self.request)
        messages.success(self.request, "Modelo criado.")
        return resposta

    def get_initial(self):
        return {"ativo": True}


class ModeloEditarView(_FormModeloMixin, UpdateView):
    def form_valid(self, form):
        resposta = super().form_valid(form)
        LogAtividade.registrar(self.request.user, "mensagem.modelo", f"Editou o modelo de mensagem “{self.object.nome}”", self.object, self.request)
        messages.success(self.request, "Modelo salvo.")
        return resposta


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def modelo_alternar(request, pk):
    modelo = get_object_or_404(ModeloMensagem, pk=pk)
    modelo.ativo = not modelo.ativo
    modelo.save(update_fields=["ativo", "atualizado_em"])
    messages.success(request, f"Modelo “{modelo.nome}” {'ativado' if modelo.ativo else 'desativado'}.")
    return redirect("mensagens:modelos")


# ------------------------------------------------------------------ enviar
def _alvo(request):
    """Descobre para quem é a mensagem: um candidato ou um convite ainda sem cadastro."""
    candidato = Candidato.objects.filter(pk=_int(request.GET.get("candidato"))).first() if request.GET.get("candidato") else None
    link = LinkCadastro.objects.select_related("vaga", "criado_por").filter(pk=_int(request.GET.get("link"))).first() if request.GET.get("link") else None
    vinculo = Vinculo.objects.select_related("vaga", "candidato").filter(pk=_int(request.GET.get("vinculo"))).first() if request.GET.get("vinculo") else None
    if candidato is None and link is not None:
        candidato = link.candidato_origem
    if candidato is None and vinculo is not None:
        candidato = vinculo.candidato
    if vinculo is None and candidato is not None:
        vinculo = candidato.vinculos.filter(status="ativo").select_related("vaga").first()
    return candidato, link, vinculo


def _modelo_inicial(request, modelos):
    escolhido = modelos.filter(pk=_int(request.GET.get("modelo"))).first() if request.GET.get("modelo") else None
    if escolhido is None and request.GET.get("categoria"):
        escolhido = modelos.filter(categoria=request.GET["categoria"]).first()
    return escolhido or modelos.first()


@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def compor(request):
    config = Configuracao.atual()
    candidato, link, vinculo = _alvo(request)
    if candidato is None and link is None:
        messages.warning(request, "Escolha para quem enviar a mensagem.")
        return redirect("candidatos:lista")
    nome = candidato.nome if candidato else link.nome_sugerido
    telefone = candidato.telefone if candidato else link.telefone_sugerido
    modelos = ModeloMensagem.objects.filter(ativo=True)
    modelo = _modelo_inicial(request, modelos)
    contexto = mensagem.montar_contexto(request.user, config, request, candidato=candidato, vinculo=vinculo, link=link)
    texto, faltando = mensagem.renderizar(modelo.texto if modelo else "", contexto)
    return render(request, "mensagens/compor.html", {
        "candidato": candidato, "link": link, "vinculo": vinculo, "nome": nome, "telefone": telefone,
        "recusou_whatsapp": candidato is not None and candidato.whatsapp_ok is False,
        "telefone_ok": validar_telefone(normalizar_telefone(telefone)), "whatsapp": telefone_whatsapp(telefone),
        "telefone_formatado": formatar_telefone(telefone), "modelos": modelos, "modelo": modelo, "texto": texto,
        "faltando": faltando, "campos": dict((c[0], c[1]) for c in mensagem.CAMPOS),
    })


@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def fila(request):
    """Envio para vários candidatos: prévia por pessoa, com o disparo automático (se a conexão estiver pronta)
    e, para cada pessoa, o botão que abre o WhatsApp com a mensagem pronta."""
    ids = [i for i in (_int(x) for x in request.GET.get("ids", "").split(",")) if i]
    candidatos = list(Candidato.objects.filter(pk__in=ids).order_by("nome"))
    if not candidatos:
        messages.warning(request, "Marque ao menos um candidato para enviar mensagem.")
        return redirect("candidatos:lista")
    modelos = ModeloMensagem.objects.filter(ativo=True)
    modelo = _modelo_inicial(request, modelos)
    api = api_ligada()
    conexao = ConexaoWhatsApp.atual() if api else None
    exige_template = bool(api and registro.definicao(conexao.provedor).exige_template)
    previa = disparos.preparar(candidatos, modelo, request.user, request, conexao, exige_template)
    linhas, avisos = [], set()
    for d in previa:
        c = d.candidato
        avisos.update(d.faltando)
        linhas.append({"c": c, "texto": d.texto, "ok": validar_telefone(c.telefone) and c.whatsapp_ok is not False,
                       "recusou": c.whatsapp_ok is False, "whatsapp": c.whatsapp, "motivo": d.motivo,
                       "parametros": list(enumerate(d.parametros, 1))})
    elegiveis = sum(1 for d in previa if not d.motivo)
    return render(request, "mensagens/fila.html", {
        "linhas": linhas, "modelos": modelos, "modelo": modelo, "ids": ",".join(str(i) for i in ids),
        "avisos": sorted(avisos), "campos": dict((c[0], c[1]) for c in mensagem.CAMPOS),
        "conexao": conexao, "exige_template": exige_template, "elegiveis": elegiveis, "nao_enviadas": len(previa) - elegiveis,
        "sem_template": bool(exige_template and modelo is not None and not modelo.template_oficial_nome),
        "ritmo_padrao": min(disparos.RITMO_PADRAO, conexao.limite_por_minuto) if api else disparos.RITMO_PADRAO,
    })


@require_POST
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def registrar(request):
    """Chamado em segundo plano quando alguém abre o WhatsApp: guarda o que foi enviado."""
    candidato = Candidato.objects.filter(pk=_int(request.POST.get("candidato"))).first()
    link = LinkCadastro.objects.filter(pk=_int(request.POST.get("link"))).first()
    modelo = ModeloMensagem.objects.filter(pk=_int(request.POST.get("modelo"))).first()
    texto = request.POST.get("texto", "").strip()[:2000]
    if not texto or (candidato is None and link is None):
        return HttpResponse(status=400)
    nome = candidato.nome if candidato else link.nome_sugerido
    envio = EnvioMensagem.objects.create(
        candidato=candidato, link=link, modelo=modelo, usuario=request.user, nome_destino=nome,
        telefone=candidato.telefone if candidato else link.telefone_sugerido, texto=texto,
    )
    if link is not None and link.enviado_em is None:
        link.enviado_em = envio.criado_em
        link.save(update_fields=["enviado_em"])
    LogAtividade.registrar(request.user, "mensagem.aberta", f"Abriu o WhatsApp para {nome}" + (f" com o modelo “{modelo.nome}”" if modelo else ""), envio, request)
    return HttpResponse(status=204)


@require_GET
@perfil_requerido(*EQUIPE_RECRUTAMENTO)
def historico(request):
    qs = EnvioMensagem.objects.select_related("usuario", "modelo", "candidato")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(nome_destino__icontains=q) | Q(texto__icontains=q))
    pagina = Paginator(qs, 30).get_page(request.GET.get("page"))
    return render(request, "mensagens/historico.html", {"page_obj": pagina, "envios": pagina.object_list, "q": q})
