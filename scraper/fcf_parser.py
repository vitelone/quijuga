"""Única part acoblada a l'HTML de fcf.cat (Next.js App Router, analitzat el nov. 2026).

Què hi ha al HTML que serveix el servidor (sense executar JS):
  - /ca/clubs/<club>/categories/<equip>   array `matches` amb TOTA la temporada de
                           l'equip: data/hora, camp + lat/lon, equips, gols, URL acta.
                           Font principal del scraper.
  - /ca/partit/...         (acta) data, hora, estadi (amb slug), equips, marcador.
  - /ca/camps/<slug>       JSON `initialData` amb latitud/longitud, localitat…
  - /ca/competicio/.../resultats   només `initialResolvedData` + `initialJornada`;
                                    la llista de partits es carrega des del client
                                    (probablement via /api/, prohibit per robots.txt).

Privacitat: l'acta i la fitxa d'equip contenen noms de menors (alineacions,
plantilla) i de tècnics. NO s'extreuen mai.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import unicodedata
import re
from datetime import date
from typing import Iterable, Iterator
from urllib.parse import urlparse

from collections import deque

from bs4 import BeautifulSoup

from http_client import Blocat, ClientEducat, Prohibit
from model import PartitBrut

log = logging.getLogger(__name__)

RE_PUSH = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)
RE_PARTIT = re.compile(
    r"^/ca/partit/(?P<temp>\d{4}-\d{4})/(?P<comp>[^/]+)/(?P<grup>[^/]+)/jornada-(?P<j>\d+)/(?P<slug>[^/]+)/?$"
)


# ---------- Utilitats Next.js ----------

def flight(html: str) -> str:
    """Concatena el payload RSC (`self.__next_f.push([1, "..."])`) de la pàgina."""
    trossos = []
    for m in RE_PUSH.finditer(html):
        try:
            arr = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if len(arr) > 1 and isinstance(arr[1], str):
            trossos.append(arr[1])
    return "".join(trossos)


def objecte_json(text: str, clau: str):
    """Retorna el valor JSON que segueix `"clau":` dins el text, o None."""
    i = text.find(f'"{clau}":')
    if i < 0:
        return None
    inici = i + len(clau) + 3
    while inici < len(text) and text[inici] in " \t\r\n":
        inici += 1
    try:
        valor, _ = json.JSONDecoder().raw_decode(text, inici)
    except json.JSONDecodeError:
        return None
    return valor


PARTICULES = {"de", "del", "dels", "la", "les", "el", "els", "i", "a", "d'", "l'"}
ABREVIATURES = {"mpal.": "Municipal", "mpal": "Municipal", "mun.": "Municipal", "c.f.": "C.F.", "c.e.": "C.E.",
                "u.e.": "U.E.", "z.e.": "Z.E.", "cmf": "CMF", "ze": "ZE"}


def bonic(nom: str) -> str:
    """'CAMP DE FUTBOL MPAL. HORTA' -> 'Camp de Futbol Municipal Horta'."""
    paraules = []
    for i, p in enumerate(nom.strip().lower().split()):
        if p in ABREVIATURES:
            paraules.append(ABREVIATURES[p])
        elif i and p in PARTICULES:
            paraules.append(p)
        elif re.match(r"^[ld]'", p):
            paraules.append(p[:2] + p[2:].capitalize() if i else p[:2].upper() + p[2:].capitalize())
        else:
            paraules.append("-".join(x.capitalize() for x in p.split("-")))
    return " ".join(paraules)


# ---------- Acta ----------

RE_CAMP = re.compile(r"^/ca/camps(?:-futbol-sala|-futbol-platja)?/(?P<slug>[^/]+)/?$")


def _fitxa_camp(href: str) -> str | None:
    """'/ca/camps-futbol-sala/x#info' -> '/ca/camps-futbol-sala/x'."""
    m = RE_CAMP.match(urlparse(href).path)
    return m.group(0).rstrip("/") if m else None


def _lletra_equip(href: str) -> str | None:
    """'/ca/clubs/horta-uat/categories/horta-uat-a-1b9' -> 'A'."""
    m = re.search(r"/clubs/([^/]+)/categories/([^/?#]+)", href)
    if not m or not m.group(2).startswith(m.group(1) + "-"):
        return None
    resta = m.group(2)[len(m.group(1)) + 1:].split("-")[0]
    return resta.upper() if re.fullmatch(r"[a-z]", resta) else None


def parse_acta(html: str, url: str = "") -> PartitBrut | None:
    soup = BeautifulSoup(html, "lxml")
    canon = soup.find("link", rel="canonical")
    url = (canon.get("href") if canon else None) or url
    m_url = RE_PARTIT.match(urlparse(url).path)

    # Barra d'informació: "TEMPORADA : 2026 / 27 DATA: 22.11.2026 HORARI: 09.00h ESTADI: …"
    ancora = soup.find(string=re.compile(r"\bDATA:"))
    barra = ancora.find_parent("div", class_=re.compile("flex-wrap")) if ancora else None
    text = (barra or soup).get_text(" ", strip=True)
    m_dia = re.search(r"DATA:\s*(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    m_hora = re.search(r"HORARI:\s*(\d{1,2})[.:h](\d{2})", text)
    if not m_dia or not m_hora:
        log.info("Acta sense data/hora (encara no programada?): %s", url)
        return None
    d, mes, a = (int(x) for x in m_dia.groups())
    dia = f"{a:04d}-{mes:02d}-{d:02d}"
    hora = f"{int(m_hora.group(1)):02d}:{m_hora.group(2)}"

    estadi = (barra or soup).find("a", href=re.compile(r"/camps(-futbol-sala|-futbol-platja)?/"))
    camp_nom = re.sub(r"^\s*ESTADI:\s*", "", estadi.get_text(" ", strip=True)) if estadi else ""
    camp_fitxa = _fitxa_camp(estadi["href"]) if estadi else None

    # Breadcrumb: COMPETICIÓ / <competició> / <grup> / Jornada N
    cat = ""
    boto = soup.find("button", string=re.compile(r"^\s*COMPETICI"))
    if boto:
        parts = [s.get_text(" ", strip=True) for s in boto.parent.find_all("span")]
        parts = [p for p in parts if p and p != "/" and not p.lower().startswith("jornada")]
        cat = " - ".join(parts[:2])

    # Equips: primer bloc amb dos enllaços a /categories/ (sense jugadors)
    local = visitant = ""
    resultat = None
    for a in soup.find_all("a", href=re.compile(r"/clubs/[^/]+/categories/"), title=True):
        fila = a.parent
        equips = fila.find_all("a", href=re.compile(r"/categories/"), title=True, recursive=False)
        if len(equips) >= 2:
            noms = []
            for e in equips[:2]:
                lletra = _lletra_equip(e["href"])
                noms.append(f"{e['title'].strip()} '{lletra}'" if lletra else e["title"].strip())
            local, visitant = noms
            bloc = fila.parent
            gols = [t for t in (s.get_text(strip=True) for s in bloc.select('span[class*="text-4xl"]')) if t.isdigit()]
            estat = bloc.find(string=re.compile(r"FINALITZAT|EN JOC|DESCANS", re.I))
            if len(gols) >= 2 and all(g.isdigit() for g in gols[:2]) and estat:
                resultat = f"{gols[0]}-{gols[1]}"
            break
    if not local or not visitant:
        log.warning("No s'han trobat els equips a %s", url)
        return None

    m_acta = re.search(r'codacta\\?"\s*:\s*\\?"(\d+)', html)
    id_acta = m_acta.group(1) if m_acta else (m_url.group("slug") if m_url else urlparse(url).path)

    return PartitBrut(
        id_acta=id_acta, dia=dia, hora=hora,
        camp_nom=bonic(camp_nom), camp_municipi="", camp_fitxa=camp_fitxa,
        categoria=cat, local=local, visitant=visitant,
        url_fcf=url, resultat=resultat,
    )


# ---------- Camp ----------

def parse_camp(html: str, fitxa: str = "") -> dict | None:
    dades = objecte_json(flight(html), "initialData")
    if not isinstance(dades, dict) or "nombre" not in dades:
        return None
    try:
        lat, lon = float(dades.get("latitud")), float(dades.get("longitud"))
    except (TypeError, ValueError):
        lat = lon = None
    if lat is not None and not (40.4 <= lat <= 42.95 and 0.1 <= lon <= 3.4):  # fora de Catalunya
        lat = lon = None
    slug = dades.get("slug") or fitxa.rsplit("/", 1)[-1]
    return {
        "id": "fcf_" + slug.replace("-", "_"),
        "fitxa": fitxa or f"/ca/camps/{slug}",
        "codi": dades.get("codigo_campo"),
        "nom": bonic(dades["nombre"]),
        "municipi": (dades.get("localidad") or "").strip(),
        "lat": lat,
        "lon": lon,
        "alias": [],
    }


# ---------- Grup ----------

def parse_grup(html: str) -> dict | None:
    f = flight(html)
    info = objecte_json(f, "initialResolvedData")
    if not isinstance(info, dict):
        return None
    jornada = objecte_json(f, "initialJornada")
    return {**info, "jornada": jornada if isinstance(jornada, int) else None}


# ---------- Sitemap ----------

def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """Retorna (urls de pàgines, urls de sub-sitemaps)."""
    soup = BeautifulSoup(xml, "xml")
    subs = [l.get_text(strip=True) for s in soup.find_all("sitemap") for l in s.find_all("loc")]
    urls = [l.get_text(strip=True) for u in soup.find_all("url") for l in u.find_all("loc")]
    return urls, subs


def recull_sitemap(client: ClientEducat, arrel: str, maxim: int = 200, segueix=None) -> Iterator[str]:
    """URL de pàgines d'un sitemap (o índex). `segueix(url)` filtra quins sub-sitemaps es baixen."""
    pendents, vistos = [arrel], set()
    while pendents and len(vistos) < maxim:
        sm = pendents.pop(0)
        if sm in vistos:
            continue
        vistos.add(sm)
        try:
            urls, subs = parse_sitemap(client.get(sm))
        except Prohibit:
            raise
        except Blocat as e:
            log.warning("%s", e)
            continue
        pendents.extend(u for u in subs if segueix is None or segueix(u))
        yield from urls


