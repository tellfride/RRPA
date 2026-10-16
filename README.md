# R-RPA

Sistema para controlar quem ocupa cada vaga de **colaborador em RPA**, até quando pode ficar (3 meses a partir do mês da chamada) e se o setor confirmou, mês a mês, que o RPA foi cumprido. Django + PostgreSQL, tudo em Docker.

- **Painel clicável**: quem ocupa cada vaga, quem está vencendo, quantas vagas estão disponíveis. Cada número abre a lista por trás dele.
- **Vagas e planilhas**: o administrador cadastra os cargos; cada cargo tem seu **modelo de planilha** (nome, CPF, telefone, endereço) para importar candidatos.
- **Vínculo de 3 meses**: o recrutador chama um candidato aprovado para a vaga e o sistema calcula o prazo.
- **Check-in do setor**: o responsável confirma, por mês, se o colaborador cumpriu o RPA.
- **Mapa territorial por CEP** (no formato do mapa do E-Find).
- **Mensagens de WhatsApp** com modelos: o sistema monta a mensagem de cada pessoa e abre o WhatsApp (celular ou Web) já escrita. Opcionalmente, e desligado por padrão, há o **disparo automático pela API oficial da Meta** (veja abaixo).

## Subir com Docker

Copie o arquivo de configuração. No PowerShell:

```powershell
Copy-Item .env.example .env
```

Suba o sistema:

```bash
docker compose up --build
```

Abra **http://localhost:8765**. Com `RRPA_SEED_DEMO=1` (padrão do `.env.example`) o sistema já vem com dados fictícios e a tela de login mostra um botão por perfil. A senha dos acessos de teste é `demo1234`.

Três serviços sobem juntos: `db` (PostgreSQL), `web` (o site) e `worker` (em segundo plano: localiza os candidatos no mapa pelo CEP e, só com `RRPA_WHATSAPP_API=1`, envia a fila do disparo automático).

> A porta padrão é **8765** de propósito: 8000 e 8080 costumam estar ocupadas por outros sistemas. Troque em `RRPA_PORTA`.

### Antes de usar com dados reais

No `.env`:

| Ajuste | Por quê |
|---|---|
| `RRPA_SEED_DEMO=0` | Desliga os dados fictícios e os acessos de teste. |
| `RRPA_ADMIN_USUARIO` e `RRPA_ADMIN_SENHA` | Primeiro administrador. Com a senha em branco, uma senha aleatória aparece no log do `web` (`docker compose logs web`). |
| `POSTGRES_PASSWORD` | Troque a senha do banco. |
| `RRPA_BASE_URL=https://rrpa.suaunidade.org.br` | Os links de cadastro mandados por WhatsApp usam este endereço. Com `localhost` o candidato não consegue abrir no celular. |
| `DJANGO_ALLOWED_HOSTS` | Inclua o domínio/IP que as pessoas usam. |
| `RRPA_HTTPS=1` e `DJANGO_CSRF_TRUSTED_ORIGINS` | Quando houver HTTPS (proxy como Caddy, Nginx ou Traefik na frente). |
| `RRPA_GEOCODER_EMAIL` | A política do OpenStreetMap pede um contato nas consultas de localização. |

Backup do banco:

```bash
docker compose exec db pg_dump -U rrpa rrpa > backup.sql
```

## Quem faz o quê

| Perfil | Pode |
|---|---|
| **Administrador(a)** | Tudo: vagas, usuários, configurações, atividades, painel compartilhável e exclusão de cadastros. |
| **Recrutador(a)** | Cadastrar/importar/aprovar candidatos, chamar para a vaga, encerrar vínculo, mensagens de WhatsApp, links, mapa. |
| **Responsável do setor** | Ver as suas vagas e fazer o **check-in mensal**. Não vê CPF. |
| **Visualizador(a)** | Só acompanha o painel e o mapa, com nomes abreviados (“Maria L.”) e sem dados pessoais. |

Há ainda o **painel compartilhável**: um link só de leitura (Configurações) para qualquer pessoa acompanhar as vagas, **sem nomes**.

## Como as regras funcionam

**Prazo de 3 meses.** Em *Configurações* escolha a contagem:

- *Meses de calendário* (padrão): o mês da chamada conta como o 1º. Chamado em 20/10 → outubro, novembro e dezembro → termina em **31/12**.
- *Data exata*: chamada + 3 meses. Chamado em 20/10 → termina em **19/01**.

