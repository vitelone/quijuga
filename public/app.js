// Qui Juga? — frontend (Vanilla JS, sense dependències)

// Les dades de prova només es fan servir en local o amb ?mock: mai en producció.
const ES_LOCAL = ['localhost', '127.0.0.1', ''].includes(location.hostname);
const FONTS_DADES = ES_LOCAL || new URLSearchParams(location.search).has('mock')
  ? ['data/partits_setmana.json', 'data/mock_partits.json']
  : ['data/partits_setmana.json'];
const MARGE_ABANS_MIN = 15;   // marge de cortesia abans de l'inici
const DESCANS_MIN = 10;       // descans estimat entre parts
const REFRESC_MS = 30_000;    // refresc del minut de joc
const CLAU_CAMP = 'quijuga:camp';

const $ = (sel) => document.querySelector(sel);
const el = {
  contingut: $('#contingut'),
  campNom: $('#camp-nom'),
  btnCamp: $('#btn-camp'),
  selector: $('#selector'),
  cerca: $('#cerca'),
  btnGps: $('#btn-gps'),
  llista: $('#llista-camps'),
};

const estat = {
  camps: [],
  camp: null,
  posicio: null, // { lat, lon }
  prova: false,  // s'estan mostrant dades de prova inventades
};

// ---------- Utilitats ----------

const params = new URLSearchParams(location.search);

/** Hora actual. Es pot simular amb ?ara=2026-11-28T10:45 per a proves. */
function ara() {
  const p = params.get('ara');
  const d = p ? new Date(p) : null;
  return d && !isNaN(d) ? d : new Date();
}

const pad = (n) => String(n).padStart(2, '0');
const diaLocal = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

const escapa = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
));

/** Normalitza per a cerca: minúscules i sense accents. */
const normalitza = (s) => String(s ?? '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');

/** Distància Haversine en km. */
function haversine(lat1, lon1, lat2, lon2) {
  const R = 6371;
  const rad = (g) => (g * Math.PI) / 180;
  const dLat = rad(lat2 - lat1);
  const dLon = rad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

const formataDist = (km) => (km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(km < 10 ? 1 : 0)} km`);

const DIES = ['Diumenge', 'Dilluns', 'Dimarts', 'Dimecres', 'Dijous', 'Divendres', 'Dissabte'];

// ---------- Lògica de partits ----------

function inici(p) { return new Date(`${p.dia}T${p.hora}`); }

function fiEstimada(p) {
  return new Date(inici(p).getTime() + ((p.durada_min || 90) + DESCANS_MIN) * 60_000);
}

/** Partit actiu si: inici - 15' <= ara <= fi estimada. */
function esActiu(p, t) {
  return inici(p).getTime() - MARGE_ABANS_MIN * 60_000 <= t && t <= fiEstimada(p).getTime();
}

/** Text d'estat/minut per a un partit actiu o futur. */
function estatPartit(p, t) {
  const ms = t - inici(p).getTime();
  const min = Math.floor(ms / 60_000);
  const durada = p.durada_min || 90;
  const mitja = durada / 2;
  if (min < 0) {
    const falta = -min;
    const txt = falta >= 60 ? `${Math.floor(falta / 60)} h ${pad(falta % 60)}'` : `${falta}'`;
    return { text: `Comença en ${txt}`, proper: true, pendent: true };
  }
  if (min < mitja) return { text: `● 1a part · ${min + 1}'` };
  if (min < mitja + DESCANS_MIN) return { text: '● Descans' };
  const m2 = min - DESCANS_MIN;
  if (m2 < durada) return { text: `● 2a part · ${m2 + 1}'` };
  return { text: 'Final (aprox.)', proper: true };
}

/** Separa "Benjamí Segona Divisió - Grup 14" en categoria i grup. */
function separaCategoria(cat) {
  const [nom, ...resta] = String(cat ?? '').split(' - ');
  return { nom, grup: resta.join(' - ') };
}

