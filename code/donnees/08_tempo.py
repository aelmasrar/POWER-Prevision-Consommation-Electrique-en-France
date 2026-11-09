"""
Étape 8 : couleurs des jours Tempo (bleu, blanc, rouge).

Tempo est une option tarifaire d'EDF : chaque jour reçoit une couleur, la même pour toute
la France (pas de découpage par région). RTE choisit la couleur la veille, selon la
consommation prévue : rouge = jours les plus tendus (22 par saison, de novembre à mars,
hors week-end), blanc = 43 jours, bleu = le reste. La couleur est connue la veille :
utilisable pour prédire le lendemain.

Un jour Tempo va de 6 h à 6 h le lendemain (heure de Paris), pas de minuit à minuit.

Sources, essayées dans l'ordre pour chaque saison (1er septembre -> 31 août) :
  1. RTE éCO2mix, calendrier Tempo téléchargeable (toutes les saisons depuis 2012)
  2. jourtempo.fr, historique ouvert (seulement depuis le 1er septembre 2014)

Sorties (donnees/base/, hors Git) :
  tempo_jours.parquet / .csv     une ligne par jour : date, couleur, source
  tempo_horaire.parquet / .csv   une ligne par heure UTC : tempo_bleu, tempo_blanc, tempo_rouge (0/1)
  etat_tempo.txt                 constat : jours par couleur et par saison, jours manquants

Usage (depuis le dossier POWER) :
    python code/donnees/08_tempo.py
    python code/donnees/08_tempo.py --sans-telechargement   # relit les fichiers déjà téléchargés
"""

import argparse
import io
import re
import zipfile
from pathlib import Path

import pandas as pd
import requests

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_BASE = RACINE / "donnees" / "base"
DOSSIER_BRUT = RACINE / "donnees" / "brut" / "tempo"

DEBUT = pd.Timestamp("2012-01-01 00:00", tz="UTC")     # même période que la base
FIN = pd.Timestamp("2025-12-31 23:00", tz="UTC")
SAISONS = range(2011, 2026)                             # saison 2011-2012 -> 2025-2026
COULEURS = ["bleu", "blanc", "rouge"]

URL_RTE = "https://eco2mix.rte-france.com/curves/downloadCalendrierTempo?season={a:02d}-{b:02d}"
URL_JOURTEMPO = "https://jourtempo.fr/api/v1/tempo-historique.csv"

MOTIF = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4}).*?\b(BLEU|BLANC|ROUGE|BLUE|WHITE|RED)\b",
                   re.IGNORECASE)
TRADUCTION = {"blue": "bleu", "white": "blanc", "red": "rouge"}


def extraire(texte: str) -> pd.DataFrame:
    """Cherche dans chaque ligne une date et une couleur, quel que soit le format du fichier."""
    lignes = []
    for ligne in texte.splitlines():
        m = MOTIF.search(ligne)
        if m:
            date = pd.to_datetime(m.group(1), dayfirst="/" in m.group(1)).date()
            c = m.group(2).lower()
            lignes.append((date, TRADUCTION.get(c, c)))
    return pd.DataFrame(lignes, columns=["date", "couleur"]).drop_duplicates("date", keep="last")


def decoder(contenu: bytes) -> str:
    if contenu[:2] == b"PK":                            # zip RTE contenant un .xls (texte tabulé)
        with zipfile.ZipFile(io.BytesIO(contenu)) as z:
            contenu = z.read(z.namelist()[0])
    for enc in ("utf-8", "latin-1"):
        try:
            return contenu.decode(enc)
        except UnicodeDecodeError:
            pass
    return contenu.decode("latin-1", errors="replace")


def telecharger(url: str, fichier: Path, telecharger_: bool) -> str | None:
    if telecharger_ or not fichier.exists():
        try:
            r = requests.get(url, timeout=60, headers={"User-Agent": "POWER-projet-ENSEIRB"})
            r.raise_for_status()
            fichier.write_bytes(r.content)
        except requests.RequestException as e:
            print(f"    échec {url} : {e}")
            return None
    return decoder(fichier.read_bytes())


