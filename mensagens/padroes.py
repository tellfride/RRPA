"""Modelos de mensagem criados na primeira execução. A equipe pode editar, desativar ou criar outros."""
from .models import ModeloMensagem

C = ModeloMensagem.Categoria

MODELOS_PADRAO = [
    (C.CONVITE_CADASTRO, "Convite para completar o cadastro",
     "Olá, {{primeiro_nome}}! Aqui é {{recrutador}} ({{unidade}}). Você foi aprovado(a) na triagem para a vaga de {{vaga}}. "
     "Para seguir, complete seu cadastro neste link: {{link_cadastro}}"),
    (C.APROVACAO, "Aprovação na triagem",
     "Olá, {{primeiro_nome}}! Boa notícia: seu cadastro para a vaga de {{vaga}} foi aprovado. "
     "Assim que houver vaga disponível, entraremos em contato. — {{recrutador}} ({{unidade}})"),
    (C.CONVOCACAO, "Convocação para a vaga",
     "Olá, {{primeiro_nome}}! Você foi chamado(a) para a vaga de {{vaga}} ({{unidade}}). Início: {{data_chamada}}. "
     "A permanência é de até {{prazo_meses}}, até {{data_fim}}. Responda esta mensagem para confirmar. — {{recrutador}}"),
    (C.LEMBRETE, "Lembrete: o prazo na vaga está acabando",
     "Olá, {{primeiro_nome}}. Seu período na vaga de {{vaga}} termina em {{data_fim}} ({{prazo_restante}}). "
     "Qualquer dúvida, fale com {{recrutador}}."),
    (C.RETORNO, "Retorno: banco de talentos",
     "Olá, {{primeiro_nome}}. Obrigado pelo interesse em trabalhar conosco ({{unidade}}). Neste momento não seguiremos com seu "
     "cadastro para {{vaga}}, mas manteremos seus dados no banco de talentos para próximas oportunidades. Abraço, {{recrutador}}."),
    (C.GERAL, "Atualização de dados",
     "Olá, {{primeiro_nome}}! Precisamos atualizar alguns dados do seu cadastro ({{unidade}}). Use este link: {{link_cadastro}}"),
]


def criar_modelos_padrao():
    if ModeloMensagem.objects.exists():
        return 0
    ModeloMensagem.objects.bulk_create([ModeloMensagem(categoria=c, nome=n, texto=t) for c, n, t in MODELOS_PADRAO])
    return len(MODELOS_PADRAO)
