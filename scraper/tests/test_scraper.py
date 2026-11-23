"""Tests sense xarxa. Executar: python -m unittest discover -s scraper/tests"""
import json
import re
import sys
import tempfile
import unittest
from unittest import mock
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

import fetch_fcf as f  # noqa: E402
from http_client import Blocat, ClientEducat, Prohibit, Robots  # noqa: E402
from model import PartitBrut  # noqa: E402

ROBOTS = "User-Agent: *\nAllow: /\nDisallow: /api/\nDisallow: /ca/cerca\n\nUser-Agent: ClaudeBot\nDisallow: /\n"


class RespostaFalsa:
    def __init__(self, status=200, text="", headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class SessioFalsa:
    def __init__(self, respostes):
        self.respostes = respostes  # dict url -> llista de RespostaFalsa
        self.headers = {}
        self.demanades = []

    def get(self, url, timeout=None):
        self.demanades.append(url)
        cua = self.respostes[url]
        return cua.pop(0) if len(cua) > 1 else cua[0]


def client(respostes, **kw):
    respostes.setdefault("https://www.fcf.cat/robots.txt", [RespostaFalsa(text=ROBOTS)])
    esperes = []
    c = ClientEducat(sessio=SessioFalsa(respostes), dorm=esperes.append, **kw)
    return c, esperes


class TestClient(unittest.TestCase):
    def test_user_agent_honest(self):
        c, _ = client({})
        self.assertTrue(c.sessio.headers["User-Agent"].startswith("QuiJugaBot/"))

    def test_api_prohibida(self):
        c, _ = client({})
        with self.assertRaises(Blocat):
            c.get("/api/partits")

    def test_robots_respectat(self):
        c, _ = client({})
        with self.assertRaises(Blocat):
            c.get("/ca/cerca?q=x")

    def test_altres_dominis_prohibits(self):
        c, _ = client({})
        with self.assertRaises(Blocat):
            c.get("https://exemple.com/")

    def test_pausa_entre_peticions(self):
        url = "https://www.fcf.cat/ca/a"
        c, esperes = client({url: [RespostaFalsa(text="ok")]})
        c.get(url)
        c.get(url)
        self.assertEqual(len(esperes), 1)
        self.assertTrue(2.5 <= esperes[0] <= 5.5)

    def test_backoff_429_amb_retry_after(self):
        url = "https://www.fcf.cat/ca/a"
        c, esperes = client({url: [RespostaFalsa(429, headers={"Retry-After": "42"}), RespostaFalsa(text="ok")]})
        self.assertEqual(c.get(url), "ok")
        self.assertIn(42.0, esperes)

    def test_esgota_intents(self):
        url = "https://www.fcf.cat/ca/a"
        c, _ = client({url: [RespostaFalsa(503)]}, max_intents=3)
        with self.assertRaises(requests.HTTPError):
            c.get(url)

    def test_403_atura_tot_sense_reintentar(self):
        url = "https://www.fcf.cat/ca/a"
        c, esperes = client({url: [RespostaFalsa(403)]})
        with self.assertRaises(Prohibit):
            c.get(url)
        self.assertEqual(c.sessio.demanades.count(url), 1)
        self.assertEqual(esperes, [])       # ni pauses ni reintents

    def test_403_atura_el_recorregut(self):
        r = TestRecorregut()
        r.setUp()
        sessio = SessioFalsa({"https://www.fcf.cat/robots.txt": [RespostaFalsa(text=ROBOTS)],
                              **{f"https://www.fcf.cat{k}": [RespostaFalsa(403)] for k in r.pagines}})
        cl = ClientEducat(sessio=sessio, dorm=lambda s: None)
        with self.assertRaises(Prohibit):
            list(fp.recull_partits(cl, date(2026, 11, 27), date(2026, 11, 29), entrades=r.entrades))
        self.assertEqual(cl.peticions, 1)   # el primer 403 (sitemap de grups) ja ho atura tot

    def test_404_no_es_reintenta(self):
        url = "https://www.fcf.cat/ca/a"
        c, _ = client({url: [RespostaFalsa(404)]})
        with self.assertRaises(requests.HTTPError):
            c.get(url)
        self.assertEqual(c.sessio.demanades.count(url), 1)

    def test_limit_peticions(self):
        url = "https://www.fcf.cat/ca/a"
        c, _ = client({url: [RespostaFalsa(text="ok")]}, max_peticions=1)
        c.get(url)
        with self.assertRaises(Blocat):
            c.get(url)

    def test_robots_caigut_no_rastreja(self):
        c, _ = client({"https://www.fcf.cat/robots.txt": [RespostaFalsa(503)]})
        with self.assertRaises(Blocat):
            c.get("/ca/a")


class TestRobots(unittest.TestCase):
    def test_regla_mes_llarga_guanya(self):
        r = Robots(ROBOTS)
        self.assertTrue(r.permes("QuiJugaBot", "https://www.fcf.cat/ca/competicio"))
        self.assertFalse(r.permes("QuiJugaBot", "https://www.fcf.cat/api/x"))
        self.assertFalse(r.permes("QuiJugaBot", "https://www.fcf.cat/ca/cerca?q=1"))

    def test_grup_especific(self):
        r = Robots(ROBOTS)
        self.assertFalse(r.permes("ClaudeBot", "https://www.fcf.cat/ca/competicio"))

    def test_comodins(self):
        r = Robots("User-agent: *\nDisallow: /*.pdf$\nAllow: /docs/*.pdf$\n")
        self.assertFalse(r.permes("x", "https://a.cat/fitxer.pdf"))
        self.assertTrue(r.permes("x", "https://a.cat/fitxer.pdf?v=1"))
        self.assertTrue(r.permes("x", "https://a.cat/docs/fitxer.pdf"))


class TestNormalitzacio(unittest.TestCase):
    def test_clau_tolerant(self):
        self.assertEqual(
            f.clau_camp("Camp Municipal de l'Àliga", "Barcelona"),
            f.clau_camp("C.M.F. L'ALIGA", "BARCELONA"),
        )

    def test_subcamp(self):
        self.assertEqual(f.separa_subcamp("Camp Municipal X - Camp 2"), ("Camp Municipal X", "Camp 2"))
        self.assertEqual(f.separa_subcamp("Camp Municipal X (F7-A)"), ("Camp Municipal X", "F7-A"))
        self.assertEqual(f.separa_subcamp("Camp Municipal X"), ("Camp Municipal X", None))
        self.assertEqual(f.separa_subcamp("Camp 2"), ("Camp 2", None))

    def test_categoria(self):
        self.assertEqual(f.info_categoria("Benjamí Segona Divisió - Grup 14"), ("F7", 50))
        self.assertEqual(f.info_categoria("Juvenil Preferent"), ("F11", 90))
        self.assertEqual(f.info_categoria("Quarta Catalana"), ("F11", 90))

    def test_finestra(self):
        self.assertEqual(f.finestra_cap_de_setmana(date(2026, 11, 24)), (date(2026, 11, 27), date(2026, 11, 29)))
        self.assertEqual(f.finestra_cap_de_setmana(date(2026, 11, 28)), (date(2026, 11, 27), date(2026, 11, 29)))


def partit(id_acta, dia="2026-11-28", camp="Camp Municipal de l'Àliga - Camp 1", municipi="Barcelona", **kw):
    base = dict(id_acta=id_acta, dia=dia, hora="10:30", camp_nom=camp, camp_municipi=municipi,
                categoria="Benjamí Segona Divisió - Grup 14", local="A", visitant="B",
                url_fcf=f"https://www.fcf.cat/acta/{id_acta}")
    base.update(kw)
    return PartitBrut(**base)


class TestConstrueix(unittest.TestCase):
    def setUp(self):
        self.index = f.IndexCamps([{"id": "fcf_aliga", "nom": "Camp Municipal de l'Àliga",
                                    "municipi": "Barcelona", "lat": 41.4, "lon": 2.1}])
        self.ara = datetime(2026, 11, 24, 3, 30, tzinfo=timezone.utc)
        self.inici, self.fi = date(2026, 11, 27), date(2026, 11, 29)

    def fes(self, partits):
        return f.construeix(partits, self.index, self.inici, self.fi, self.ara)

    def test_agrupa_i_separa_subcamps(self):
        dades, pendents = self.fes([partit("1"), partit("2", camp="CMF L'Aliga - Camp 2")])
        self.assertEqual(len(dades["camps"]), 1)
        c = dades["camps"][0]
        self.assertEqual((c["id"], c["lat"]), ("fcf_camp_municipal_de_l_aliga", 41.4))
        self.assertEqual([p["subcamp"] for p in c["partits"]], ["Camp 1", "Camp 2"])
        self.assertEqual(c["partits"][0]["modalitat"], "F7")
        self.assertEqual(pendents, [])

    def test_filtra_fora_finestra_i_duplicats(self):
        dades, _ = self.fes([partit("1"), partit("1"), partit("2", dia="2026-12-05"), partit("3", dia="malament")])
        self.assertEqual(dades["total_partits"], 1)

    def test_camp_desconegut_a_pendents(self):
        dades, pendents = self.fes([partit("1", camp="Camp de Futbol de Can Nou", municipi="Sabadell")])
        self.assertIsNone(dades["camps"][0]["lat"])
        self.assertEqual(pendents[0]["municipi"], "Sabadell")

    def test_metadades(self):
        dades, _ = self.fes([partit("1")])
        self.assertEqual(dades["setmana"], "2026-W48")
        self.assertEqual(dades["actualitzat"], "2026-11-24T03:30:00Z")


def rutes_temporals(d):
    return ["--equips", str(Path(d) / "eq.json"), "--coords", str(Path(d) / "co.json"),
            "--pendents", str(Path(d) / "pe.json")]


class TestValidacio(unittest.TestCase):
    def test_llindar(self):
        f.valida(80, 100)
        f.valida(5, None)
        with self.assertRaises(f.IntegritatError):
            f.valida(79, 100)
        with self.assertRaises(f.IntegritatError):
            f.valida(0, None)

    def test_escriptura_atomica(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            f.escriu_atomic(p, {"camps": [], "total_partits": 3})
            self.assertEqual(json.loads(p.read_text())["total_partits"], 3)
            self.assertEqual(f.total_anterior(p), 3)
            self.assertEqual(list(Path(d).iterdir()), [p])

    def test_main_no_escriu_si_parser_falla(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text('{"total_partits": 7}')
            with mock.patch("fcf_parser.recull_partits", side_effect=RuntimeError("HTML canviat")):
                self.assertEqual(f.main(["--sortida", str(p), *rutes_temporals(d)]), 1)
            self.assertEqual(p.read_text(), '{"total_partits": 7}')

    def test_main_no_escriu_si_massa_pocs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text('{"total_partits": 7, "setmana": "2026-W48"}')
            with mock.patch("fcf_parser.recull_partits", return_value=[partit("1")]):
                self.assertEqual(f.main(["--sortida", str(p), "--data", "2026-11-24", *rutes_temporals(d)]), 2)
            self.assertEqual(p.read_text(), '{"total_partits": 7, "setmana": "2026-W48"}')


if __name__ == "__main__":
    unittest.main()


# ---------- Parser de fcf.cat (fixtures anonimitzades a partir de mostres reals) ----------

import fcf_parser as fp  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
URL_ACTA = ("https://www.fcf.cat/ca/partit/2026-2027/primera-divisio-benjami-s9/grup-9/"
            "jornada-9/horta-uat-a-1b9-vs-fundacio-pe-europa-b-1b9")


def fixture(nom):
    return (FIX / nom).read_text(encoding="utf-8")


class TestParserFcf(unittest.TestCase):
    def test_acta_finalitzada(self):
        p = fp.parse_acta(fixture("acta_finalitzada.html"))
        self.assertEqual((p.id_acta, p.dia, p.hora), ("4188895", "2026-11-22", "09:00"))
        self.assertEqual(p.camp_fitxa, "/ca/camps/camp-de-futbol-mpal-horta")
        self.assertEqual(p.camp_nom, "Camp de Futbol Municipal Horta")
        self.assertEqual(p.categoria, "PRIMERA DIVISIÓ BENJAMÍ S9 - GRUP 9")
        self.assertEqual((p.local, p.visitant), ("HORTA, U.AT. 'A'", "FUNDACIO P.E. EUROPA 'B'"))
        self.assertEqual(p.resultat, "1-3")
        self.assertEqual(p.url_fcf, URL_ACTA)
        self.assertEqual(f.info_categoria(p.categoria)[0], "F7")

    def test_acta_programada_sense_resultat_ni_codacta(self):
        html = fixture("acta_finalitzada.html").replace("FINALITZAT", "")
        html = re.sub(r"<script>self\.__next_f.*?</script>", "", html)
        p = fp.parse_acta(html)
        self.assertIsNone(p.resultat)
        self.assertEqual(p.id_acta, "horta-uat-a-1b9-vs-fundacio-pe-europa-b-1b9")

    def test_acta_sense_data_es_descarta(self):
        self.assertIsNone(fp.parse_acta(fixture("acta_finalitzada.html").replace("DATA:", "")))

    def test_pagina_no_acta(self):
        self.assertIsNone(fp.parse_acta(fixture("camp.html")))

    def test_camp(self):
        c = fp.parse_camp(fixture("camp.html"))
        self.assertEqual(c["id"], "fcf_camp_de_futbol_mpal_horta")
        self.assertEqual((c["codi"], c["municipi"]), ("384", "Barcelona"))
        self.assertAlmostEqual(c["lat"], 41.434667)
        self.assertAlmostEqual(c["lon"], 2.160872)

    def test_grup(self):
        g = fp.parse_grup(fixture("grup.html"))
        self.assertEqual((g["grupId"], g["competicioUrl"], g["jornada"]), (58162224, "primera-divisio-benjami-s9", 10))

    def test_sense_dades_personals(self):
        acta = fixture("acta_finalitzada.html")
        self.assertNotIn("player", acta)
        self.assertNotIn("TITULARS", acta)
        self.assertNotIn("ÀRBITRES", acta)

    def test_bonic(self):
        self.assertEqual(fp.bonic("CAMP DE FUTBOL MPAL. HORTA "), "Camp de Futbol Municipal Horta")
        self.assertEqual(fp.bonic("CAMP MUNICIPAL DE L'ÀLIGA"), "Camp Municipal de l'Àliga")

    def test_lletra_equip(self):
        self.assertEqual(fp._lletra_equip("/ca/clubs/horta-uat/categories/horta-uat-a-1b9"), "A")
        self.assertIsNone(fp._lletra_equip("/ca/clubs/horta-uat/categories/horta-uat-1b9"))

    def test_temporada(self):
        self.assertEqual(fp.temporada_actual(date(2026, 11, 27)), "2026-2027")
        self.assertEqual(fp.temporada_actual(date(2027, 3, 5)), "2026-2027")

    def test_sitemap(self):
        idx = "<sitemapindex><sitemap><loc>https://www.fcf.cat/s1.xml</loc></sitemap></sitemapindex>"
        self.assertEqual(fp.parse_sitemap(idx), ([], ["https://www.fcf.cat/s1.xml"]))
        us = "<urlset><url><loc>https://www.fcf.cat/a</loc></url></urlset>"
        self.assertEqual(fp.parse_sitemap(us), (["https://www.fcf.cat/a"], []))


class ClientFals:
    def __init__(self, pagines):
        self.pagines, self.demanades, self.peticions = pagines, [], 0

    def get(self, url):
        self.demanades.append(url)
        self.peticions += 1
        return self.pagines[url]


def pagina_equip(matches):
    rsc = "x:" + json.dumps({"matches": matches}, ensure_ascii=False)
    return "<html><body><script>self.__next_f.push(" + json.dumps([1, rsc], ensure_ascii=False) + ")</script></body></html>"


def match(acta, casa, fora, dia, hora="10:00", jornada=10, camp="CAMP MUNICIPAL X", codi="1", grup="grup-1",
          comp="lliga-benjami-futbol-sala"):
    return {
        "CODACTA": str(acta), "JORNADA": str(jornada), "COMIENZO1": f"{dia} {hora}:00",
        "NOMBRE_CASA": f'{casa.upper()} "A"', "NOMBRE_FUERA": f'{fora.upper()} "A"',
        "SLUG_CASA": f"{casa}-a", "SLUG_FUERA": f"{fora}-a", "CLUB_SLUG_CASA": casa, "CLUB_SLUG_FUERA": fora,
        "CAMPO": camp, "CODIGO_CAMPO": codi, "LATITUD": "41.4", "LONGITUD": "2.1",
        "GOLES_CASA": None, "GOLES_FUERA": None, "NOMBRE_COMPETICION": "LLIGA BENJAMÍ FUTBOL SALA", "GRUPO": "GRUP 1",
        "COMPETICIO_URL": comp, "GRUPO_URL": grup, "TEMPORADA_SLUG": "2026-2027",
        "MATCH_URL": f"/partit/2026-2027/{comp}/{grup}/jornada-{jornada}/{casa}-a-vs-{fora}-a",
    }


class TestFitxaEquip(unittest.TestCase):
    def test_partits_de_la_mostra(self):
        partits, equips, grup_de = fp.parse_equip(fixture("equip.html"))
        self.assertEqual(len(partits), 30)
        self.assertEqual(len(equips), 16)
        self.assertEqual(set(grup_de.values()), {"primera-divisio-benjami-s9/grup-9"})
        p = next(x for x in partits if x.id_acta == "4188903")
        self.assertEqual((p.dia, p.hora), ("2026-11-28", "14:30"))
        self.assertEqual((p.local, p.visitant), ("FUNDACIO P.E. EUROPA 'B'", "SARRIA, C.P. 'A'"))
        self.assertEqual((p.camp_codi, p.lat, p.lon), ("1717", 41.438155, 2.157475))
        self.assertIsNone(p.resultat)
        self.assertTrue(p.url_fcf.startswith("https://www.fcf.cat/ca/partit/2026-2027/primera-divisio-benjami-s9/"))
        jugat = next(x for x in partits if x.id_acta == "4188831")
        self.assertEqual(jugat.resultat, "4-3")

    def test_fixture_sense_plantilla(self):
        html = fixture("equip.html")
        for clau in ('"players"', '"staff"', '"CARGO"', '"NOMBRE":'):
            self.assertNotIn(clau, html)

    def test_slug_camp(self):
        self.assertEqual(fp.clau_compacta("CAMP DE FUTBOL COL·LEGI ESCALA DEI"), fp.clau_compacta("camp-de-futbol-col-legi-escala-dei"))
        self.assertEqual(fp.clau_compacta("CAMP DE FUTBOL MPAL. L´HOSPITALET"), "campdefutbolmpallhospitalet")

    def test_no_endevina_slugs_de_camp(self):
        partits, _, _ = fp.parse_equip(fixture("equip.html"))
        self.assertTrue(all(p.camp_fitxa is None for p in partits))
        self.assertTrue(all(p.camp_nom_fcf for p in partits))


class TestRecorregut(unittest.TestCase):
    """Grup de 5 equips: A-B i C-D juguen el cap de setmana; E descansa."""

    def setUp(self):
        dia = "2026-11-28"
        anterior = "2026-11-21"
        self.pagines = {
            "/sitemap/competicions.xml": (
                "<urlset><url><loc>https://www.fcf.cat/ca/competicio/2026-2027/lliga-benjami-futbol-sala/grup-1/resultats</loc></url>"
                "<url><loc>https://www.fcf.cat/ca/competicio/2026-2027/walking-futbol/grup-1/resultats</loc></url></urlset>"),
            fp.cami_equip("a", "a-a"): pagina_equip([match(1, "a", "b", dia), match(9, "a", "e", anterior, jornada=9)]),
            fp.cami_equip("b", "b-a"): pagina_equip([match(1, "a", "b", dia)]),
            fp.cami_equip("c", "c-a"): pagina_equip([match(2, "c", "d", dia, codi="2", camp="CAMP MUNICIPAL X - CAMP 2"),
                                                     match(8, "c", "e", anterior, jornada=9)]),
            fp.cami_equip("d", "d-a"): pagina_equip([match(2, "c", "d", dia)]),
            fp.cami_equip("e", "e-a"): pagina_equip([match(9, "a", "e", anterior, jornada=9), match(8, "c", "e", anterior)]),
        }
        self.entrades = {"lliga-benjami-futbol-sala/grup-1": [fp.cami_equip("a", "a-a")]}

    def recorre(self, **kw):
        c = ClientFals(self.pagines)
        coneguts = {}
        partits = list(fp.recull_partits(c, date(2026, 11, 27), date(2026, 11, 29),
                                         entrades=self.entrades, coneguts=coneguts, **kw))
        return c, partits, coneguts

    def test_cobreix_el_grup_amb_minim_de_fitxes(self):
        c, partits, coneguts = self.recorre()
        self.assertEqual(sorted(p.id_acta for p in partits), ["1", "2"])
        fitxes = [u for u in c.demanades if "/clubs/" in u]
        self.assertNotIn(fp.cami_equip("b", "b-a"), fitxes)   # cobert per la fitxa d'A
        self.assertNotIn(fp.cami_equip("d", "d-a"), fitxes)   # cobert per la fitxa de C
        self.assertIn(fp.cami_equip("e", "e-a"), fitxes)      # descansa: cal mirar-ho
        self.assertEqual(len(coneguts["lliga-benjami-futbol-sala/grup-1"]), 5)

    def test_no_descarta_cap_disciplina(self):
        c = ClientFals(self.pagines)
        grups = fp.grups_del_sitemap(c, "2026-2027")
        self.assertEqual(grups, [("lliga-benjami-futbol-sala", "grup-1"), ("walking-futbol", "grup-1")])

    def test_filtre(self):
        with self.assertRaises(RuntimeError):
            self.recorre(filtre="juvenil")

    def afegeix_sitemaps(self, equips):
        self.pagines["/sitemap.xml"] = (
            "<sitemapindex><sitemap><loc>https://www.fcf.cat/sitemap/noticies-1.xml</loc></sitemap>"
            "<sitemap><loc>https://www.fcf.cat/sitemap/equips-1.xml</loc></sitemap></sitemapindex>")
        self.pagines["https://www.fcf.cat/sitemap/equips-1.xml"] = "<urlset>" + "".join(
            f"<url><loc>https://www.fcf.cat{c}</loc></url>" for c in equips) + "</urlset>"

    def test_entrada_pel_sitemap_d_equips(self):
        self.afegeix_sitemaps([fp.cami_equip(x, f"{x}-a") for x in "abcde"] + ["/ca/clubs/a"])
        c = ClientFals(self.pagines)
        partits = list(fp.recull_partits(c, date(2026, 11, 27), date(2026, 11, 29)))
        self.assertEqual(sorted(p.id_acta for p in partits), ["1", "2"])
        self.assertNotIn("https://www.fcf.cat/sitemap/noticies-1.xml", c.demanades)
        self.assertNotIn(fp.cami_equip("b", "b-a"), c.demanades)
        self.assertEqual(len([u for u in c.demanades if "/clubs/" in u]), 3)   # a, c, e

    def test_sitemap_equips_real_de_mostra(self):
        mostra = Path(__file__).resolve().parent.parent / "mostres" / "equips-1.xml"
        if not mostra.exists():
            self.skipTest("mostra no disponible")
        c = ClientFals({
            "/sitemap.xml": "<sitemapindex><sitemap><loc>https://www.fcf.cat/sitemap/equips-1.xml</loc></sitemap></sitemapindex>",
            "https://www.fcf.cat/sitemap/equips-1.xml": mostra.read_text(encoding="utf-8"),
        })
        equips = fp.equips_del_sitemap(c)
        self.assertEqual(len(equips), 4487)
        self.assertTrue(all(fp.RE_EQUIP.match(e) for e in equips))

    def test_sense_entrades(self):
        self.afegeix_sitemaps([])
        with self.assertRaises(RuntimeError):
            list(fp.recull_partits(ClientFals(self.pagines), date(2026, 11, 27), date(2026, 11, 29)))

    def test_subcamps_al_mateix_recinte(self):
        _, partits, _ = self.recorre()
        dades, pendents = f.construeix(partits, f.IndexCamps([]), date(2026, 11, 27), date(2026, 11, 29),
                                       datetime.now(timezone.utc))
        self.assertEqual(len(dades["camps"]), 1)
        camp = dades["camps"][0]
        self.assertEqual(camp["lat"], 41.4)
        self.assertEqual(sorted(str(p["subcamp"]) for p in camp["partits"]), ["Camp 2", "None"])
        self.assertTrue(pendents)   # falta el municipi

    def test_sitemap_real_de_mostra(self):
        mostra = Path(__file__).resolve().parent.parent / "mostres" / "sitemap-competicions.xml"
        if not mostra.exists():
            self.skipTest("mostra no disponible")
        c = ClientFals({"/sitemap/competicions.xml": mostra.read_text(encoding="utf-8")})
        grups = fp.grups_del_sitemap(c, "2026-2027")
        self.assertEqual(len(grups), 1061)
        self.assertTrue(any("futbol-sala" in comp for comp, _ in grups))

    def test_sitemap_camps_real_de_mostra(self):
        mostres = Path(__file__).resolve().parent.parent / "mostres"
        if not (mostres / "camps.xml").exists() or not (mostres / "equip.html").exists():
            self.skipTest("mostres no disponibles")
        c = ClientFals({"/sitemap/camps.xml": (mostres / "camps.xml").read_text(encoding="utf-8")})
        mapa = fp.camps_del_sitemap(c)
        self.assertEqual(len(mapa), 1043 + 1154 + 11)
        self.assertFalse(any(v.startswith("/es/") for v in mapa.values()))
        self.assertTrue(any(v.startswith("/ca/camps-futbol-sala/") for v in mapa.values()))
        partits, _, _ = fp.parse_equip((mostres / "equip.html").read_text(encoding="utf-8"))
        for p in partits:   # tots els camps de la mostra es troben, inclòs el COL·LEGI que donava 404
            self.assertIn(fp.clau_compacta(p.camp_nom_fcf), mapa, p.camp_nom_fcf)
        self.assertEqual(mapa[fp.clau_compacta("CAMP DE FUTBOL COL·LEGI ESCALA DEI-PROVIDENCIA DE CORAZON DE JESUS")],
                         "/ca/camps/camp-de-futbol-collegi-escala-dei-providencia-de-corazon-de-jesus")

    def test_fitxa_camp_sala_i_platja(self):
        self.assertEqual(fp._fitxa_camp("/ca/camps-futbol-sala/pavello-x#info"), "/ca/camps-futbol-sala/pavello-x")
        self.assertEqual(fp._fitxa_camp("https://www.fcf.cat/ca/camps-futbol-platja/platja-y"),
                         "/ca/camps-futbol-platja/platja-y")
        self.assertIsNone(fp._fitxa_camp("/es/camps/x"))
        self.assertIsNone(fp._fitxa_camp("/ca/campionat/x"))

    def test_completa_camps(self):
        horta = fp.parse_acta(fixture("acta_finalitzada.html"))          # slug real (de l'acta)
        pb = lambda **k: PartitBrut(**{**dict(id_acta="x", dia="2026-11-21", hora="10:00", camp_nom="Camp Y",
                                               camp_municipi="", categoria="", local="", visitant="", url_fcf="",
                                               lat=41.5, lon=2.2), **k})
        col_legi = pb(camp_codi="77", camp_nom_fcf="CAMP COL·LEGI Y")     # slug via sitemap
        perdut = pb(camp_codi="88", camp_nom_fcf="CAMP QUE NO EXISTEIX")  # sense slug: cap petició
        trencat = pb(camp_codi="99", camp_nom_fcf="CAMP TRENCAT")         # fitxa 404
        c = ClientFals({"/ca/camps/camp-de-futbol-mpal-horta": fixture("camp.html"),
                        "/ca/camps/camp-col-legi-y": fixture("camp.html").replace("Barcelona", "Vic")
                        .replace("camp-de-futbol-mpal-horta", "camp-col-legi-y").replace('"384"', '"77"')})
        crides = []

        def sitemap(client):
            crides.append(1)
            return {fp.clau_compacta(s): f"/ca/camps/{s}" for s in ("camp-col-legi-y", "camp-trencat")}

        index = f.IndexCamps([])
        self.assertEqual(f.completa_camps(c, index, [horta, horta, col_legi, perdut, trencat],
                                          fp.recull_camp, sitemap), 4)
        self.assertEqual(len(crides), 1)                                  # sitemap llegit un sol cop
        self.assertEqual(sorted(c.demanades), ["/ca/camps/camp-col-legi-y", "/ca/camps/camp-de-futbol-mpal-horta",
                                               "/ca/camps/camp-trencat"])
        self.assertEqual(index.per_codi("77")["municipi"], "Vic")
        self.assertEqual(index.per_codi("77")["lat"], 41.5)               # coordenades del partit
        self.assertEqual(index.per_codi("88")["municipi"], "")
        self.assertTrue(index.per_codi("99")["fitxa_fallida"])
        self.assertEqual(len(index.camps), 4)

        # Segona execució: res de nou, cap petició (el 404 no es reintenta)
        c.demanades.clear()
        self.assertEqual(f.completa_camps(c, index, [horta, col_legi, perdut, trencat], fp.recull_camp, sitemap), 0)
        self.assertEqual(c.demanades, [])
        dades, _ = f.construeix([horta], index, date(2026, 11, 20), date(2026, 11, 22), datetime.now(timezone.utc))
        self.assertEqual((dades["camps"][0]["municipi"], dades["camps"][0]["lat"]), ("Barcelona", 41.434667))

class TestRotacio(unittest.TestCase):
    def test_parseja_part(self):
        self.assertEqual(f.parseja_part("2/3"), (2, 3))
        self.assertIsNone(f.parseja_part("1/1"))
        for dolent in ("0/3", "4/3", "x"):
            with self.assertRaises(Exception):
                f.parseja_part(dolent)

    def test_parts_disjuntes_i_completes(self):
        grups = [f"comp-{i}/grup-{j}" for i in range(30) for j in range(10)]
        parts = [{g for g in grups if fp.part_de(g, 3) == i} for i in (1, 2, 3)]
        self.assertEqual(sum(len(x) for x in parts), len(grups))
        self.assertEqual(set().union(*parts), set(grups))
        self.assertTrue(all(len(x) > 50 for x in parts))

    def test_recorregut_nomes_de_la_part(self):
        r = TestRecorregut()
        r.setUp()
        grup = "lliga-benjami-futbol-sala/grup-1"
        entrades = {grup: [fp.cami_equip(x, f"{x}-a") for x in "abcde"]}
        meva = fp.part_de(grup, 2)
        altra = 3 - meva
        c = ClientFals(r.pagines)
        self.assertEqual(len(list(fp.recull_partits(c, date(2026, 11, 27), date(2026, 11, 29),
                                                    entrades=entrades, part=(meva, 2)))), 2)
        with self.assertRaises(RuntimeError):   # cap equip d'aquesta part
            list(fp.recull_partits(ClientFals(r.pagines), date(2026, 11, 27), date(2026, 11, 29),
                                   entrades=entrades, part=(altra, 2)))

    def test_temps_maxim(self):
        r = TestRecorregut()
        r.setUp()
        temps = iter(range(0, 1000, 10))
        informe = {}
        partits = list(fp.recull_partits(ClientFals(r.pagines), date(2026, 11, 27), date(2026, 11, 29),
                                         entrades=r.entrades, temps_max_s=15, informe=informe,
                                         rellotge=lambda: next(temps)))
        self.assertTrue(informe["tallat"])
        self.assertEqual(informe["fitxes"], 1)
        self.assertEqual([p.id_acta for p in partits], ["1"])

    def test_fusiona(self):
        url = "https://www.fcf.cat/ca/partit/2026-2027/c/g/jornada-1/{}-vs-{}"
        ant = [partit("1", url_fcf=url.format("a-a", "b-a")), partit("2", url_fcf=url.format("c-a", "d-a")),
               partit("3", url_fcf=url.format("e-a", "f-a"), hora="09:00")]
        nous = [partit("3", url_fcf=url.format("e-a", "f-a"), hora="12:00")]
        # a-a visitat i el partit 1 ja no hi surt (ajornat): desapareix
        res = {p.id_acta: p for p in f.fusiona(ant, nous, {"a-a", "e-a"})}
        self.assertEqual(sorted(res), ["2", "3"])
        self.assertEqual(res["3"].hora, "12:00")

    def test_sortida_reconstruible(self):
        p1 = partit("1", camp="Camp Municipal X - Camp 2")
        p1 = PartitBrut(**{**p1.__dict__, "lat": 41.4, "lon": 2.1, "camp_municipi": "Vic"})
        ini, fi = date(2026, 11, 27), date(2026, 11, 29)
        d1, _ = f.construeix([p1], f.IndexCamps([]), ini, fi, datetime.now(timezone.utc))
        d2, _ = f.construeix(f.partits_de_sortida(d1), f.IndexCamps([]), ini, fi, datetime.now(timezone.utc))
        self.assertEqual(d1["camps"], d2["camps"])

    def test_main_fusiona_amb_la_mateixa_setmana(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            ini, fi = date(2026, 11, 27), date(2026, 11, 29)
            url = "https://www.fcf.cat/ca/partit/2026-2027/c/g/jornada-1/{}-vs-{}"
            ant, _ = f.construeix([partit(str(i), url_fcf=url.format(f"x{i}-a", f"y{i}-a")) for i in range(10)],
                                  f.IndexCamps([]), ini, fi, datetime.now(timezone.utc))
            p.write_text(json.dumps(ant))
            nou = partit("99", url_fcf=url.format("z-a", "w-a"))

            def fals(*a, informe=None, **k):
                informe.update(equips_visitats={"z-a"}, fitxes=1, tallat=False)
                return [nou]
            with mock.patch("fcf_parser.recull_partits", side_effect=fals):
                self.assertEqual(f.main(["--sortida", str(p), "--data", "2026-11-25", "--part", "2/3",
                                         *rutes_temporals(d)]), 0)
            self.assertEqual(json.loads(p.read_text())["total_partits"], 11)


class TestOrdreITemps(unittest.TestCase):
    def test_completa_el_grup_abans_de_saltar(self):
        """Amb dos grups i entrada intercalada, un tall deixa el primer grup sencer."""
        dia = "2026-11-28"
        pag = {}
        for g, (x, y, z, w) in (("grup-1", "abcd"), ("grup-2", "pqrs")):
            pag[fp.cami_equip(x, f"{x}-a")] = pagina_equip([match(g + "1", x, y, dia, grup=g), match(g + "9", x, z, "2026-11-21", grup=g)])
            pag[fp.cami_equip(z, f"{z}-a")] = pagina_equip([match(g + "2", z, w, dia, grup=g)])
            pag[fp.cami_equip(y, f"{y}-a")] = pagina_equip([match(g + "1", x, y, dia, grup=g)])
            pag[fp.cami_equip(w, f"{w}-a")] = pagina_equip([match(g + "2", z, w, dia, grup=g)])
        entrades = {"sitemap": [fp.cami_equip("a", "a-a"), fp.cami_equip("p", "p-a")]}
        temps = iter(range(0, 1000, 10))
        partits = list(fp.recull_partits(ClientFals(pag), date(2026, 11, 27), date(2026, 11, 29),
                                         entrades=entrades, temps_max_s=35, rellotge=lambda: next(temps)))
        # Sense prioritzar rivals, la 2a fitxa seria "p" (grup-2) i el grup-1 quedaria a mitges
        self.assertEqual(sorted(p.id_acta for p in partits), ["grup-11", "grup-12"])

    def test_camps_respecten_el_temps(self):
        pb = lambda codi: PartitBrut(id_acta=codi, dia="2026-11-28", hora="10:00", camp_nom=f"Camp {codi}",
                                     camp_municipi="", categoria="", local="", visitant="", url_fcf="",
                                     camp_codi=codi, camp_fitxa=f"/ca/camps/camp-{codi}", lat=41.0, lon=2.0)
        c = ClientFals({f"/ca/camps/camp-{i}": fixture("camp.html") for i in range(5)})
        temps = iter(range(0, 1000, 10))
        index = f.IndexCamps([])
        self.assertEqual(f.completa_camps(c, index, [pb(str(i)) for i in range(5)], fp.recull_camp,
                                          temps_max_s=25, rellotge=lambda: next(temps)), 5)
        self.assertLess(len(c.demanades), 5)
        sense = [x for x in index.camps if not x["municipi"]]
        self.assertTrue(sense)
        self.assertFalse(any(x.get("fitxa_fallida") for x in sense))   # es reintentaran


class TestProhibitAMain(unittest.TestCase):
    def test_main_surt_amb_3_i_no_escriu(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text('{"total_partits": 7}')
            with mock.patch("fcf_parser.recull_partits", side_effect=Prohibit("HTTP 403")):
                self.assertEqual(f.main(["--sortida", str(p), *rutes_temporals(d)]), 3)
            self.assertEqual(p.read_text(), '{"total_partits": 7}')
            self.assertFalse((Path(d) / "eq.json").exists())

    def test_prohibit_a_les_fitxes_de_camp_tambe_atura(self):
        p = PartitBrut(id_acta="1", dia="2026-11-28", hora="10:00", camp_nom="Camp", camp_municipi="",
                       categoria="", local="", visitant="", url_fcf="", camp_codi="1", camp_fitxa="/ca/camps/x")

        def prohibit(client, fitxa):
            raise Prohibit("403")
        with self.assertRaises(Prohibit):
            f.completa_camps(None, f.IndexCamps([]), [p], prohibit)