# ---------- Orquestració ----------

def temporada_actual(avui: date) -> str:
    a = avui.year if avui.month >= 7 else avui.year - 1
    return f"{a}-{a + 1}"


RE_GRUP = re.compile(r"^/ca/competicio/(?P<temp>\d{4}-\d{4})/(?P<comp>[^/]+)/(?P<grup>[^/]+)/resultats/?$")
SITEMAP_GRUPS = "/sitemap/competicions.xml"
SITEMAP_ARREL = "/sitemap.xml"
RE_EQUIP = re.compile(r"^/ca/clubs/[^/]+/categories/[^/]+/?$")
RE_SITEMAP_EQUIPS = re.compile(r"/sitemap/equips(-\d+)?\.xml$")


def equips_del_sitemap(client: ClientEducat) -> list[str]:
    """Camins de totes les fitxes d'equip (/sitemap/equips-N.xml, via l'índex arrel)."""
    camins = []
    for u in recull_sitemap(client, SITEMAP_ARREL, segueix=lambda x: bool(RE_SITEMAP_EQUIPS.search(x))):
        cami = urlparse(u).path.rstrip("/")
        if RE_EQUIP.match(cami):
            camins.append(cami)
    return list(dict.fromkeys(camins))


def grups_del_sitemap(client: ClientEducat, temp: str, filtre: str | None = None) -> list[tuple[str, str]]:
    """Tots els grups de la temporada (futbol, futbol sala, platja, walking…)."""
    re_filtre = re.compile(filtre) if filtre else None
    grups = set()
    for u in recull_sitemap(client, SITEMAP_GRUPS):
        m = RE_GRUP.match(urlparse(u).path)
        if not m or m.group("temp") != temp:
            continue
        if re_filtre and not re_filtre.search(m.group("comp")):
            continue
        grups.add((m.group("comp"), m.group("grup")))
    return sorted(grups)