A mudança vale para as próximas chamadas; vínculos já criados mantêm o prazo que tinham. O prazo e o aviso de vencimento (30 dias) também são configuráveis.

**Vaga disponível** = quantidade de vagas − colaboradores ativos **dentro do prazo**. Quem passou do prazo não ocupa mais a vaga e aparece em **Prazo vencido** até a equipe registrar a saída.

**Check-in.** Para cada mês do período o responsável do setor da vaga marca *cumpriu*, *cumpriu em parte* ou *não cumpriu* (com observação). Pendentes aparecem no painel e na página *Check-ins*, com confirmação em lote.

**Fluxo do candidato.** Cadastro (planilha, manual ou link) → aprovação → *Chamar para a vaga* (vínculo de 3 meses) → check-ins → encerramento. Só candidatos aprovados podem ser chamados, e ninguém ocupa duas vagas ao mesmo tempo.

**Links de cadastro.** O *link pessoal* de cada pessoa da equipe (com QR Code e cartaz para imprimir) cadastra candidatos “aguardando aprovação”. O *convite individual* é gerado **depois da aprovação**, vale poucos dias e só funciona uma vez. Nos dois casos o candidato fica ligado a quem forneceu o link, e o painel mostra quem mais trouxe candidatos.

**Mapa.** Modos *Bairros* (padrão), *Pontos* e *Calor*, ranking de bairros, bairros sem localização e fila de localização, como no E-Find. Extras: marcador da unidade com anéis de 5, 10 e 20 km. Coordenadas por CEP: o endereço vem da BrasilAPI (ou ViaCEP); o ponto, da AwesomeAPI ou da rua no OpenStreetMap (Nominatim), com cache. **Todo ponto é conferido contra os limites do bairro no OpenStreetMap** e só é aceito se cair dentro dele; se não achar a rua, o candidato fica no centro do bairro (precisão “Bairro”). As coordenadas que a BrasilAPI manda junto com o endereço não são usadas: para os CEPs do Rio de Janeiro ela devolve sempre o mesmo ponto, no Centro (Guadalupe aparecia a ~20 km do lugar certo). O ponto da unidade acompanha o do CEP dela e se acerta sozinho (o worker confere a cada minuto) quando as localizações são refeitas. O mapa nunca envia nome, CPF ou telefone ao navegador.

**WhatsApp (envio normal).** Modelos com campos `{{primeiro_nome}}`, `{{vaga}}`, `{{data_fim}}`, `{{link_cadastro}}`… (mesmo formato do E-Find). Em *Candidatos*, use o botão de WhatsApp de uma pessoa ou marque várias e escolha **Enviar mensagem**: a tela mostra a mensagem pronta de cada uma, com um botão que **abre o WhatsApp já escrito** (você confere e toca em enviar) e o envio fica registrado em *Mensagens enviadas*. Quem pediu para não receber mensagens (`whatsapp_ok = não`) fica bloqueado. Esse caminho não usa API, não precisa de conta da Meta e funciona com a instalação como vem.

### Disparo automático por API (opcional, desligado)

Existe, mas **não aparece** enquanto `RRPA_WHATSAPP_API=0` (o padrão): sem as telas de conexão e de disparos, sem webhook (a URL responde 404) e sem envio pelo `worker`. Para ligar, ponha `RRPA_WHATSAPP_API=1` no `.env` e reinicie (`docker compose up -d`). Aí:

- A tela *Enviar mensagem* ganha o painel **Disparo automático**: prévia de cada pessoa e de quem fica de fora (com o motivo). Ao confirmar, cria um **Disparo** com uma mensagem por candidato; o `worker` envia no ritmo escolhido e a página *Disparos* mostra **na fila → enviada → entregue → lida**, ou **falhou** / **não enviada**. Dá para pausar, retomar, cancelar e reenviar as falhas.
- Quem recusou (`whatsapp_ok = não`) **nunca** entra, e isso é conferido de novo no instante do envio. Também ficam de fora telefone inválido, telefone repetido no mesmo disparo e quem não tem algum dado que o modelo usa (por exemplo, a vaga). Se a resposta do candidato for **SAIR** (ou PARAR, STOP, DESCADASTRAR, REMOVER), o webhook marca `whatsapp_ok = não` e tira a pessoa da fila.

