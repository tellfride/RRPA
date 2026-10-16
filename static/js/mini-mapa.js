/* R-RPA — mini mapa da ficha do candidato (um ponto, sem filtros). */
(function () {
  "use strict";
  const alvo = document.getElementById("mini-mapa");
  const dadosEl = document.getElementById("mini-mapa-dados");
  if (!alvo || !dadosEl || !window.L) return;
  const d = JSON.parse(dadosEl.textContent);
  const mapa = L.map(alvo, { scrollWheelZoom: false, zoomControl: true }).setView([d.lat, d.lng], d.zoom);
  L.tileLayer(d.tiles, { maxZoom: 19, attribution: d.atribuicao }).addTo(mapa);
  L.circleMarker([d.lat, d.lng], { radius: 9, weight: 3, color: "#ffffff", fillColor: "#2a78d6", fillOpacity: 1 }).addTo(mapa);
  if (d.unidade) {
    L.circleMarker([d.unidade.lat, d.unidade.lng], { radius: 7, weight: 3, color: "#ffffff", fillColor: "#10272b", fillOpacity: 1 })
      .bindTooltip(d.unidade.nome).addTo(mapa);
    mapa.fitBounds([[d.lat, d.lng], [d.unidade.lat, d.unidade.lng]], { padding: [28, 28], maxZoom: 15 });
  }
})();
