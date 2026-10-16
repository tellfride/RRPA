/* R-RPA — mapa territorial (Leaflet). Modos: Bairros, Pontos e Calor, como no E-Find.
 *
 * Privacidade: o servidor só envia id e coordenadas aproximadas; nomes, CPFs e telefones nunca chegam aqui.
 * Todo texto vindo do servidor entra no DOM por textContent (nunca como HTML).
 */
(function () {
  "use strict";

  const raiz = document.getElementById("mapa");
  const cfgEl = document.getElementById("mapa-config");
  if (!raiz || !cfgEl || !window.L) return;

  const L = window.L;
  const cfg = JSON.parse(cfgEl.textContent);
  const $ = (sel, base) => (base || document).querySelector(sel);
  const $$ = (sel, base) => Array.from((base || document).querySelectorAll(sel));
  const form = $("#mapa-filtros");
  const fmt = (n) => new Intl.NumberFormat("pt-BR").format(Number(n) || 0);
  const pct1 = (n) => new Intl.NumberFormat("pt-BR", { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(n) + "%";
  const norm = (s) => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const reduzMovimento = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function guardar(chave, valor) { try { localStorage.setItem(chave, valor); } catch (e) { /* modo privado */ } }
  function ler(chave) { try { return localStorage.getItem(chave); } catch (e) { return null; } }
  function no(tag, classe, texto) {
    const e = document.createElement(tag);
    if (classe) e.className = classe;
    if (texto !== undefined && texto !== null) e.textContent = String(texto);
    return e;
  }

  const estado = {
    modo: cfg.defaultMode, base: cfg.defaultBasemap, dados: null, selecionado: null, busca: "", legendaAberta: true,
    verTodosSemGeo: false,
  };
  const modosValidos = cfg.modes.map((m) => m.value);
  if (modosValidos.includes(ler("rrpa:mapa:modo"))) estado.modo = ler("rrpa:mapa:modo");
  if (cfg.basemaps[ler("rrpa:mapa:base")]) estado.base = ler("rrpa:mapa:base");

  /* ------------------------------------------------------------ mapa base */
  const mapa = L.map(raiz, { center: cfg.center, zoom: cfg.zoom, minZoom: 3, maxZoom: 19, preferCanvas: true });
  let tiles = null;
  function aplicarBase(chave) {
    const b = cfg.basemaps[chave];
    if (!b) return;
    if (tiles) mapa.removeLayer(tiles);
    tiles = L.tileLayer(b.url, { attribution: b.attribution, maxZoom: b.maxZoom, className: b.className || "" }).addTo(mapa);
    tiles.bringToBack();
  }
  aplicarBase(estado.base);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => mapa.invalidateSize()).observe(raiz);

  const camadas = { bairros: null, pontos: null, calor: null };
  const marcadores = new Map();
  const referencias = L.layerGroup().addTo(mapa);
  let controle = null;

  // Unidade e anéis de 5, 10 e 20 km (extra do R-RPA: mostra o deslocamento até o local de trabalho)
  function desenharUnidade(u) {
    referencias.clearLayers();
    if (!u || u.lat === null || u.lng === null) return;
    const icone = L.divIcon({
      className: "", iconSize: [34, 34], iconAnchor: [17, 17],
      html: '<div class="mapa-pino"><svg class="icon" aria-hidden="true"><use href="' + cfg.sprite + '#i-building-2"></use></svg></div>',
    });
    L.marker([u.lat, u.lng], { icon: icone, title: u.nome, zIndexOffset: 1000 })
      .bindTooltip(u.nome, { direction: "top", offset: [0, -16] }).addTo(referencias);
    [5, 10, 20].forEach((km) => {
      L.circle([u.lat, u.lng], { radius: km * 1000, color: "#10272b", weight: 1, opacity: 0.3, fill: false, interactive: false }).addTo(referencias);
      const topo = L.latLng(u.lat, u.lng).toBounds(km * 2000).getNorth();
      L.marker([topo, u.lng], {
        interactive: false, keyboard: false,
        icon: L.divIcon({ className: "anel-rotulo", html: km + " km", iconSize: [40, 14], iconAnchor: [20, 7] }),
      }).addTo(referencias);
    });
  }

  /* ------------------------------------------------------------ popups (nós DOM) */
  function linkCandidatos(b) {
    const d = estado.dados;
    if (!d || !d.can_view_candidates || !b.known) return null;
    const p = new URLSearchParams({ bairro: b.name, cidade: b.city });
    return cfg.candidatosUrl + "?" + p.toString();
  }

  function popupBairro(b) {
    const caixa = no("div", "mv-popup");
    caixa.append(no("div", "mv-popup-titulo", b.name), no("div", "mv-popup-sub", (b.city || "Cidade não informada") + (b.uf ? "/" + b.uf : "")));
    const lista = no("dl");
    const linha = (rotulo, valor) => lista.append(no("dt", "", rotulo), no("dd", "", fmt(valor)));
    linha("Candidatos", b.count);
    linha("Em vaga", b.em_vaga);
    linha("Aprovados aguardando vaga", b.aprovados);
    linha("Aceitam WhatsApp", b.whatsapp);
    if (b.count - b.mapped > 0) linha("Sem localização no mapa", b.count - b.mapped);
    caixa.append(lista);
    if (b.top_indicadores && b.top_indicadores.length) {
      caixa.append(no("div", "rotulo", "Quem mais trouxe candidatos"));
      const ol = no("ol");
      b.top_indicadores.forEach((m) => ol.append(no("li", "", m.name + " — " + fmt(m.count))));
      caixa.append(ol);
    }
    if (estado.dados && estado.dados.can_view_candidates && b.known) {
      const area = no("div", "mv-candidatos");
      const botao = no("button", "btn btn-sm", "Ver quem são");
      botao.type = "button";
      botao.addEventListener("click", () => { botao.remove(); listarCandidatos(area, new URLSearchParams({ bairro: b.name, cidade: b.city })); });
      area.append(botao);
      caixa.append(area);
    }
    const href = linkCandidatos(b);
    if (href) { const a = no("a", "", "Abrir na lista de candidatos →"); a.href = href; caixa.append(a); }
    return caixa;
  }

  /* Quem são os candidatos (nome, situação e vaga a que se candidatou): só para quem pode abrir cadastros. */
  async function listarCandidatos(area, extra) {
    area.append(no("p", "small muted", "Carregando…"));
    try {
      const p = new URLSearchParams(parametros());
      extra.forEach((v, k) => p.set(k, v));
      const r = await fetch(cfg.candidatosApi + "?" + p.toString(), { credentials: "same-origin", headers: { Accept: "application/json" } });
      const d = await r.json();
      if (!r.ok) throw new Error(d.erro || "falha");
      area.textContent = "";
      const ul = no("ul", "mv-lista");
      d.candidatos.forEach((c) => {
        const li = no("li");
        const a = no("a", "", c.nome);
        a.href = c.url;
        const vaga = c.na_vaga ? "em " + c.na_vaga : c.vagas.length ? "quer " + c.vagas.join(", ") : "sem vaga de interesse";
        li.append(a, no("div", "small muted", c.situacao + " · " + vaga + (c.telefone ? " · " + c.telefone : "") + (c.localizado ? "" : " · sem localização")));
        ul.append(li);
      });
      area.append(ul);
      if (d.total > d.candidatos.length) area.append(no("p", "small muted", "Mostrando " + d.candidatos.length + " de " + fmt(d.total) + "."));
    } catch (e) {
      area.textContent = "";
      area.append(no("p", "small", "Não foi possível carregar agora."));
    }
  }

  function popupPonto(p) {
    const caixa = no("div", "mv-popup");
    caixa.append(no("div", "mv-popup-titulo", "Candidato cadastrado"), no("div", "mv-popup-sub", (cfg.rotulos[p.status] || "") + " · localização aproximada (CEP)"));
    if (estado.dados && estado.dados.can_view_candidates) {
      const area = no("div", "mv-candidatos");
      caixa.append(area);
      listarCandidatos(area, new URLSearchParams({ id: p.id }));
      const a = no("a", "", "Abrir cadastro →");
      a.href = cfg.candidatosUrl + p.id + "/";
      caixa.append(a);
    }
    return caixa;
  }

  /* ------------------------------------------------------------ camadas */
  function camadaBairros() {
    const grupo = L.layerGroup();
    estado.dados.neighborhoods.forEach((b) => {
      const m = L.circleMarker([b.lat, b.lng], { radius: b.radius, color: "#ffffff", weight: 1.5, fillColor: b.color, fillOpacity: 0.85 });
      m.bindTooltip(() => no("span", "", b.name + " · " + fmt(b.count)), { direction: "top", offset: [0, -b.radius], opacity: 1 });
      m.bindPopup(() => popupBairro(b), { minWidth: 230, maxWidth: 310, autoPanPadding: [16, 16] });
      m.on("click", () => selecionar(b.key));
      marcadores.set(b.key, m);
      grupo.addLayer(m);
    });
    return grupo;
  }

  function camadaPontos() {
    const pontos = estado.dados.points;
    if (!pontos.length || !L.markerClusterGroup) return null;
    const grupo = L.markerClusterGroup(Object.assign({
      chunkedLoading: true, showCoverageOnHover: false, spiderfyOnMaxZoom: true,
      iconCreateFunction: (c) => {
        const n = c.getChildCount();
        const px = n < 10 ? 34 : n < 100 ? 42 : 52;
        const miolo = no("span", "", n >= 10000 ? Math.round(n / 1000) + " mil" : fmt(n));
        const casca = no("div", "rrpa-cluster");
        casca.style.width = casca.style.height = px + "px";
        casca.append(miolo);
        return L.divIcon({ html: casca, className: "", iconSize: L.point(px, px) });
      },
    }, cfg.clusterOptions));
    grupo.addLayers(pontos.map((p) => L.marker([p.lat, p.lng], {
      keyboard: false,
      icon: L.divIcon({ className: "", html: '<div class="rrpa-ponto ' + p.status + '"></div>', iconSize: [14, 14], iconAnchor: [7, 7], popupAnchor: [0, -8] }),
    }).bindPopup(() => popupPonto(p), { minWidth: 200 })));
    return grupo;
  }

  function camadaCalor() {
    const pontos = estado.dados.points;
    if (!pontos.length || !L.heatLayer) return null;
    return L.heatLayer(pontos.map((p) => [p.lat, p.lng, 1]), Object.assign({}, cfg.heatOptions, { gradient: cfg.heatGradient }));
  }

  function camada(modo) {
    if (!camadas[modo]) camadas[modo] = modo === "pontos" ? camadaPontos() : modo === "calor" ? camadaCalor() : camadaBairros();
    return camadas[modo];
  }

  function descartarCamadas() {
    Object.keys(camadas).forEach((m) => { if (camadas[m] && mapa.hasLayer(camadas[m])) mapa.removeLayer(camadas[m]); camadas[m] = null; });
    marcadores.clear();
  }

  function desenhar() {
    if (!estado.dados) return;
    Object.keys(camadas).forEach((m) => { if (camadas[m] && mapa.hasLayer(camadas[m])) mapa.removeLayer(camadas[m]); });
    const c = camada(estado.modo);
    if (c) mapa.addLayer(c);
    destacar(estado.selecionado, true);
    desenharLegenda();
  }

  function destacar(chave, ativo) {
    const m = chave ? marcadores.get(chave) : null;
    if (!m) return;
    m.setStyle({ color: ativo ? "#10272b" : "#ffffff", weight: ativo ? 3 : 1.5 });
    if (ativo && mapa.hasLayer(m)) m.bringToFront();
  }

  function selecionar(chave) {
    if (estado.selecionado === chave) return;
    destacar(estado.selecionado, false);
    estado.selecionado = chave;
    destacar(chave, true);
    desenharRanking();
  }

  function enquadrar() {
    mapa.invalidateSize();
    const d = estado.dados;
    if (d && d.bounds) mapa.fitBounds(d.bounds, { padding: [32, 32], maxZoom: cfg.fitMaxZoom });
    else if (d && d.unidade && d.unidade.lat !== null) mapa.setView([d.unidade.lat, d.unidade.lng], 11);
    else mapa.setView(cfg.center, cfg.zoom);
  }

  /* ------------------------------------------------------------ painel lateral */
  function definir(id, texto) { const e = document.getElementById(id); if (e) e.textContent = texto; }
  function mostrar(id, visivel) { const e = document.getElementById(id); if (e) e.hidden = !visivel; }

  function desenharLegenda() {
    const d = estado.dados;
    const caixa = document.getElementById("mapa-legenda");
    mostrar("mapa-legenda", !!d && d.totals.with_geo > 0);
    if (!d) return;
    const titulo = estado.modo === "bairros" ? "Candidatos por bairro" : estado.modo === "pontos" ? "Candidatos" : "Concentração";
    caixa.replaceChildren();
    const botao = no("button");
    botao.type = "button";
    botao.setAttribute("aria-expanded", String(estado.legendaAberta));
    botao.append(no("span", "", titulo), no("span", "", estado.legendaAberta ? "−" : "+"));
    botao.addEventListener("click", () => { estado.legendaAberta = !estado.legendaAberta; desenharLegenda(); });
    caixa.append(botao);
    if (!estado.legendaAberta) return;
    const corpo = no("div", "corpo");
    if (estado.modo === "bairros") {
      const ul = no("ul");
      d.legend.bins.forEach((b) => {
        const s = no("i", "amostra-cor");
        s.style.background = b.color;
        ul.append(no("li", "", "")); ul.lastChild.append(s, no("span", "num", b.label));
      });
      corpo.append(ul, no("p", "nota", "O tamanho do círculo é proporcional ao total."));
    } else if (estado.modo === "pontos") {
      const ul = no("ul");
      [["em_vaga", "Em vaga"], ["aprovado", "Aprovado"], ["pendente", "Aguardando aprovação"]].forEach(([chave, rotulo]) => {
        const s = no("i", "amostra-cor");
        s.style.background = chave === "em_vaga" ? "#2a78d6" : chave === "aprovado" ? "#1baf7a" : "#898781";
        ul.append(no("li", "", "")); ul.lastChild.append(s, no("span", "", rotulo));
      });
      corpo.append(ul, no("p", "nota", "Perto uns dos outros, os pontos se agrupam. Toque no grupo para aproximar."));
    } else {
      const g = no("div", "gradiente-calor");
      const paradas = Object.entries(cfg.heatGradient).sort((a, b) => Number(a[0]) - Number(b[0])).map(([p, c]) => c + " " + Number(p) * 100 + "%");
      g.style.background = "linear-gradient(to right, " + paradas.join(", ") + ")";
      corpo.append(g, no("p", "nota", "Menor → maior concentração."));
    }
    caixa.append(corpo);
  }

  function desenharRanking() {
    const d = estado.dados;
    const lista = document.getElementById("ranking-lista");
    lista.replaceChildren();
    if (!d) return;
    const q = norm(estado.busca.trim());
    const todos = d.neighborhoods.map((b, i) => ({ b, posicao: i + 1 })).filter((x) => !q || norm(x.b.name + " " + x.b.city).includes(q));
    definir("ranking-contagem", fmt(d.neighborhoods.length));
    mostrar("ranking-busca-caixa", d.neighborhoods.length > 0);
    const maximo = d.legend.max_count || 1;
    todos.slice(0, 200).forEach(({ b, posicao }) => {
      const botao = no("button", "ranking-item");
      botao.type = "button";
      botao.title = "Ver no mapa";
      if (estado.selecionado === b.key) botao.setAttribute("aria-current", "true");
      const barra = no("i");
      barra.style.width = Math.min(100, (100 * b.count) / maximo).toFixed(1) + "%";
      const linha = no("div", "linha");
      linha.append(no("span", "nome", posicao + ". " + b.name), no("span", "valor", fmt(b.count)));
      const sub = no("div", "linha");
      sub.append(no("span", "sub", (b.city || "") + (b.uf ? "/" + b.uf : "")), no("span", "sub", d.totals.candidatos ? pct1((100 * b.count) / d.totals.candidatos) : ""));
      const trilho = no("div", "barra-trilho");
      trilho.append(barra);
      botao.append(linha, sub, trilho);
      botao.addEventListener("click", () => focarBairro(b.key));
      lista.append(botao);
    });
    if (!d.neighborhoods.length) lista.append(no("p", "muted small", "Nenhum bairro com candidatos localizados."));
    else if (!todos.length) lista.append(no("p", "muted small", "Nenhum bairro encontrado para a busca."));
    mostrar("ranking-mais", todos.length > 200);
  }

  function desenharSemGeo() {
    const d = estado.dados;
    const itens = d.neighborhoods_without_geo;
    mostrar("semgeo-box", itens.length > 0);
    if (!itens.length) return;
    const soma = itens.reduce((s, i) => s + i.count, 0);
    definir("semgeo-texto", fmt(soma) + " candidato(s) em bairros ainda sem coordenadas; por isso não aparecem no mapa.");
    const lista = document.getElementById("semgeo-lista");
    lista.replaceChildren();
    (estado.verTodosSemGeo ? itens : itens.slice(0, 8)).forEach((i) => {
      const li = no("li", "");
      const esq = no("div");
      esq.append(no("div", i.known ? "" : "muted", i.name), no("div", "small muted", i.city));
      li.append(esq, no("strong", "num", fmt(i.count)));
      lista.append(li);
    });
    const botao = document.getElementById("semgeo-toggle");
    botao.hidden = itens.length <= 8;
    botao.textContent = estado.verTodosSemGeo ? "Mostrar menos" : "Mostrar todos (" + fmt(itens.length) + ")";
  }

  function desenharNumeros() {
    const t = estado.dados.totals;
    definir("st-nomapa", fmt(t.with_geo));
    definir("st-cobertura", (t.candidatos ? pct1((100 * t.with_geo) / t.candidatos) : "0,0%") + " de " + fmt(t.candidatos) + " candidatos");
    definir("st-sem", fmt(t.without_geo));
    definir("st-aprox", fmt(t.approximate));
    mostrar("aviso-amostra", estado.dados.truncated && estado.modo !== "bairros");
    definir("aviso-amostra-texto", "Exibindo uma amostra de " + fmt(estado.dados.points.length) + " dos " + fmt(t.with_geo) + " candidatos localizados. Use os filtros para detalhar uma região.");
  }

  function focarBairro(chave) {
    const b = estado.dados.neighborhoods.find((x) => x.key === chave);
    if (!b) return;
    if (estado.modo !== "bairros") definirModo("bairros");
    selecionar(chave);
    const area = document.getElementById("mapa-area");
    const r = area.getBoundingClientRect();
    if (r.top < 64 || r.bottom > window.innerHeight) area.scrollIntoView({ behavior: reduzMovimento ? "auto" : "smooth", block: "center" });
    let aberto = false;
    const abrir = () => {
      if (aberto) return;
      aberto = true;
      mapa.off("moveend", abrir);
      const m = marcadores.get(chave);
      if (m && mapa.hasLayer(m)) m.openPopup();
    };
    setTimeout(abrir, 1600);
    mapa.once("moveend", abrir);
    mapa.flyTo([b.lat, b.lng], Math.max(mapa.getZoom(), cfg.focusZoom), { duration: reduzMovimento ? 0 : 0.8 });
  }

  /* ------------------------------------------------------------ carga dos dados */
  function parametros() {
    const p = new URLSearchParams();
    if (form.elements.vaga.value) p.set("vaga", form.elements.vaga.value);
    p.set("status", $$("input[name=status]:checked", form).map((c) => c.value).join(","));
    if (form.elements.ate_km.value) p.set("ate_km", form.elements.ate_km.value);
    if (form.elements.q.value.trim()) p.set("q", form.elements.q.value.trim());
    return p;
  }

  async function carregar(ajustar) {
    if (controle) controle.abort();
    const meu = new AbortController();
    controle = meu;
    mostrar("mapa-atualizando", !!estado.dados);
    mostrar("mapa-carregando", !estado.dados);
    mostrar("mapa-erro", false);
    const p = parametros();
    try {
      const r = await fetch(cfg.dataUrl + "?" + p.toString(), { headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store", signal: meu.signal });
      if (r.status === 401 || r.redirected) { window.location.assign("/login/?next=" + encodeURIComponent(window.location.pathname)); return; }
      const corpo = await r.json().catch(() => null);
      if (!r.ok || !corpo || !corpo.totals) throw new Error((corpo && corpo.erro) || "Não foi possível carregar os dados do mapa.");
      aplicar(corpo, ajustar);
      const url = new URL(window.location.href);
      url.search = p.toString();
      history.replaceState(null, "", url);
    } catch (e) {
      if (meu.signal.aborted) return;
      mostrar("mapa-erro", !estado.dados);
      definir("mapa-erro-texto", e && e.name !== "TypeError" && e.message ? e.message : "Falha de conexão. Verifique a internet e tente de novo.");
      if (estado.dados) window.RRPA.toast("Não consegui atualizar o mapa.");
    } finally {
      if (controle === meu) { mostrar("mapa-carregando", false); mostrar("mapa-atualizando", false); }
    }
  }

  function aplicar(corpo, ajustar) {
    estado.dados = corpo;
    descartarCamadas();
    if (estado.selecionado && !corpo.neighborhoods.some((b) => b.key === estado.selecionado)) estado.selecionado = null;
    desenharUnidade(corpo.unidade);
    desenharNumeros();
    desenharRanking();
    desenharSemGeo();
    mostrar("mapa-vazio", corpo.totals.with_geo === 0);
    definir("mapa-vazio-texto", corpo.totals.candidatos > 0
      ? fmt(corpo.totals.candidatos) + " candidato(s) encontrado(s), ainda sem localização (CEP ausente ou aguardando processamento)."
      : "Ajuste os filtros ou cadastre candidatos com CEP para vê-los no mapa.");
    desenhar();
    if (ajustar) enquadrar();
  }

  /* ------------------------------------------------------------ controles */
  function definirModo(modo) {
    if (!modosValidos.includes(modo) || modo === estado.modo) return;
    estado.modo = modo;
    guardar("rrpa:mapa:modo", modo);
    marcarSegmentos();
    desenhar();
    desenharNumeros();
  }
  function definirBase(chave) {
    if (!cfg.basemaps[chave] || chave === estado.base) return;
    estado.base = chave;
    guardar("rrpa:mapa:base", chave);
    marcarSegmentos();
    aplicarBase(chave);
  }
  function marcarSegmentos() {
    $$("[data-modo]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.modo === estado.modo)));
    $$("[data-base]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.base === estado.base)));
  }
  $$("[data-modo]").forEach((b) => b.addEventListener("click", () => definirModo(b.dataset.modo)));
  $$("[data-base]").forEach((b) => b.addEventListener("click", () => definirBase(b.dataset.base)));
  marcarSegmentos();
  document.getElementById("btn-centralizar").addEventListener("click", enquadrar);
  document.getElementById("btn-tentar-de-novo").addEventListener("click", () => carregar(true));

  let espera = null;
  form.addEventListener("input", () => { clearTimeout(espera); espera = setTimeout(() => carregar(true), 350); });
  form.addEventListener("submit", (ev) => { ev.preventDefault(); carregar(true); });
  form.addEventListener("reset", () => setTimeout(() => carregar(true), 0));
  document.getElementById("ranking-busca").addEventListener("input", (ev) => { estado.busca = ev.target.value; desenharRanking(); });
  document.getElementById("semgeo-toggle").addEventListener("click", () => { estado.verTodosSemGeo = !estado.verTodosSemGeo; desenharSemGeo(); });

  const botaoGeo = document.getElementById("btn-localizar");
  if (botaoGeo) {
    const localizarPendentes = async (rodadas) => {
      botaoGeo.disabled = true;
      const rotulo = botaoGeo.textContent;
      try {
        for (let i = 0; i < rodadas; i++) {
          botaoGeo.textContent = "Localizando…";
          const r = await fetch(cfg.geoUrl, { method: "POST", headers: { "X-CSRFToken": window.RRPA.csrf() }, credentials: "same-origin" });
          const d = await r.json();
          if (!r.ok) throw new Error(d.erro || "falha");
          definir("fila-pendentes", fmt(d.restantes));
          definir("fila-falharam", fmt(d.falharam));
          botaoGeo.textContent = "Localizando… faltam " + fmt(d.restantes);
          if (!d.restantes || !d.processados) break;
        }
        window.RRPA.toast("Localizações atualizadas.");
      } catch (e) {
        window.RRPA.toast(e.message && e.message !== "falha" ? e.message : "A consulta de CEP não respondeu. Tente de novo em instantes.");
      }
      botaoGeo.textContent = rotulo;
      botaoGeo.disabled = false;
      carregar(false);
    };
    botaoGeo.addEventListener("click", () => localizarPendentes(30));
    // Candidatos recém-cadastrados ficam "aguardando localização": ao abrir o mapa a equipe já localiza alguns, sem precisar clicar.
    if (!botaoGeo.disabled) setTimeout(() => localizarPendentes(3), 1200);
  }
  const botaoReloc = document.getElementById("btn-relocalizar");
  if (botaoReloc) {
    botaoReloc.addEventListener("click", async () => {
      if (!window.confirm("Relocalizar todos os candidatos? Eles voltam para a fila e serão localizados de novo pelo CEP. Pode levar um tempo.")) return;
      const r = await fetch(cfg.relocUrl, { method: "POST", headers: { "X-CSRFToken": window.RRPA.csrf() }, credentials: "same-origin" });
      const d = await r.json().catch(() => ({}));
      window.RRPA.toast(r.ok ? fmt(d.recolocados) + " candidatos voltaram para a fila de localização." : "Não foi possível relocalizar agora.");
      if (r.ok) window.location.reload();
    });
  }

  // Filtros vindos do endereço (link compartilhado ou voltar ao mapa)
  const inicial = new URLSearchParams(window.location.search);
  if (inicial.get("vaga")) form.elements.vaga.value = inicial.get("vaga");
  if (inicial.get("ate_km")) form.elements.ate_km.value = inicial.get("ate_km");
  if (inicial.get("q")) form.elements.q.value = inicial.get("q");
  if (inicial.get("status")) {
    const marcados = inicial.get("status").split(",");
    $$("input[name=status]", form).forEach((c) => { c.checked = marcados.includes(c.value); });
  }
  if (cfg.unidade && cfg.unidade.lat !== null) mapa.setView([cfg.unidade.lat, cfg.unidade.lng], 11);
  carregar(true);
})();