#### Conectar (com `RRPA_WHATSAPP_API=1`)

1. Entre como administrador e abra **Configurações → Configurar o WhatsApp**.
2. **Simulador**: não precisa de credenciais e **não envia nada de verdade**. Os disparos aparecem como enviados e, segundos depois, entregues e lidos; a *taxa de falhas* mostra como o sistema reage a erros. Use para treinar.
3. **API oficial (Meta)**: informe o ID do número de telefone, o token permanente e a chave secreta do app, e clique em **Salvar e testar conexão**. O passo a passo no Meta for Developers aparece na própria tela.
4. Na Meta (WhatsApp › Configuração), cadastre a **URL de callback** e o **token de verificação** mostrados na tela e assine o campo `messages`. É isso que traz entrega, leitura e as respostas “SAIR”. A URL precisa ser pública e com HTTPS: defina `RRPA_BASE_URL` no `.env`.
5. Fora da janela de 24 h a Meta só aceita **template aprovado**. Em cada modelo de mensagem (bloco *API oficial da Meta*), informe o nome do template aprovado e quais campos preenchem `{{1}}`, `{{2}}`… (por exemplo `primeiro_nome, vaga`). Sem isso o disparo pela Meta é recusado; o Simulador usa o texto do modelo.

O **limite de mensagens por minuto** da conexão vale para todos os disparos somados; cada disparo ainda pode ter um ritmo menor. Uma falha passageira (limite da API, erro 5xx, rede) volta para a fila e espera 1 e depois 5 minutos; na 3ª tentativa a mensagem fica como *falhou*. A opção *Enviar só a quem autorizou* exige que o candidato tenha marcado que aceita WhatsApp (por padrão, só quem recusou é bloqueado).

Para testar o webhook sem a Meta, use o Simulador: ele aceita o formato da Cloud API sem assinatura.

```bash
curl -X POST http://localhost:8765/webhook/whatsapp/<token-da-tela>/ -H "Content-Type: application/json" \
  -d '{"entry":[{"changes":[{"value":{"messages":[{"from":"5511900000001","type":"text","text":{"body":"SAIR"}}]}}]}]}'
```

## Estrutura

```
rrpa/         configurações e rotas
core/         regras comuns: CPF/CEP/prazo, geocodificação, permissões, auditoria, comandos de manutenção
contas/       usuários e perfis
vagas/        cargos e planilhas (.xlsx)
candidatos/   candidatos, importação, links de cadastro, cadastro público
vinculos/     vínculo de 3 meses e check-ins
mensagens/    modelos, disparos de WhatsApp (fila, ritmo, tentativas, eventos do webhook) e registro dos envios manuais
whatsapp/     conexão com o WhatsApp: provedores (Simulador e Meta Cloud API), credenciais criptografadas, webhook
painel/       painel, gaveta de detalhes, painel compartilhável
mapa/         mapa territorial
templates/ static/ docker/
```

Comandos úteis (dentro do container: `docker compose exec web ...`):

| Comando | Faz |
|---|---|
| `python manage.py seed_demo --forcar` | Carrega os dados fictícios. |
| `python manage.py geocodificar` | Localiza candidatos pendentes. |
| `python manage.py disparar --continuo --localizar` | O que o `worker` executa: localiza candidatos e, com `RRPA_WHATSAPP_API=1`, envia a fila dos disparos. Sem `--continuo`, faz uma rodada e sai. |
| `python manage.py createsuperuser` | Cria outro administrador. |

## Rodar sem Docker (desenvolvimento)

