#!/usr/bin/env python3
"""Genera public/data/partits_setmana.json amb els partits del cap de setmana.

Ús:
    python scraper/fetch_fcf.py                 # setmana actual, escriu el JSON
    python scraper/fetch_fcf.py --dry-run       # no escriu res
    python scraper/fetch_fcf.py --força         # ignora la validació del 80%
    python scraper/fetch_fcf.py --part 2/3      # rotació: només 1/3 dels grups
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from model import PartitBrut

ARREL = Path(__file__).resolve().parent.parent
SORTIDA = ARREL / "public" / "data" / "partits_setmana.json"
COORDS = ARREL / "scraper" / "camps_coords.json"
EQUIPS = ARREL / "scraper" / "equips_coneguts.json"
PENDENTS = ARREL / "scraper" / "camps_pendents.json"
LLINDAR_INTEGRITAT = 0.8

log = logging.getLogger("fetch_fcf")

# ---------- Normalització de text ----------

PARAULES_BUIDES = {
    "de", "del", "dels", "d", "la", "el", "els", "les", "l", "i", "y",
    "camp", "camps", "municipal", "mpal", "mun", "futbol", "cmf", "cm", "zona", "esportiva",
    "complex", "poliesportiu", "instal", "lacio", "lacions", "estadi", "nou", "nova",
}


def sense_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", sense_accents(s).lower())


def slug(s: str) -> str:
    return "_".join(tokens(s))


def clau_camp(nom: str, municipi: str) -> str:
    """Clau tolerant: ignora accents, puntuació, ordre i paraules genèriques."""
    # Les lletres soltes ("C.M.F.", "l'") no aporten res
    t = sorted({x for x in tokens(nom) if len(x) > 1 or x.isdigit()} - PARAULES_BUIDES)
    return f"{slug(municipi)}|{' '.join(t) or slug(nom)}"


RE_SUBCAMP = re.compile(
    r"\s*(?:[-–,(]\s*)?"
    r"(?P<sub>(?:camp|pista|terreny|sector)\s*(?:\d+|[ab])"
    r"|f[-\s]?7\s*[-\s]?[ab12]"
    r"|meitat\s+(?:nord|sud|est|oest)"
    r"|annex)\)?\s*$",
    re.IGNORECASE,
)


def separa_subcamp(nom: str) -> tuple[str, str | None]:
    """'Camp Municipal X - Camp 2' -> ('Camp Municipal X', 'Camp 2')."""
    m = RE_SUBCAMP.search(nom)
    if not m or m.start() == 0:
        return nom.strip(), None
    sub = m.group("sub").strip()
    sub = re.sub(r"\s+", " ", sub)
    return nom[: m.start()].strip(" -–,"), sub[0].upper() + sub[1:]


# ---------- Categoria -> modalitat i durada ----------

# Durades de joc aproximades (sense descans). Ajustar si l'FCF les publica.
CATEGORIES = [
    ("debutant", "F7", 40),
    ("prebenjami", "F7", 50),
    ("benjami", "F7", 50),
    ("alevi", "F7", 60),
    ("infantil", "F11", 70),
    ("cadet", "F11", 80),
    ("juvenil", "F11", 90),
]


def info_categoria(categoria: str) -> tuple[str, int]:
    c = sense_accents(categoria).lower()
    for clau, modalitat, durada in CATEGORIES:
        if clau in c:
            if "f11" in c or "futbol 11" in c:
                modalitat = "F11"
            return modalitat, durada
    return ("F7" if "f7" in c or "futbol 7" in c else "F11"), 90


# ---------- Finestra temporal ----------

def finestra_cap_de_setmana(avui: date) -> tuple[date, date]:
    """De divendres a diumenge de la setmana ISO d'`avui`."""
    dilluns = avui - timedelta(days=avui.weekday())
    return dilluns + timedelta(days=4), dilluns + timedelta(days=6)


# ---------- Diccionari mestre de camps ----------