function ordenaPartits(a, b) {
  return inici(a) - inici(b) || String(a.subcamp ?? '').localeCompare(String(b.subcamp ?? ''));
}

// ---------- Renderitzat ----------

function htmlTargeta(p, t, idx, total) {
  const { nom, grup } = separaCategoria(p.categoria);
  const st = estatPartit(p, t);
  const sub = p.subcamp || (total > 1 ? `Partit ${String.fromCharCode(65 + idx)}` : '');
  const res = p.resultat_cache;
  return `
    <article class="targeta" data-acta="${escapa(p.id_acta)}">
      <div class="targeta-cap">
        <span class="etiqueta">⚽ ${escapa(p.modalitat || 'F11')}</span>
        ${sub ? `<span class="etiqueta sub">${escapa(sub)}</span>` : ''}
        <span class="categoria">${escapa(nom)}</span>
        ${grup ? `<span class="grup">${escapa(grup)}</span>` : ''}
      </div>
      <div class="equips">
        <span class="local">${escapa(p.local)}</span>
        <span class="${res ? 'marcador' : 'vs'}">${res ? escapa(res) : 'vs'}</span>
        <span class="visitant">${escapa(p.visitant)}</span>
      </div>
      <div class="accions">
        ${st.pendent ? '<span></span>' : `<button class="btn btn-marcador" type="button" data-acta="${escapa(p.id_acta)}">🔄 Resultat</button>`}
        <span class="minut${st.proper ? ' proper' : ''}">${escapa(st.text)}</span>
        <a class="btn primari" href="${escapa(p.url_fcf)}" target="_blank" rel="noopener">Fitxa FCF ↗</a>
      </div>
    </article>`;
}

function htmlJornada(partits, t, titol) {
  if (!partits.length) return '';
  const files = partits.map((p) => {
    const { nom } = separaCategoria(p.categoria);
    const passat = fiEstimada(p).getTime() < t;
    const sub = p.subcamp ? ` · ${p.subcamp}` : '';
    return `
      <li class="${passat ? 'passat' : ''}">
        <span class="h">${escapa(p.hora)}</span>
        <span>${escapa(p.local)} – ${escapa(p.visitant)}
          <span class="cat">${escapa(p.modalitat)}${escapa(sub)} · ${escapa(nom)}</span></span>
        <a href="${escapa(p.url_fcf)}" target="_blank" rel="noopener" aria-label="Fitxa FCF">${p.resultat_cache ? escapa(p.resultat_cache) : '↗'}</a>
      </li>`;
  }).join('');
  return `<details class="jornada"><summary>${escapa(titol)}</summary><ul>${files}</ul></details>`;
}