```powershell
py -3.13 -m venv .venv
```

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
```

```powershell
$env:DJANGO_DEBUG = "1"; $env:RRPA_SEED_DEMO = "1"; .venv\Scripts\python manage.py migrate; .venv\Scripts\python manage.py createcachetable; .venv\Scripts\python manage.py bootstrap; .venv\Scripts\python manage.py runserver 8765
```

Sem `POSTGRES_HOST` o sistema usa SQLite em `data/db.sqlite3`. Para experimentar o disparo automático, defina `RRPA_WHATSAPP_API=1` e deixe o `worker` rodando em outro terminal (`.venv\Scripts\python manage.py disparar --continuo`). Testes (nenhum acessa a rede; o WhatsApp da Meta é simulado em `requests`):

```powershell
.venv\Scripts\python manage.py test
```

## Segurança e LGPD

- Todas as telas exigem login, exceto o formulário de cadastro por link, o painel compartilhável e a consulta de CEP (com limite de uso). Novas telas já nascem protegidas.
- Com o disparo automático ligado: as credenciais do WhatsApp (token e chave do app) ficam **criptografadas no banco** (Fernet, chave derivada da `SECRET_KEY`) e **nunca voltam ao navegador**: a tela mostra só “Salvo: ••••1234”, e campo em branco mantém o que já está salvo. Se a `SECRET_KEY` mudar, será preciso digitá-las de novo.
- Com o disparo automático ligado: o webhook do WhatsApp é público (o provedor chama), mas a URL tem um token secreto e cada aviso da Meta é conferido pela assinatura `X-Hub-Signature-256`; sem a chave secreta do app salva, o sistema recusa o aviso.
- O registro de atividades dos disparos automáticos guarda só números e contagens, sem nome, telefone ou texto de mensagem.
- Senhas com hash, CSRF, cookies `HttpOnly`/`SameSite`, CSP sem scripts inline, limite de tentativas de login e de cadastro, campo-isca contra robôs.
- CPF mascarado nas listas; só administrador e recrutador veem o CPF completo.
- Formulário público com **autorização LGPD** e **autorização separada para WhatsApp**; CPF repetido não sobrescreve nem revela o cadastro existente.
- *Remover dados pessoais* (administrador) atende pedido de exclusão e preserva o histórico sem identificar a pessoa; isso inclui nome, telefone e texto das mensagens automáticas e das respostas recebidas.
- *Excluir cadastro* (administrador, na ficha do candidato ou em massa na lista) apaga de vez duplicados, erros de digitação e testes. Se a pessoa já foi chamada para uma vaga, o **vínculo e os check-ins também são apagados** e a vaga fica livre (a confirmação avisa quantos). Para atender a um pedido de exclusão de dados **mantendo** o histórico, use *Remover dados pessoais*. As mensagens e os convites ligados ao cadastro ficam sem nome, telefone e texto, e convites ainda abertos são revogados.
- Trilha de atividades (quem fez o quê) em *Administração → Atividades*.
- Planilhas exportadas neutralizam fórmulas (`=`, `+`, `-`, `@`) digitadas por terceiros.

## Limites conhecidos

- O envio normal é **um clique por pessoa** (abre o WhatsApp com a mensagem pronta); a equipe confere e envia cada uma.
- O **disparo automático** (opcional, `RRPA_WHATSAPP_API=1`) com a API da Meta foi escrito de acordo com a documentação da Cloud API e coberto por testes com respostas simuladas, mas **nunca foi executado contra a Meta de verdade** (sem credenciais nem internet no computador onde foi criado). Faça o primeiro teste com o seu próprio número antes de disparar para candidatos.
- Só existe **uma conexão** de WhatsApp por instalação (sem Twilio ou 360dialog como no E-Find) e o disparo **começa na hora**: não há agendamento nem envio de imagem ou arquivo.
- A fila é *pelo menos uma vez*: se o `worker` for interrompido no instante em que a Meta aceitou uma mensagem e antes de o sistema anotar isso, ela volta para a fila e pode ser enviada de novo. Se o aviso de entrega da Meta chegar antes de o sistema gravar o id da mensagem, aquele status se perde.
- Entrega, leitura e “SAIR” dependem do webhook (URL pública com HTTPS). Sem ele as mensagens ficam como *enviada* e o pedido de saída não chega: peça aos candidatos que também avisem a equipe.
- Testado com SQLite e com a suíte de testes (`manage.py test`). A imagem Docker e o PostgreSQL foram escritos e revisados, mas **não puderam ser executados** no computador onde o projeto foi criado (sem Docker instalado). Rode `docker compose up --build` e confira o log do `web` na primeira subida.
- Consulta de CEP e mapa dependem de internet (BrasilAPI, ViaCEP, AwesomeAPI, OpenStreetMap). Sem internet os cadastros funcionam, mas ficam “aguardando localização”. Se o OpenStreetMap estiver fora do ar, o endereço do CEP ainda é preenchido, mas o ponto só é gravado quando for possível conferi-lo.
- O ponto de um CEP é aproximado: vale para a rua (ou, se a rua não estiver no OpenStreetMap, para o bairro). Para refazer todas as localizações, use **Relocalizar** no mapa (administrador).