class IndexCamps:
    def __init__(self, camps: list[dict], nota: str = ""):
        self.camps: list[dict] = []
        self.nota = nota
        self._per_clau: dict[str, dict] = {}
        self._per_fitxa: dict[str, dict] = {}
        self._per_codi: dict[str, dict] = {}
        for c in camps:
            self.afegeix(c)

    def afegeix(self, c: dict) -> None:
        """Afegeix o reindexa (si ja hi és, no es duplica)."""
        if not any(x is c for x in self.camps):
            self.camps.append(c)
        if c.get("fitxa"):
            self._per_fitxa[c["fitxa"]] = c
        if c.get("codi"):
            self._per_codi[str(c["codi"])] = c
        for nom in [c["nom"], *c.get("alias", [])]:
            self._per_clau.setdefault(clau_camp(nom, c.get("municipi", "")), c)

    def per_fitxa(self, fitxa: str | None) -> dict | None:
        return self._per_fitxa.get(fitxa) if fitxa else None

    def per_codi(self, codi: str | None) -> dict | None:
        return self._per_codi.get(str(codi)) if codi else None

    def troba(self, p: PartitBrut) -> dict | None:
        return self.per_codi(p.camp_codi) or self.per_fitxa(p.camp_fitxa)

    @classmethod
    def carrega(cls, path: Path) -> "IndexCamps":
        if not path.exists():
            return cls([])
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(d.get("camps", []), d.get("_nota", ""))

    def desa(self, path: Path) -> None:
        camps = sorted(self.camps, key=lambda c: (c.get("municipi", ""), c["nom"]))
        escriu_atomic(path, {"_nota": self.nota, "camps": camps}, indent=2)

    def busca(self, nom: str, municipi: str) -> dict | None:
        return self._per_clau.get(clau_camp(nom, municipi))


# ---------- Construcció del JSON ----------

def construeix(
    partits: Iterable[PartitBrut],
    index: IndexCamps,
    inici: date,
    fi: date,
    ara: datetime,
) -> tuple[dict, list[dict]]:
    camps: dict[str, dict] = {}
    pendents: dict[str, dict] = {}
    vistos: set[str] = set()

    for p in partits:
        if not p.id_acta or p.id_acta in vistos:
            continue
        try:
            dia = date.fromisoformat(p.dia)
        except ValueError:
            log.warning("Data invàlida a l'acta %s: %r", p.id_acta, p.dia)
            continue
        if not (inici <= dia <= fi):
            continue
        vistos.add(p.id_acta)

        nom_base, sub_nom = separa_subcamp(p.camp_nom)
        entrada = index.troba(p) or index.busca(nom_base, p.camp_municipi)
        municipi = (entrada or {}).get("municipi") or p.camp_municipi
        lat = (entrada or {}).get("lat") or p.lat
        lon = (entrada or {}).get("lon") or p.lon
        if entrada:
            nom_base, sub_entrada = separa_subcamp(entrada["nom"])
            sub_nom = sub_nom or sub_entrada
        # Els subcamps (Camp 1 / Camp 2) comparteixen recinte: s'agrupen pel nom base.
        cid = "fcf_" + slug(nom_base)
        camp = camps.setdefault(cid, {
            "id": cid, "nom": nom_base, "municipi": municipi, "lat": lat, "lon": lon, "partits": [],
        })
        if camp["lat"] is None and lat is not None:
            camp["lat"], camp["lon"] = lat, lon
        if not camp["municipi"] and municipi:
            camp["municipi"] = municipi
        if lat is None or not municipi:
            pendents.setdefault(cid, {"id": cid, "fitxa": p.camp_fitxa, "codi": p.camp_codi, "nom": nom_base,
                                      "municipi": municipi, "coordenades": lat is not None, "partits": 0})
            pendents[cid]["partits"] += 1

        modalitat, durada = info_categoria(p.categoria)
        camp["partits"].append({
            "id_acta": p.id_acta,
            "dia": p.dia,
            "hora": p.hora,
            "durada_min": p.durada_min or durada,
            "modalitat": p.modalitat or modalitat,
            "subcamp": p.subcamp or sub_nom,
            "categoria": p.categoria,
            "local": p.local,
            "visitant": p.visitant,
            "url_fcf": p.url_fcf,
            "resultat_cache": p.resultat,
        })

    for c in camps.values():
        c["partits"].sort(key=lambda x: (x["dia"], x["hora"], x["subcamp"] or ""))

    iso = inici.isocalendar()
    dades = {
        "actualitzat": ara.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "setmana": f"{iso[0]}-W{iso[1]:02d}",
        "total_partits": len(vistos),
        "camps": sorted(camps.values(), key=lambda c: c["id"]),
    }
    return dades, sorted(pendents.values(), key=lambda c: -c["partits"])


# ---------- Validació i escriptura ----------

class IntegritatError(Exception):
    pass


def total_anterior(path: Path) -> int | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return d.get("total_partits") or sum(len(c.get("partits", [])) for c in d.get("camps", []))


def valida(nou: int, anterior: int | None, llindar: float = LLINDAR_INTEGRITAT) -> None:
    if nou == 0:
        raise IntegritatError("El scraper no ha retornat cap partit")
    if anterior and nou < anterior * llindar:
        raise IntegritatError(
            f"Només {nou} partits davant de {anterior} anteriors (< {llindar:.0%}); "
            "possible canvi d'HTML a l'FCF"
        )