function render() {
  const camp = estat.camp;
  if (!camp) {
    el.campNom.textContent = 'Tria camp';
    el.contingut.innerHTML = '<p class="estat">Selecciona un camp per veure qui hi juga.</p>';
    return;
  }

  el.campNom.textContent = camp.nom;
  const t = ara();
  const ms = t.getTime();
  const avui = diaLocal(t);
  const partits = [...(camp.partits || [])].sort(ordenaPartits);
  const dAvui = partits.filter((p) => p.dia === avui);
  const actius = dAvui.filter((p) => esActiu(p, ms));
  const hora = `${DIES[t.getDay()]} ${pad(t.getHours())}:${pad(t.getMinutes())}`;

  if (actius.length) {
    // Cas 1 (un partit) o Cas 2 (simultanis F7 Camp A / Camp B)
    el.contingut.innerHTML = `
      <h2 class="titol-seccio"><span class="viu">●</span> Partits en joc ara (${escapa(hora)})</h2>
      <div class="targetes ${actius.length === 1 ? 'unica' : 'multiple'}">
        ${actius.map((p, i) => htmlTargeta(p, ms, i, actius.length)).join('')}
      </div>
      ${htmlJornada(dAvui, ms, `Veure tots els partits d'avui al camp (${dAvui.length})`)}`;
    return;
  }

  // Cas 3: cap partit en joc → pròxim partit (i simultanis a la mateixa hora)
  const futurs = partits.filter((p) => inici(p).getTime() > ms);
  if (futurs.length) {
    const p0 = futurs[0];
    const seguents = futurs.filter((p) => p.dia === p0.dia && p.hora === p0.hora);
    const quan = p0.dia === avui ? `avui a les ${p0.hora}` : `${DIES[inici(p0).getDay()].toLowerCase()} ${p0.dia.slice(8)}/${p0.dia.slice(5, 7)} a les ${p0.hora}`;
    el.contingut.innerHTML = `
      <h2 class="titol-seccio">Cap partit en joc · Pròxim ${escapa(quan)}</h2>
      <div class="targetes ${seguents.length === 1 ? 'unica' : 'multiple'}">
        ${seguents.map((p, i) => htmlTargeta(p, ms, i, seguents.length)).join('')}
      </div>
      ${htmlJornada(dAvui.length ? dAvui : partits.filter((p) => p.dia === p0.dia),
        ms, dAvui.length ? `Resta de la jornada d'avui (${dAvui.length})` : `Jornada del ${p0.dia.slice(8)}/${p0.dia.slice(5, 7)}`)}`;
    return;
  }

  el.contingut.innerHTML = `
    <h2 class="titol-seccio">${escapa(hora)}</h2>
    <p class="estat">No hi ha més partits programats aquesta setmana en aquest camp.</p>
    ${htmlJornada(dAvui, ms, `Partits d'avui (${dAvui.length})`)}`;
}

// ---------- Marcador (Fase 3: Cloudflare Worker) ----------

async function actualitzaMarcador(btn) {
  const acta = btn.dataset.acta;
  btn.disabled = true;
  btn.textContent = '⏳';
  try {
    const r = await fetch(`/api/marcador?acta=${encodeURIComponent(acta)}`, { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    const { resultat } = await r.json();
    if (!resultat) throw new Error('buit');
    const p = estat.camp.partits.find((x) => x.id_acta === acta);
    if (p) p.resultat_cache = resultat;
    render();
  } catch {
    btn.textContent = 'No disponible';
    setTimeout(() => { btn.textContent = '🔄 Resultat'; btn.disabled = false; }, 2500);
  }
}

// ---------- Selecció de camp ----------

function triaCamp(camp, { desa = true } = {}) {
  estat.camp = camp;
  if (desa && camp) {
    localStorage.setItem(CLAU_CAMP, camp.id);
    const url = new URL(location.href);
    url.searchParams.set('camp', camp.id);
    history.replaceState(null, '', url);
  }
  render();
}

function campPerParam(valor) {
  if (!valor) return null;
  const v = normalitza(valor);
  return estat.camps.find((c) => normalitza(c.id) === v)
    || estat.camps.find((c) => normalitza(c.id).endsWith(`_${v}`))
    || null;
}

function campMesProper(pos) {
  let millor = null;
  let dist = Infinity;
  for (const c of estat.camps) {
    if (typeof c.lat !== 'number') continue;
    const d = haversine(pos.lat, pos.lon, c.lat, c.lon);
    if (d < dist) { dist = d; millor = c; }
  }
  return millor;
}

function demanaPosicio() {
  return new Promise((resolve, reject) => {
    if (!('geolocation' in navigator)) return reject(new Error('sense GPS'));
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }),
      reject,
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 60_000 },
    );
  });
}

