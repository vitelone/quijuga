"""Estructures de dades compartides entre el parser i el pipeline."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PartitBrut:
    """Un partit tal com l'extreu el parser, abans de normalitzar.

    `camp_nom` pot portar el subcamp enganxat (ex. "Camp Municipal X - Camp 2");
    el pipeline el separa. `modalitat` i `durada_min` són opcionals: si el parser
    no els troba, es dedueixen de la categoria.
    """

    id_acta: str
    dia: str            # "YYYY-MM-DD"
    hora: str           # "HH:MM"
    camp_nom: str
    camp_municipi: str
    categoria: str      # ex. "Benjamí Segona Divisió - Grup 14"
    local: str
    visitant: str
    url_fcf: str
    camp_fitxa: str | None = None  # camí REAL de la fitxa del camp, p. ex. /ca/camps-futbol-sala/x (mai endevinat)
    camp_nom_fcf: str | None = None  # nom del camp tal com el dona l'FCF (per buscar-ne l'slug)
    camp_codi: str | None = None   # CODIGO_CAMPO de l'FCF
    lat: float | None = None
    lon: float | None = None
    subcamp: str | None = None
    modalitat: str | None = None
    durada_min: int | None = None
    resultat: str | None = None