def escriu_atomic(path: Path, dades: dict | list, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(dades, f, ensure_ascii=False, indent=indent,
                      separators=None if indent else (",", ":"))
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def completa_camps(client, index: IndexCamps, partits: list[PartitBrut], recull_camp,
                   slugs_camps=None, temps_max_s: float | None = None, rellotge=time.monotonic) -> int:
    """Afegeix a l'índex cada camp nou i intenta saber-ne el municipi.

    El municipi surt de la fitxa del camp. El camí no s'endevina mai: o ve de l'acta,
    o es busca al sitemap de camps (`slugs_camps()` -> {clau_compacta: camí}).
    Sense camí real no es fa cap petició; el camp queda amb coordenades i sense municipi.
    Una fitxa que falla es marca (`fitxa_fallida`) i no es reintenta.
    Amb `temps_max_s`, s'atura quan s'esgota: els camps pendents es fan la nit següent.
    Retorna quants camps nous s'han afegit.
    """
    import fcf_parser
    mapa: dict[str, str] | None = None

    def fitxa_real(nom_fcf: str | None) -> str | None:
        nonlocal mapa
        if not nom_fcf or slugs_camps is None:
            return None
        if mapa is None:
            try:
                mapa = slugs_camps(client)
                log.info("%d camps al sitemap de camps", len(mapa))
            except Exception as e:
                log.warning("No s'ha pogut llegir el sitemap de camps: %s", e)
                mapa = {}
        return mapa.get(fcf_parser.clau_compacta(nom_fcf))

    nous = 0
    vistos: set[str] = set()
    t0 = rellotge()
    sense_temps = False
    for p in partits:
        clau = p.camp_codi or p.camp_fitxa or p.camp_nom_fcf
        if not clau or clau in vistos:
            continue
        vistos.add(clau)
        entrada = index.troba(p)
        if entrada is None:
            entrada = {"id": "fcf_" + slug(p.camp_nom), "fitxa": p.camp_fitxa, "codi": p.camp_codi,
                       "nom": p.camp_nom, "nom_fcf": p.camp_nom_fcf, "municipi": "",
                       "lat": p.lat, "lon": p.lon, "alias": []}
            index.afegeix(entrada)
            nous += 1
        if entrada.get("municipi") or entrada.get("fitxa_fallida") or sense_temps:
            continue
        if temps_max_s is not None and rellotge() - t0 > temps_max_s:
            sense_temps = True
            log.warning("Temps esgotat: la resta de fitxes de camp es farà la propera execució")
            continue
        s = entrada.get("fitxa") or p.camp_fitxa or fitxa_real(entrada.get("nom_fcf") or p.camp_nom_fcf)
        if not s:
            continue
        try:
            fitxa = recull_camp(client, s)
        except Exception as e:
            log.warning("No s'ha pogut llegir el camp %s: %s", s, e)
            fitxa = None
        if not fitxa:
            entrada["fitxa_fallida"] = True
            continue
        entrada["fitxa"] = s                                  # el camí que realment ha respost
        entrada["codi"] = entrada.get("codi") or fitxa.get("codi")
        entrada["municipi"] = fitxa.get("municipi") or ""
        entrada["lat"] = entrada.get("lat") or fitxa.get("lat")
        entrada["lon"] = entrada.get("lon") or fitxa.get("lon")
        index.afegeix(entrada)   # reindexa per slug/codi
    return nous


def carrega_equips(path: Path) -> dict[str, list[str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("grups", {})
    except (OSError, json.JSONDecodeError):
        return {}


# ---------- CLI ----------

def llegeix_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def partits_de_sortida(dades: dict) -> list[PartitBrut]:
    """Reconstrueix els partits d'un partits_setmana.json anterior (per fusionar-hi una part)."""
    res = []
    for c in dades.get("camps", []):
        for x in c.get("partits", []):
            res.append(PartitBrut(
                id_acta=x["id_acta"], dia=x["dia"], hora=x["hora"],
                camp_nom=c["nom"], camp_municipi=c.get("municipi", ""),
                categoria=x.get("categoria", ""), local=x.get("local", ""), visitant=x.get("visitant", ""),
                url_fcf=x.get("url_fcf", ""), subcamp=x.get("subcamp"), modalitat=x.get("modalitat"),
                durada_min=x.get("durada_min"), resultat=x.get("resultat_cache"),
                lat=c.get("lat"), lon=c.get("lon"),
            ))
    return res


def fusiona(anteriors: list[PartitBrut], nous: list[PartitBrut], equips_visitats: set[str]) -> list[PartitBrut]:
    """Els partits nous substitueixen els anteriors.

    Dels equips visitats en aquesta execució, la fitxa és la veritat: els seus partits
    anteriors que ja no hi surten (ajornats, canviats de dia) desapareixen.
    """
    import fcf_parser
    ids_nous = {p.id_acta for p in nous}
    queden = []
    for p in anteriors:
        if p.id_acta in ids_nous:
            continue
        eq = fcf_parser._equips_del_partit(p)
        if eq and (eq[0] in equips_visitats or eq[1] in equips_visitats):
            continue
        queden.append(p)
    return queden + list(nous)


def parseja_part(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    m = re.fullmatch(r"(\d+)/(\d+)", text)
    if not m or not (1 <= int(m.group(1)) <= int(m.group(2))):
        raise argparse.ArgumentTypeError("format: i/n, p. ex. 2/3")
    i, n = int(m.group(1)), int(m.group(2))
    return None if n == 1 else (i, n)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="no escriu cap fitxer")
    ap.add_argument("--força", "--forca", dest="forca", action="store_true", help="ignora la validació del 80%%")
    ap.add_argument("--data", type=date.fromisoformat, help="simula el dia d'execució (YYYY-MM-DD)")
    ap.add_argument("--part", type=parseja_part, help="rotació: només la part i de n dels grups (ex. 2/3)")
    ap.add_argument("--temps-max", type=float, default=320, help="minuts màxims de rastreig (defecte 320)")
    ap.add_argument("--filtre", help="regex sobre l'slug de competició (ex. 'benjami|alevi')")
    ap.add_argument("--sortida", type=Path, default=SORTIDA)
    ap.add_argument("--coords", type=Path, default=COORDS)
    ap.add_argument("--equips", type=Path, default=EQUIPS)
    ap.add_argument("--pendents", type=Path, default=PENDENTS)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from http_client import ClientEducat
    import fcf_parser

    ara = datetime.now(timezone.utc)
    inici, fi = finestra_cap_de_setmana(args.data or ara.date())
    setmana = "%d-W%02d" % inici.isocalendar()[:2]
    log.info("Recollint partits del %s al %s%s", inici, fi,
             f" (part {args.part[0]}/{args.part[1]})" if args.part else "")

    client = ClientEducat()
    informe: dict = {}
    t0 = time.monotonic()
    temps_total = args.temps_max * 60
    try:
        coneguts = carrega_equips(args.equips)
        nous = list(fcf_parser.recull_partits(
            client, inici, fi, filtre=args.filtre,
            entrades={g: list(c) for g, c in coneguts.items()}, coneguts=coneguts,
            # 90% per al rastreig de partits; la resta, per a fitxes de camp noves
            part=args.part, temps_max_s=temps_total * 0.9, informe=informe,
        ))
    except Exception:
        log.exception("El scraper ha fallat; es manté el JSON anterior")
        return 1
    log.info("%d partits en %d fitxes (%d peticions)%s", len(nous), informe.get("fitxes", 0),
             client.peticions, " — TALLAT per temps" if informe.get("tallat") else "")

    if informe.get("fitxes", 0) >= 20 and not nous:
        log.error("%d fitxes llegides i cap partit: possible canvi d'HTML. No s'escriu res.", informe["fitxes"])
        return 2
    if not args.dry_run:
        escriu_atomic(args.equips, {"_nota": "Equips per grup (llavor del recorregut). Es manté sol.",
                                    "grups": {g: sorted(c) for g, c in sorted(coneguts.items())}}, indent=1)

    # Fusió amb l'execució anterior de la mateixa setmana (rotació per parts)
    anterior = llegeix_json(args.sortida)
    mateixa_setmana = bool(anterior) and anterior.get("setmana") == setmana
    brut = fusiona(partits_de_sortida(anterior), nous, informe.get("equips_visitats", set())) \
        if mateixa_setmana else nous

    index = IndexCamps.carrega(args.coords)
    n_camps = completa_camps(client, index, nous, fcf_parser.recull_camp, fcf_parser.camps_del_sitemap,
                             temps_max_s=max(60.0, temps_total - (time.monotonic() - t0)))
    if not args.dry_run:
        index.desa(args.coords)
        if n_camps:
            log.info("%d camps nous afegits a %s", n_camps, args.coords.name)

    dades, pendents = construeix(brut, index, inici, fi, ara)
    try:
        # Una setmana nova comença amb una sola part: només es compara dins la mateixa setmana.
        valida(dades["total_partits"], total_anterior(args.sortida) if mateixa_setmana else None)
    except IntegritatError as e:
        if not args.forca:
            log.error("%s. No s'escriu res (usa --força si és legítim, p. ex. vacances).", e)
            return 2
        log.warning("%s (ignorat per --força)", e)

    log.info("%d partits a %d camps (%d camps incomplets); %d peticions en total",
             dades["total_partits"], len(dades["camps"]), len(pendents), client.peticions)
    if args.dry_run:
        return 0
    escriu_atomic(args.sortida, dades)
    escriu_atomic(args.pendents, pendents)
    log.info("Escrit %s", args.sortida)
    return 0


if __name__ == "__main__":
    sys.exit(main())