function renderLlista() {
  const q = normalitza(el.cerca.value.trim());
  const pos = estat.posicio;
  let items = estat.camps.map((c) => ({
    c,
    d: pos && typeof c.lat === 'number' ? haversine(pos.lat, pos.lon, c.lat, c.lon) : null,
    clau: normalitza(`${c.nom} ${c.municipi}`),
  }));
  if (q) {
    const termes = q.split(/\s+/);
    items = items.filter((i) => termes.every((tm) => i.clau.includes(tm)));
  }
  items.sort((a, b) => (a.d != null && b.d != null ? a.d - b.d : a.c.nom.localeCompare(b.c.nom, 'ca')));

  el.llista.innerHTML = items.length
    ? items.slice(0, 100).map(({ c, d }) => `
      <li><button type="button" data-id="${escapa(c.id)}">
        <span><span class="nom">${escapa(c.nom)}</span><span class="mun">${escapa(c.municipi)}</span></span>
        ${d != null ? `<span class="dist">${formataDist(d)}</span>` : ''}
      </button></li>`).join('')
    : '<li class="estat">Cap camp coincideix amb la cerca.</li>';
}

function obreSelector() {
  el.cerca.value = '';
  renderLlista();
  el.selector.showModal();
  el.cerca.focus();
}

async function usaGps({ tancaSelector = false } = {}) {
  el.btnGps.disabled = true;
  el.btnGps.textContent = '📡 Localitzant…';
  try {
    estat.posicio = await demanaPosicio();
    const c = campMesProper(estat.posicio);
    if (c) triaCamp(c);
    if (tancaSelector && el.selector.open) el.selector.close();
    return true;
  } catch {
    el.btnGps.textContent = '📡 Ubicació no disponible';
    return false;
  } finally {
    el.btnGps.disabled = false;
    setTimeout(() => { el.btnGps.textContent = '📡 Usar la meva ubicació'; }, 2000);
    if (el.selector.open) renderLlista();
  }
}

// ---------- Inicialització ----------

async function carregaDades() {
  for (const url of FONTS_DADES) {
    try {
      const r = await fetch(url, { cache: 'no-cache' });
      if (!r.ok) continue;
      const dades = await r.json();
      if (Array.isArray(dades.camps) && dades.camps.length) {
        dades.prova = url.includes('mock');
        return dades;
      }
    } catch { /* prova la següent font */ }
  }
  throw new Error("Encara no hi ha partits carregats per a aquesta setmana");
}

function lligaEsdeveniments() {
  el.btnCamp.addEventListener('click', obreSelector);
  el.cerca.addEventListener('input', renderLlista);
  el.btnGps.addEventListener('click', () => usaGps({ tancaSelector: true }));

  el.llista.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-id]');
    if (!b) return;
    triaCamp(estat.camps.find((c) => c.id === b.dataset.id));
    el.selector.close();
  });

  el.contingut.addEventListener('click', (e) => {
    const b = e.target.closest('.btn-marcador');
    if (b) actualitzaMarcador(b);
  });

  // Tancar el diàleg tocant el fons
  el.selector.addEventListener('click', (e) => { if (e.target === el.selector) el.selector.close(); });

  setInterval(render, REFRESC_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) render(); });
}

async function inicia() {
  lligaEsdeveniments();
  try {
    const dades = await carregaDades();
    estat.camps = dades.camps;
    estat.prova = dades.prova;
    document.body.classList.toggle('prova', estat.prova);
  } catch (err) {
    el.campNom.textContent = 'Error';
    el.contingut.innerHTML = `<p class="estat">${escapa(err.message)}. Torna-ho a provar més tard.</p>`;
    return;
  }

  // Prioritat: ?camp= → GPS → últim camp desat → selector manual
  const perParam = campPerParam(params.get('camp'));
  if (perParam) return triaCamp(perParam);

  const desat = estat.camps.find((c) => c.id === localStorage.getItem(CLAU_CAMP));
  if (desat) triaCamp(desat, { desa: false }); // es mostra mentre el GPS respon

  const ok = await usaGps();
  if (!ok && !desat) {
    render();
    obreSelector();
  }
}

inicia();