# ---------- Fitxa d'equip ----------

def clau_compacta(text: str) -> str:
    """Només lletres i xifres, sense accents: 'COL·LEGI X', 'col-legi-x' i 'collegi x' -> 'collegix'."""
    s = "".join(c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


SITEMAP_CAMPS = "/sitemap/camps.xml"
def camps_del_sitemap(client: ClientEducat) -> dict[str, str]:
    """{clau_compacta(slug): camí de la fitxa} de tots els camps reals.

    Inclou /ca/camps/, /ca/camps-futbol-sala/ i /ca/camps-futbol-platja/. Si una clau
    es repeteix entre seccions, guanya /ca/camps/ (no n'hi havia cap el nov. 2026).
    """
    camins = []
    for u in recull_sitemap(client, SITEMAP_CAMPS):
        f = _fitxa_camp(u)
        if f:
            camins.append(f)
    res: dict[str, str] = {}
    for f in sorted(set(camins), key=lambda x: (not x.startswith("/ca/camps/"), x)):
        res.setdefault(clau_compacta(f.rsplit("/", 1)[-1]), f)
    return res


def cami_equip(club_slug: str, equip_slug: str) -> str:
    return f"/ca/clubs/{club_slug}/categories/{equip_slug}"


def _nom_equip(nom: str) -> str:
    """'FUNDACIO P.E. EUROPA "B"' -> "FUNDACIO P.E. EUROPA 'B'"."""
    return re.sub(r'"([^"]*)"', r"'\1'", (nom or "").strip())


def _float(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v else None


def parse_equip(html: str) -> tuple[list[PartitBrut], set[str], dict[str, str]]:
    """Partits de la temporada d'un equip (array `matches` del payload RSC).

    Retorna (partits, camins de fitxes d'equip implicats, {camí_equip: "comp/grup"}).
    La pàgina també porta plantilla i staff: no es llegeixen.
    """
    matches = objecte_json(flight(html), "matches")
    if not isinstance(matches, list):
        return [], set(), {}
    partits, equips, grup_de = [], set(), {}
    for m in matches:
        if not isinstance(m, dict):
            continue
        grup = f"{m.get('COMPETICIO_URL')}/{m.get('GRUPO_URL')}"
        for costat in ("CASA", "FUERA"):
            if m.get(f"CLUB_SLUG_{costat}") and m.get(f"SLUG_{costat}"):
                c = cami_equip(m[f"CLUB_SLUG_{costat}"], m[f"SLUG_{costat}"])
                equips.add(c)
                grup_de[c] = grup
        comenca = m.get("COMIENZO1") or ""
        mt = re.match(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", comenca)
        if not mt or not m.get("CODACTA") or not m.get("MATCH_URL"):
            continue
        gc, gf = m.get("GOLES_CASA"), m.get("GOLES_FUERA")
        resultat = f"{gc}-{gf}" if gc not in (None, "") and gf not in (None, "") else None
        camp = (m.get("CAMPO") or "").strip()
        partits.append(PartitBrut(
            id_acta=str(m["CODACTA"]),
            dia=mt.group(1), hora=mt.group(2),
            camp_nom=bonic(camp), camp_municipi="",
            camp_nom_fcf=camp or None,
            camp_codi=str(m["CODIGO_CAMPO"]) if m.get("CODIGO_CAMPO") else None,
            lat=_float(m.get("LATITUD")), lon=_float(m.get("LONGITUD")),
            categoria=" - ".join(x for x in (m.get("NOMBRE_COMPETICION"), m.get("GRUPO")) if x),
            local=_nom_equip(m.get("NOMBRE_CASA")), visitant=_nom_equip(m.get("NOMBRE_FUERA")),
            url_fcf="https://www.fcf.cat/ca" + m["MATCH_URL"],
            resultat=resultat,
        ))
    return partits, equips, grup_de


def _equips_del_partit(p: PartitBrut) -> tuple[str, str] | None:
    m = RE_PARTIT.match(urlparse(p.url_fcf).path)
    if not m or "-vs-" not in m.group("slug"):
        return None
    a, b = m.group("slug").split("-vs-", 1)
    return a, b


# ---------- Orquestració ----------

def part_de(clau: str, n: int) -> int:
    """Part estable (1..n) d'un grup o equip: sempre la mateixa entre execucions."""
    return int(hashlib.sha1(clau.encode()).hexdigest(), 16) % n + 1


def recull_partits(
    client: ClientEducat,
    inici: date,
    fi: date,
    filtre: str | None = None,
    entrades: dict[str, list[str]] | None = None,
    coneguts: dict[str, list[str]] | None = None,
    part: tuple[int, int] | None = None,
    temps_max_s: float | None = None,
    informe: dict | None = None,
    rellotge=time.monotonic,
) -> Iterable[PartitBrut]:
    """Recorre fitxes d'equip fins cobrir tots els equips de cada grup.

    Entrades: els equips de `entrades` ({"comp/grup": [camí_equip, …]}) i, si no hi ha
    filtre, totes les fitxes del sitemap d'equips (així s'arriba a grups nous).
    `coneguts`: es completa in situ amb tots els equips vistos (per persistir-ho).
    `part`: (i, n) només processa els grups de la part i de n (rotació entre nits).
    `temps_max_s`: s'atura net quan s'esgota (els partits ja trobats són vàlids).
    `informe`: s'hi deixen `equips_visitats` (slugs), `fitxes`, `tallat`.

    Cada fitxa porta tota la temporada de l'equip; un equip es considera cobert
    quan ja tenim el seu partit del cap de setmana per la fitxa del rival.
    """
    temp = temporada_actual(inici)
    re_filtre = re.compile(filtre) if filtre else None
    entrades = entrades or {}
    coneguts = coneguts if coneguts is not None else {}
    informe = informe if informe is not None else {}
    t0 = rellotge()

    grup_equip: dict[str, str] = {c: g for g, cc in entrades.items() for c in cc}

    def vol(grup: str) -> bool:
        return not re_filtre or bool(re_filtre.search(grup.split("/")[0]))

    def de_la_part(cami: str) -> bool:
        return part is None or part_de(grup_equip.get(cami, cami), part[1]) == part[0]

    try:
        grups = {f"{c}/{g}" for c, g in grups_del_sitemap(client, temp, filtre)}
        if part:
            grups = {g for g in grups if part_de(g, part[1]) == part[0]}
    except Prohibit:
        raise
    except Exception as e:
        log.warning("No s'ha pogut llegir el sitemap de grups: %s", e)
        grups = set()

    pendents: list[str] = []
    # Tots els equips coneguts a la cua: si una fitxa falla, el grup s'assoleix per una altra.
    # Els equips ja coberts per la fitxa d'un rival se salten sense fer cap petició.
    for grup, camins in sorted(entrades.items()):
        if vol(grup):
            pendents.extend(camins)
    if not re_filtre:
        # Amb filtre no se sap a quin grup pertany cada equip del sitemap: només coneguts.
        try:
            del_sitemap = equips_del_sitemap(client)
            log.info("%d fitxes d'equip al sitemap", len(del_sitemap))
            pendents.extend(del_sitemap)
        except Blocat:
            raise
        except Exception as e:
            log.warning("No s'ha pogut llegir el sitemap d'equips: %s", e)
    pendents = deque(c for c in pendents if de_la_part(c))
    if not pendents:
        raise RuntimeError("No hi ha cap equip d'entrada (ni coneguts ni al sitemap d'equips)")

    visitats: set[str] = set()
    coberts: set[str] = set()      # slugs d'equip amb el partit del cap de setmana ja trobat
    vistos: set[str] = set()
    grups_assolits: set[str] = set()
    informe.update(equips_visitats=set(), fitxes=0, tallat=False)

    while pendents:
        if temps_max_s is not None and rellotge() - t0 > temps_max_s:
            informe["tallat"] = True
            log.warning("Temps esgotat: queden %d equips a la cua", len(pendents))
            break
        cami = pendents.popleft()
        slug_equip = cami.rsplit("/", 1)[-1]
        if cami in visitats or slug_equip in coberts:
            continue
        visitats.add(cami)
        try:
            partits, equips, grup_de = parse_equip(client.get(cami))
        except Blocat:
            raise
        except Exception as e:
            log.warning("Equip %s: %s", cami, e)
            continue
        informe["fitxes"] += 1
        informe["equips_visitats"].add(slug_equip)

        # Els rivals van al CAP de la cua: es completa el grup abans de saltar a un altre,
        # així una execució tallada per temps deixa grups sencers.
        rivals = []
        for c, g in grup_de.items():
            grup_equip[c] = g
            if c not in coneguts.setdefault(g, []):
                coneguts[g].append(c)
            if vol(g):
                if part is None or part_de(g, part[1]) == part[0]:
                    grups_assolits.add(g)
                    if c not in visitats:
                        rivals.append(c)
        pendents.extendleft(reversed(sorted(rivals)))

        for p in partits:
            if not (inici.isoformat() <= p.dia <= fi.isoformat()):
                continue
            m_url = RE_PARTIT.match(urlparse(p.url_fcf).path)
            if re_filtre and m_url and not re_filtre.search(m_url.group("comp")):
                continue
            eq = _equips_del_partit(p)
            if eq:
                coberts.update(eq)
            if p.id_acta not in vistos:
                vistos.add(p.id_acta)
                yield p
        coberts.add(slug_equip)

    if grups:
        falten = grups - grups_assolits
        log.info("Grups coberts: %d de %d", len(grups & grups_assolits), len(grups))
        if falten and not informe["tallat"]:
            log.warning("%d grups sense cap equip d'entrada (p. ex. %s)", len(falten), sorted(falten)[:3])
    log.info("%d fitxes d'equip visitades", len(visitats))


def recull_camp(client: ClientEducat, fitxa: str) -> dict | None:
    return parse_camp(client.get(fitxa), fitxa)
