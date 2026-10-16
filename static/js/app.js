/* R-RPA — comportamento da interface. Sem dependências; compatível com a CSP (sem scripts inline). */
(function () {
  "use strict";

  const $ = (sel, raiz) => (raiz || document).querySelector(sel);
  const $$ = (sel, raiz) => Array.from((raiz || document).querySelectorAll(sel));

  /* ------------------------------------------------------------ utilidades */
  function toast(texto, ms) {
    const area = $("#toasts");
    if (!area) return;
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = texto;
    area.appendChild(el);
    setTimeout(() => el.remove(), ms || 2800);
  }

  function csrf() {
    const campo = $("input[name=csrfmiddlewaretoken]");
    if (campo) return campo.value;
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  function copiar(texto) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(texto);
    return new Promise((resolve, reject) => {
      const area = document.createElement("textarea");
      area.value = texto;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try { document.execCommand("copy") ? resolve() : reject(); } catch (e) { reject(e); }
      area.remove();
    });
  }

  window.RRPA = Object.assign(window.RRPA || {}, { toast, csrf, copiar });

  /* ------------------------------------------------------------ menu (celular) */
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-menu]")) {
      document.body.classList.toggle("menu-aberto");
      if (document.body.classList.contains("menu-aberto")) {
        const fundo = document.createElement("div");
        fundo.className = "menu-fundo";
        fundo.addEventListener("click", () => { document.body.classList.remove("menu-aberto"); fundo.remove(); });
        document.body.appendChild(fundo);
      }
    }
  });

  /* ------------------------------------------------------------ gaveta lateral (detalhes sem sair da página) */
  const gaveta = $("#gaveta");
  const fundoGaveta = $("[data-gaveta-fundo]");
  let abridor = null;

  function fecharGaveta() {
    if (!gaveta) return;
    gaveta.classList.remove("aberta");
    fundoGaveta.classList.remove("aberta");
    gaveta.setAttribute("aria-hidden", "true");
    if (abridor && document.contains(abridor)) abridor.focus();
    abridor = null;
  }

  function preencherGaveta(html) {
    const corpo = $("#gaveta-corpo");
    corpo.innerHTML = html; // HTML renderizado pelo próprio servidor (mesma origem), com valores já escapados
    const raiz = $("[data-gaveta-titulo]", corpo);
    $("#gaveta-titulo").textContent = raiz ? raiz.dataset.gavetaTitulo : "Detalhes";
    $("#gaveta-sub").textContent = raiz ? raiz.dataset.gavetaSub || "" : "";
    const pagina = $("#gaveta-pagina");
    if (raiz && raiz.dataset.gavetaPagina) {
      pagina.href = raiz.dataset.gavetaPagina;
      pagina.hidden = false;
    } else {
      pagina.hidden = true;
    }
    corpo.scrollTop = 0;
    iniciar(corpo);
  }

  function abrirGaveta(url, origem) {
    if (!gaveta) { window.location.href = url; return; }
    if (!gaveta.classList.contains("aberta")) abridor = origem || document.activeElement;
    $("#gaveta-corpo").innerHTML = '<div class="gaveta-carregando">Carregando…</div>';
    $("#gaveta-titulo").textContent = "Carregando…";
    $("#gaveta-sub").textContent = "";
    $("#gaveta-pagina").hidden = true;
    gaveta.classList.add("aberta");
    fundoGaveta.classList.add("aberta");
    gaveta.setAttribute("aria-hidden", "false");
    gaveta.focus();
    const alvo = new URL(url, window.location.href);
    alvo.searchParams.set("gaveta", "1");
    fetch(alvo, { headers: { "X-Requested-With": "fetch" }, credentials: "same-origin" })
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(preencherGaveta)
      .catch(() => {
        $("#gaveta-corpo").innerHTML = '<div class="gaveta-carregando">Não foi possível carregar agora. Feche e tente de novo.</div>';
      });
  }

  document.addEventListener("click", (ev) => {
    const link = ev.target.closest("a[data-gaveta]");
    if (link && !ev.defaultPrevented && ev.button === 0 && !(ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey)) {
      ev.preventDefault();
      abrirGaveta(link.getAttribute("href"), link);
      return;
    }
    if (ev.target.closest("[data-gaveta-fechar]") || ev.target === fundoGaveta) fecharGaveta();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && gaveta && gaveta.classList.contains("aberta")) fecharGaveta();
  });
  window.RRPA.abrirGaveta = abrirGaveta;

  /* ------------------------------------------------------------ diálogos (<dialog>) */
  function preencherDialogo(dialogo, origem) {
    Object.entries(origem.dataset).forEach(([chave, valor]) => {
      if (chave.startsWith("campo")) {
        const nome = chave.slice(5, 6).toLowerCase() + chave.slice(6);
        const campos = $$('[name="' + nome + '"]', dialogo);
        campos.forEach((c) => {
          if (c.type === "radio" || c.type === "checkbox") c.checked = c.value === valor;
          else c.value = valor;
        });
      } else if (chave.startsWith("texto") && chave.length > 5) {
        const nome = chave.slice(5, 6).toLowerCase() + chave.slice(6);
        $$('[data-texto="' + nome + '"]', dialogo).forEach((el) => { el.textContent = valor; });
      }
    });
  }

  document.addEventListener("click", (ev) => {
    const abre = ev.target.closest("[data-abrir-dialogo]");
    if (abre) {
      const dialogo = document.getElementById(abre.dataset.abrirDialogo);
      if (dialogo && dialogo.showModal) {
        preencherDialogo(dialogo, abre);
        if (gaveta && gaveta.classList.contains("aberta")) fecharGaveta();
        dialogo.showModal();
        const primeiro = $("[autofocus], textarea, input:not([type=hidden]):not([type=radio]), select", dialogo);
        if (primeiro) primeiro.focus();
      }
      return;
    }
    const fecha = ev.target.closest("[data-fechar-dialogo]");
    if (fecha) { fecha.closest("dialog").close(); return; }
    if (ev.target.tagName === "DIALOG") ev.target.close();
  });

  /* ------------------------------------------------------------ máscaras e CEP */
  const mascaras = {
    cpf(v) {
      v = v.replace(/\D/g, "").slice(0, 11);
      return v.replace(/(\d{3})(\d)/, "$1.$2").replace(/(\d{3})(\d)/, "$1.$2").replace(/(\d{3})(\d{1,2})$/, "$1-$2");
    },
    telefone(v) {
      v = v.replace(/\D/g, "");
      if (v.startsWith("00")) v = v.slice(2);
      if (v.length > 11 && v.startsWith("55")) v = v.slice(2); // número colado com o código do país
      if (v.length > 11) v = v.replace(/^0+/, ""); // 0 de operadora na frente
      v = v.slice(0, 11);
      if (v.length <= 10) return v.replace(/(\d{2})(\d)/, "($1) $2").replace(/(\d{4})(\d)/, "$1-$2");
      return v.replace(/(\d{2})(\d)/, "($1) $2").replace(/(\d{5})(\d)/, "$1-$2");
    },
    cep(v) {
      return v.replace(/\D/g, "").slice(0, 8).replace(/(\d{5})(\d)/, "$1-$2");
    },
  };
  window.RRPA.mascaras = mascaras; // exposto para conferir as máscaras fora da página

  function configurarCep(campo) {
    if (campo.dataset.cepPronto) return;
    campo.dataset.cepPronto = "1";
    const raiz = campo.form || document;
    const status = $("[data-cep-status]", raiz);
    let ultimo = "";
    $$("[data-cep-campo]", raiz).forEach((el) => el.addEventListener("input", () => { delete el.dataset.auto; }));

    async function buscar() {
      const cep = campo.value.replace(/\D/g, "");
      if (cep.length !== 8 || cep === ultimo) return;
      ultimo = cep;
      if (status) status.textContent = "Buscando o endereço…";
      try {
        const resposta = await fetch("/api/cep/" + cep + "/", { headers: { Accept: "application/json" } });
        const dados = await resposta.json();
        if (!resposta.ok) throw new Error(dados.erro || "Não foi possível consultar o CEP.");
        ["logradouro", "bairro", "cidade", "uf"].forEach((nome) => {
          const el = $('[data-cep-campo="' + nome + '"]', raiz);
          if (el && (!el.value || el.dataset.auto === "1")) { el.value = dados[nome] || ""; el.dataset.auto = "1"; }
        });
        if (status) {
          const partes = [dados.logradouro, dados.bairro, dados.cidade && dados.uf ? dados.cidade + "/" + dados.uf : dados.cidade];
          status.textContent = "Endereço encontrado: " + partes.filter(Boolean).join(", ");
        }
      } catch (erro) {
        ultimo = "";
        if (status) status.textContent = erro.message;
      }
    }
    campo.addEventListener("input", buscar);
    campo.addEventListener("blur", buscar);
  }

  /* ------------------------------------------------------------ seleção em massa */
  function configurarSelecao(tabela) {
    if (tabela.dataset.selecaoPronta) return;
    tabela.dataset.selecaoPronta = "1";
    const todos = $("[data-selecionar-todos]", tabela);
    const barra = $("[data-barra-selecao]");
    const contador = $("[data-contador-selecao]");
    const linhas = () => $$("input[name=ids]", tabela);

    function atualizar() {
      const marcadas = linhas().filter((c) => c.checked).length;
      if (barra) barra.hidden = marcadas === 0;
      if (contador) contador.textContent = marcadas === 1 ? "1 selecionado" : marcadas + " selecionados";
      if (todos) {
        todos.checked = marcadas > 0 && marcadas === linhas().length;
        todos.indeterminate = marcadas > 0 && marcadas < linhas().length;
      }
    }
    if (todos) todos.addEventListener("change", () => { linhas().forEach((c) => { c.checked = todos.checked; }); atualizar(); });
    tabela.addEventListener("change", (ev) => { if (ev.target.name === "ids") atualizar(); });
    atualizar();
  }

  /* ------------------------------------------------------------ inicialização reaproveitável (também após carregar a gaveta) */
  function iniciar(raiz) {
    $$("[data-mascara]", raiz).forEach((campo) => {
      if (campo.dataset.mascaraPronta) return;
      campo.dataset.mascaraPronta = "1";
      const aplicar = mascaras[campo.dataset.mascara];
      if (!aplicar) return;
      if (campo.value) campo.value = aplicar(campo.value);
      campo.addEventListener("input", () => { campo.value = aplicar(campo.value); });
    });
    $$("[data-cep]", raiz).forEach(configurarCep);
    $$("table[data-selecionavel]", raiz).forEach(configurarSelecao);
    $$("[data-some-em]", raiz).forEach((el) => {
      if (el.dataset.someProgramado) return;
      el.dataset.someProgramado = "1";
      setTimeout(() => el.remove(), parseInt(el.dataset.someEm, 10) || 6000);
    });
  }
  window.RRPA.iniciar = iniciar;
  iniciar(document);

  /* ------------------------------------------------------------ ações genéricas */
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-imprimir]")) { window.print(); return; }
    const demo = ev.target.closest("[data-preencher-login]");
    if (demo) {
      const usuario = $("#id_username");
      const senha = $("#id_password");
      if (usuario && senha) { usuario.value = demo.dataset.usuario; senha.value = demo.dataset.senha; senha.focus(); }
      return;
    }
    const botao = ev.target.closest("[data-copiar]");
    if (botao) {
      const alvo = botao.dataset.copiar.charAt(0) === "#" ? $(botao.dataset.copiar) : null;
      const texto = alvo ? (alvo.value !== undefined ? alvo.value : alvo.textContent) : botao.dataset.copiar;
      copiar(texto).then(() => toast(botao.dataset.copiado || "Copiado!"), () => toast("Não consegui copiar. Selecione e copie manualmente."));
      return;
    }
    const alternar = ev.target.closest("[data-alternar]");
    if (alternar) {
      const [grafico, tabela] = alternar.dataset.alternar.split("|").map((s) => $(s));
      if (grafico && tabela) {
        const mostrarTabela = !grafico.hidden;
        grafico.hidden = mostrarTabela;
        tabela.hidden = !mostrarTabela;
        alternar.setAttribute("aria-pressed", String(mostrarTabela));
        alternar.textContent = mostrarTabela ? "Ver gráfico" : "Ver como tabela";
      }
    }
  });

  document.addEventListener("click", (ev) => {
    const botao = ev.target.closest("[data-whatsapp]");
    if (!botao) return;
    ev.preventDefault();
    const origem = botao.dataset.textoDe ? $(botao.dataset.textoDe) : null;
    const texto = origem ? (origem.value !== undefined ? origem.value : origem.textContent.trim()) : botao.dataset.texto || "";
    const telefone = (botao.dataset.telefone || "").replace(/\D/g, "");
    if (!telefone) { toast("Este contato não tem um telefone válido."); return; }
    const url = "https://wa.me/" + telefone + (texto ? "?text=" + encodeURIComponent(texto) : "");
    window.open(url, "_blank", "noopener");
    if (botao.dataset.registrar && navigator.sendBeacon) {
      const dados = new FormData();
      dados.append("csrfmiddlewaretoken", csrf());
      dados.append("texto", texto);
      Object.entries(botao.dataset).forEach(([chave, valor]) => {
        if (/^reg[A-Z]/.test(chave)) dados.append(chave.slice(3, 4).toLowerCase() + chave.slice(4), valor);
      });
      navigator.sendBeacon(botao.dataset.registrar, dados);
    }
    const linha = botao.closest("[data-linha-envio]");
    if (linha) { linha.classList.add("enviada"); const marca = $("[data-marca-envio]", linha); if (marca) marca.hidden = false; }
    toast("WhatsApp aberto em outra aba. Confira a mensagem e envie.");
  });

  document.addEventListener("submit", (ev) => {
    const mensagem = ev.target.dataset.confirmar || (ev.submitter && ev.submitter.dataset.confirmar);
    if (mensagem && !window.confirm(mensagem)) { ev.preventDefault(); return; }
    if (ev.target.hasAttribute("data-uma-vez")) {
      $$("button[type=submit], input[type=submit]", ev.target).forEach((b) => { setTimeout(() => { b.disabled = true; }, 0); });
    }
  });

  /* ------------------------------------------------------------ mensagens: espelho, campos e prévia */
  $$("[data-espelhar]").forEach((campo) => {
    const alvo = $(campo.dataset.espelhar);
    if (alvo) campo.addEventListener("input", () => { alvo.textContent = campo.value; });
  });

  const editorModelo = $("textarea[data-previa-mensagem]");
  const saidaModelo = $("#previa-mensagem");
  const exemplosEl = $("#exemplos-campos");
  if (editorModelo && saidaModelo && exemplosEl) {
    const exemplos = JSON.parse(exemplosEl.textContent);
    const atualizar = () => {
      saidaModelo.textContent = editorModelo.value.replace(/\{\{\s*(\w+)\s*\}\}/g, (m, nome) => (nome in exemplos ? exemplos[nome] : m)) || "Escreva a mensagem ao lado.";
    };
    editorModelo.addEventListener("input", atualizar);
    atualizar();
    document.addEventListener("click", (ev) => {
      const botao = ev.target.closest("[data-inserir-campo]");
      if (!botao) return;
      const trecho = "{{" + botao.dataset.inserirCampo + "}}";
      const ini = editorModelo.selectionStart;
      const fim = editorModelo.selectionEnd;
      editorModelo.setRangeText(trecho, ini, fim, "end");
      editorModelo.focus();
      atualizar();
    });
  }

  /* ------------------------------------------------------------ chamar para a vaga: prévia do prazo */
  const campoData = $("input[data-previa-prazo]");
  const saidaPrazo = $("[data-previa-prazo-saida]");
  if (campoData && saidaPrazo) {
    const consultar = async () => {
      if (!campoData.value) return;
      try {
        const r = await fetch("/vinculos/previa-prazo/?data=" + encodeURIComponent(campoData.value), { credentials: "same-origin" });
        const d = await r.json();
        if (!r.ok) throw new Error();
        saidaPrazo.textContent = d.vencido
          ? "Com essa data o prazo já estaria vencido (" + d.fim + "). Escolha uma data mais recente."
          : "Contando " + d.meses.join(", ") + ", a pessoa pode ficar na vaga até " + d.fim + ".";
      } catch (e) {
        saidaPrazo.textContent = "Não consegui calcular o prazo agora.";
      }
    };
    campoData.addEventListener("change", consultar);
    consultar();
  }

  /* ------------------------------------------------------------ conexão do WhatsApp: só os campos do provedor escolhido */
  const seletorProvedor = $("select[data-provedor-select]");
  if (seletorProvedor) {
    const mostrarProvedor = () => $$("[data-so-provedor]").forEach((el) => { el.hidden = el.dataset.soProvedor !== seletorProvedor.value; });
    seletorProvedor.addEventListener("change", mostrarProvedor);
    mostrarProvedor();
  }

  $$("form[data-autoenviar]").forEach((form) => {
    form.addEventListener("change", (ev) => {
      if (ev.target.matches("select, input[type=checkbox], input[type=date], input[type=month]")) form.requestSubmit();
    });
  });
})();
