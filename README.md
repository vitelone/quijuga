# ⚽ Qui Juga?

**Obres el mòbil al camp i et diu quin partit s'està jugant ara mateix.**

Ets a la grada un dissabte al matí. Al camp hi ha dos partits de futbol 7 alhora, el teu fill juga en un i a l'altre hi ha un equip que no coneixes. *Quin equip és el de vermell? De quina categoria són? Com van?*

Qui Juga? respon això en un segon: detecta el camp on ets i et mostra els partits que s'hi estan jugant, amb la categoria, els equips i un botó que porta directament a la fitxa oficial de la **Federació Catalana de Futbol**.

---

## Qui hi ha darrere

Sóc un pare de nanos que juguen a futbol. Com tantes famílies, passo els caps de setmana d'un camp a l'altre, i moltes vegades em trobo buscant al mòbil quin partit és el que tinc davant. La informació hi és tota a la web de l'FCF, però des de la grada, amb una mà ocupada i el partit començant, no sempre és fàcil arribar-hi.

Aquest projecte el faig en el meu temps lliure. Si et resulta útil, ja ha valgut la pena.

## La font és l'FCF

Qui Juga? **no substitueix la web de la Federació Catalana de Futbol, ni ho pretén.** L'FCF és la font oficial de totes les dades i el lloc on cal consultar actes, classificacions, calendaris i qualsevol informació oficial.

Aquí només s'hi posa una drecera:

- 📍 Detecta el camp on ets i mostra només el que s'hi juga ara.
- 🔗 Cada partit porta un enllaç directe a la seva fitxa a **fcf.cat**, que és on hi ha la informació completa i oficial.
- 📱 Està pensat per a una consulta ràpida des del mòbil, a peu de camp.

Si les dades d'aquí i les de l'FCF no coincideixen, **sempre val el que diu l'FCF.**

## Sobre la publicitat

Si mai veus un anunci a Qui Juga?, és per cobrir els costos del projecte, com ara el domini. El servei és i serà gratuït per a les famílies.

## Com es fa, amb respecte pel web de l'FCF

M'importa que aquesta eina no sigui cap molèstia per a la Federació:

- **Ritme lent.** Les dades es recullen de nit, d'una en una, amb pauses de diversos segons entre petició i petició, i repartides al llarg de la setmana.
- **Identificació honesta.** Les peticions s'identifiquen com a `QuiJugaBot`, sense fer-se passar per un navegador.
- **Es respecta el `robots.txt` de fcf.cat**, i no es fa servir cap zona que hi estigui restringida.
- **Cap dada personal.** Les pàgines de l'FCF inclouen alineacions i plantilles amb noms de jugadors, molts d'ells menors. **Qui Juga? no les recull ni les guarda mai.** Només es fan servir dades del partit: data, hora, camp, categoria i equips.
- **El web és completament estàtic.** Quan algú consulta Qui Juga? des del camp, el seu mòbil no fa cap petició a l'FCF fins que no prem l'enllaç a la fitxa oficial.

Si treballes a l'FCF i hi ha alguna cosa que voldríeu que fes diferent, o preferiu que no es faci, **obre una [*issue*](../../issues) i ho parlem.**

## Per a qui vulgui mirar el codi

```
public/     Web estàtica (HTML, CSS i JavaScript, sense dependències)
scraper/    Recollida setmanal de dades (Python)
.github/    Automatització amb GitHub Actions
```

- **Frontend:** tot el contingut cap a la meitat superior de la pantalla. Quan hi ha partits simultanis en un camp dividit (futbol 7, Camp 1 i Camp 2), es mostren les dues targetes alhora, amb la categoria ben destacada perquè sàpigues quin és quin.
- **Scraper:** llegeix les fitxes públiques d'equip de fcf.cat, agrupa els partits per camp i genera un sol fitxer JSON per a tot Catalunya.
- **Tests:** `python -m unittest discover -s scraper/tests`. Funcionen sense connexió, amb fragments anonimitzats.

Per provar la web en local:

```bash
cd public && python3 -m http.server 8000
# http://localhost:8000/?camp=aliga&ara=2026-11-28T10:45
```

El paràmetre `ara` simula una data i una hora, per veure els partits de prova sense haver d'esperar al cap de setmana.

## Contacte

Suggeriments, errors o un camp que no surt bé: obre una [*issue*](../../issues). Totes es llegeixen.

---

*Fet amb ❤️ per a les famílies del futbol base català. Dades: [Federació Catalana de Futbol](https://www.fcf.cat).*
