"""Client HTTP "educat": seqüencial, lent, honest i respectuós amb robots.txt."""
from __future__ import annotations

import logging
import random
import re
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import requests

log = logging.getLogger(__name__)

USER_AGENT = "QuiJugaBot/0.1 (+https://quijuga.cat/bot)"
ROBOTS_TOKEN = "QuiJugaBot"
CAMINS_PROHIBITS = ("/api/",)      # explícitament prohibit al robots.txt de l'FCF
CODIS_REINTENT = {429, 502, 503, 504}


class Robots:
    """Intèrpret de robots.txt segons RFC 9309 (guanya la regla més llarga).

    `urllib.robotparser` aplica la primera regla que coincideix, cosa que amb
    "Allow: /" al davant ho permetria tot.
    """

    def __init__(self, text: str = "", tot_permes: bool = False):
        self.tot_permes = tot_permes
        self.grups: list[tuple[set[str], list[tuple[bool, str]]]] = []
        agents: set[str] = set()
        regles: list[tuple[bool, str]] = []
        darrera_agent = False
        for linia in text.splitlines():
            linia = linia.split("#", 1)[0].strip()
            if ":" not in linia:
                continue
            camp, valor = (x.strip() for x in linia.split(":", 1))
            camp = camp.lower()
            if camp == "user-agent":
                if not darrera_agent and (agents or regles):
                    self.grups.append((agents, regles))
                    agents, regles = set(), []
                agents.add(valor.lower())
                darrera_agent = True
            elif camp in ("allow", "disallow"):
                darrera_agent = False
                if agents and valor:
                    regles.append((camp == "allow", valor))
        if agents:
            self.grups.append((agents, regles))

    @staticmethod
    def _patro(regla: str) -> re.Pattern:
        ancorat = regla.endswith("$")
        cos = regla[:-1] if ancorat else regla
        return re.compile(".*".join(re.escape(p) for p in cos.split("*")) + ("$" if ancorat else ""))

    def permes(self, token: str, url: str) -> bool:
        if self.tot_permes:
            return True
        p = urlparse(url)
        cami = (p.path or "/") + (f"?{p.query}" if p.query else "")
        token = token.lower()
        regles = [r for agents, rr in self.grups if token in agents for r in rr]
        if not regles and not any(token in a for a, _ in self.grups):
            regles = [r for agents, rr in self.grups if "*" in agents for r in rr]
        millor: tuple[int, bool] = (-1, True)
        for allow, regla in regles:
            if self._patro(regla).match(cami):
                clau = (len(regla), allow)  # a igual longitud, guanya Allow
                if clau > millor:
                    millor = clau
        return millor[1]


class Blocat(Exception):
    """La URL no es pot demanar (robots.txt, camí prohibit o límit de peticions)."""


class ClientEducat:
    def __init__(
        self,
        base_url: str = "https://www.fcf.cat",
        user_agent: str = USER_AGENT,
        pausa: tuple[float, float] = (2.5, 5.5),
        max_intents: int = 5,
        max_peticions: int = 3000,
        timeout: float = 20.0,
        sessio: requests.Session | None = None,
        dorm=time.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.pausa = pausa
        self.max_intents = max_intents
        self.max_peticions = max_peticions
        self.timeout = timeout
        self.dorm = dorm
        self.sessio = sessio or requests.Session()
        self.sessio.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ca,es;q=0.8",
        })
        self.peticions = 0
        self._robots: Robots | None = None

    # ---------- robots.txt ----------

    def _carrega_robots(self) -> Robots:
        url = f"{self.base_url}/robots.txt"
        try:
            r = self.sessio.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            raise Blocat(f"No s'ha pogut llegir {url}: {e}") from e
        if r.status_code >= 500:
            # Conveni: si robots.txt no respon, no rastregem.
            raise Blocat(f"robots.txt ha retornat {r.status_code}; no es rastreja")
        if r.status_code >= 400:
            return Robots(tot_permes=True)
        return Robots(r.text)

    def permes(self, url: str) -> bool:
        cami = urlparse(url).path or "/"
        if any(cami.startswith(p) for p in CAMINS_PROHIBITS):
            return False
        if self._robots is None:
            self._robots = self._carrega_robots()
        return self._robots.permes(ROBOTS_TOKEN, url)

    # ---------- peticions ----------

    def _espera_reintent(self, intent: int, resposta: requests.Response | None) -> float:
        if resposta is not None:
            ra = resposta.headers.get("Retry-After")
            if ra:
                try:
                    return min(float(ra), 600.0)
                except ValueError:
                    try:
                        return max(0.0, min(parsedate_to_datetime(ra).timestamp() - time.time(), 600.0))
                    except (TypeError, ValueError):
                        pass
        return min(10.0 * 2 ** intent + random.uniform(0, 5), 600.0)

    def get(self, url_o_cami: str) -> str:
        url = urljoin(self.base_url + "/", url_o_cami)
        if urlparse(url).netloc != urlparse(self.base_url).netloc:
            raise Blocat(f"Domini no permès: {url}")
        if not self.permes(url):
            raise Blocat(f"Prohibit per robots.txt: {url}")

        for intent in range(self.max_intents):
            if self.peticions >= self.max_peticions:
                raise Blocat(f"S'ha arribat al límit de {self.max_peticions} peticions")
            if self.peticions:
                self.dorm(random.uniform(*self.pausa))
            self.peticions += 1

            resposta = None
            try:
                resposta = self.sessio.get(url, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                log.warning("Error de xarxa a %s (%s), intent %d", url, e, intent + 1)
            else:
                if resposta.status_code not in CODIS_REINTENT:
                    resposta.raise_for_status()
                    resposta.encoding = resposta.encoding or "utf-8"
                    return resposta.text
                log.warning("HTTP %d a %s, intent %d", resposta.status_code, url, intent + 1)

            if intent + 1 < self.max_intents:
                self.dorm(self._espera_reintent(intent, resposta))

        raise requests.HTTPError(f"Esgotats {self.max_intents} intents per {url}")
