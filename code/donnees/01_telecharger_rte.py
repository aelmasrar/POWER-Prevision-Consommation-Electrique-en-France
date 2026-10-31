"""
Étape 1 : télécharger la consommation électrique française (RTE éCO2mix).

Source : ODRÉ (Open Data Réseaux Énergies), jeu « eco2mix-national-cons-def »
= données éCO2mix nationales consolidées et définitives, publiées par RTE.
Pas besoin de compte ni de clé d'API.

Le fichier brut est enregistré tel quel dans donnees/brut/ : on ne le modifie
jamais à la main, tout le nettoyage se fait dans 02_construire_base.py.

Usage (depuis le dossier POWER) :
    python code/donnees/01_telecharger_rte.py
    python code/donnees/01_telecharger_rte.py --debut 2011-12-31 --fin 2026-01-02
"""

import argparse
from pathlib import Path

import requests

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_BRUT = RACINE / "donnees" / "brut"

URL_EXPORT = (
    "https://odre.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
    "{jeu}/exports/csv"
)


def telecharger(jeu: str, debut: str, fin: str) -> Path:
    # Le jeu RTE commence le 1er janvier 2012 à 00:00 heure de Paris, soit le
    # 31 décembre 2011 à 23:00 UTC : d'où un début au 31/12/2011 (filtre en UTC).
    params = {
        "where": f"date_heure >= date'{debut}' AND date_heure < date'{fin}'",
        "timezone": "UTC",
        "delimiter": ";",
    }
    DOSSIER_BRUT.mkdir(parents=True, exist_ok=True)
    sortie = DOSSIER_BRUT / f"{jeu}_{debut}_{fin}.csv"

    print(f"Téléchargement de {jeu} ({debut} -> {fin})...")
    with requests.get(URL_EXPORT.format(jeu=jeu), params=params,
                      stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(sortie, "wb") as f:
            for bloc in r.iter_content(chunk_size=1 << 20):
                f.write(bloc)

    taille = sortie.stat().st_size / 1e6
    print(f"OK : {sortie} ({taille:.1f} Mo)")
    return sortie


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--jeu", default="eco2mix-national-cons-def")
    p.add_argument("--debut", default="2011-12-31")
    p.add_argument("--fin", default="2026-01-02")
    a = p.parse_args()
    telecharger(a.jeu, a.debut, a.fin)