def lire_jours(telecharger_: bool) -> pd.DataFrame:
    DOSSIER_BRUT.mkdir(parents=True, exist_ok=True)
    texte = telecharger(URL_JOURTEMPO, DOSSIER_BRUT / "jourtempo_historique.csv", telecharger_)
    secours = extraire(texte) if texte else pd.DataFrame(columns=["date", "couleur"])

    morceaux = []
    for a in SAISONS:
        debut, fin = pd.Timestamp(a, 9, 1).date(), pd.Timestamp(a + 1, 8, 31).date()
        nom = f"rte_tempo_{a % 100:02d}-{(a + 1) % 100:02d}.zip"
        texte = telecharger(URL_RTE.format(a=a % 100, b=(a + 1) % 100), DOSSIER_BRUT / nom, telecharger_)
        jours = extraire(texte) if texte else pd.DataFrame(columns=["date", "couleur"])
        jours = jours[(jours["date"] >= debut) & (jours["date"] <= fin)]
        source = "rte"
        if len(jours) < 300:                            # saison absente ou incomplète chez RTE
            jours = secours[(secours["date"] >= debut) & (secours["date"] <= fin)]
            source = "jourtempo"
        print(f"  saison {a}-{a + 1} : {len(jours)} jours ({source})")
        morceaux.append(jours.assign(source=source))
    return pd.concat(morceaux, ignore_index=True).sort_values("date").reset_index(drop=True)


def table_horaire(jours: pd.DataFrame) -> pd.DataFrame:
    index = pd.date_range(DEBUT, FIN, freq="h", name="heure_utc")
    # heure UTC -> heure de Paris -> jour Tempo (qui commence à 6 h)
    jour_tempo = (index.tz_convert("Europe/Paris") - pd.Timedelta(hours=6)).date
    couleur = pd.Series(jour_tempo, index=index).map(jours.set_index("date")["couleur"])
    h = pd.DataFrame(index=index)
    for c in COULEURS:
        h[f"tempo_{c}"] = (couleur == c).astype("int8")
    return h


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sans-telechargement", action="store_true")
    args = p.parse_args()

    jours = lire_jours(not args.sans_telechargement)
    h = table_horaire(jours)
    DOSSIER_BASE.mkdir(parents=True, exist_ok=True)
    jours.to_parquet(DOSSIER_BASE / "tempo_jours.parquet", index=False)
    jours.to_csv(DOSSIER_BASE / "tempo_jours.csv", sep=";", index=False)
    h.to_parquet(DOSSIER_BASE / "tempo_horaire.parquet")
    h.to_csv(DOSSIER_BASE / "tempo_horaire.csv", sep=";")

    sans = h.index[h.sum(axis=1) == 0]
    constat = [f"{len(jours)} jours lus, du {jours['date'].min()} au {jours['date'].max()}",
               f"Table horaire : {len(h)} heures, {h.index[0]} -> {h.index[-1]}",
               f"Heures sans couleur : {len(sans)}"
               + (f" (de {sans[0]} à {sans[-1]})" if len(sans) else ""), "",
               "Jours par saison (attendu environ 300 bleu, 43 blanc, 22 rouge) :",
               f"    {'saison':<11}{'bleu':>6}{'blanc':>7}{'rouge':>7}  source"]
    saison = jours["date"].map(lambda d: d.year if d.month >= 9 else d.year - 1)
    for a, g in jours.groupby(saison):
        n = g["couleur"].value_counts()
        constat.append(f"    {a}-{a + 1:<6}{n.get('bleu', 0):>6}{n.get('blanc', 0):>7}{n.get('rouge', 0):>7}"
                       f"  {'/'.join(sorted(g['source'].unique()))}")
    (DOSSIER_BASE / "etat_tempo.txt").write_text("\n".join(constat) + "\n", encoding="utf-8")
    print("\n".join(constat))


if __name__ == "__main__":
    main()
