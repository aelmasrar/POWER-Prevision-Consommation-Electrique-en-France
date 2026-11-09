"""
Étape 7 : événements exceptionnels (crises, grèves, compétitions, météo extrême...).

Entrée : donnees/evenements.csv, une ligne par événement, écrite à la main.
  debut_local / fin_local : heure de Paris, fin EXCLUE (un jour entier = J 00:00 -> J+1 00:00).
  intensite               : 1 = faible, 2 = fort, 3 = très fort (confinement strict).
  connu_la_veille         : 1 si l'événement est connu la veille de la prévision.
  verifie                 : 1 si les dates viennent d'une source sûre, 0 si à vérifier.

Sorties (donnees/base/, hors Git) :
  evenements_periodes.parquet  une ligne par événement, en UTC, au format de la table
                               `evenement` du schéma (type, debut_utc, fin_utc, code_region, description)
  evenements_horaire.parquet   une ligne par heure UTC, une colonne evt_<categorie> par catégorie
  evenements_horaire.csv       la même chose, pour regarder dans Excel
  etat_evenements.txt          constat : nombre d'heures touchées par catégorie, chevauchements

Colonnes horaires : evt_<categorie> = intensité maximale des événements actifs à cette heure
(0 = rien). Entiers 0 à 3 : rien à encoder de plus pour les modèles à arbres.
evt_imprevisible = 1 pendant un événement non connu la veille : à exclure des variables
de prédiction, utile seulement pour expliquer des erreurs.

Usage (depuis le dossier POWER) :
    python code/donnees/07_evenements.py
"""

from pathlib import Path

import pandas as pd

RACINE = Path(__file__).resolve().parents[2]
SOURCE = RACINE / "donnees" / "evenements.csv"
DOSSIER_BASE = RACINE / "donnees" / "base"

DEBUT = pd.Timestamp("2012-01-01 00:00", tz="UTC")     # même période que la base
FIN = pd.Timestamp("2025-12-31 23:00", tz="UTC")
CATEGORIES = ["sanitaire", "energie", "geopolitique", "social", "reglementaire",
              "sport", "meteo", "choc_national"]


def vers_utc(colonne: pd.Series) -> pd.Series:
    """Heure de Paris -> UTC. Heure inexistante (passage à l'heure d'été) : décalée en avant ;
    heure en double (passage à l'heure d'hiver) : on prend la première."""
    locale = pd.to_datetime(colonne, format="%Y-%m-%d %H:%M")
    return locale.dt.tz_localize("Europe/Paris", nonexistent="shift_forward",
                                 ambiguous=True).dt.tz_convert("UTC")


def lire_evenements() -> pd.DataFrame:
    ev = pd.read_csv(SOURCE, sep=";", dtype={"code_region": str}, keep_default_na=False)
    inconnues = set(ev["categorie"]) - set(CATEGORIES)
    if inconnues:
        raise SystemExit(f"Catégories inconnues dans {SOURCE.name} : {sorted(inconnues)}")
    ev["debut_utc"] = vers_utc(ev["debut_local"])
    ev["fin_utc"] = vers_utc(ev["fin_local"])
    mauvais = ev[ev["fin_utc"] <= ev["debut_utc"]]
    if len(mauvais):
        raise SystemExit("Fin avant le début pour les événements : " + ", ".join(map(str, mauvais["id"])))
    ev["code_region"] = ev["code_region"].replace("", None)
    return ev


def table_horaire(ev: pd.DataFrame) -> pd.DataFrame:
    index = pd.date_range(DEBUT, FIN, freq="h", name="heure_utc")
    h = pd.DataFrame(0, index=index, columns=[f"evt_{c}" for c in CATEGORIES] + ["evt_imprevisible"],
                     dtype="int8")
    for e in ev.itertuples():
        actif = (index >= e.debut_utc) & (index < e.fin_utc)
        col = f"evt_{e.categorie}"
        h.loc[actif, col] = h.loc[actif, col].clip(lower=e.intensite)
        if not e.connu_la_veille:
            h.loc[actif, "evt_imprevisible"] = 1
    return h


def main() -> None:
    ev = lire_evenements()
    h = table_horaire(ev)
    DOSSIER_BASE.mkdir(parents=True, exist_ok=True)

    periodes = ev.rename(columns={"nom": "description"})[
        ["id", "categorie", "type", "debut_utc", "fin_utc", "code_region", "description",
         "intensite", "connu_la_veille", "verifie"]]
    periodes.to_parquet(DOSSIER_BASE / "evenements_periodes.parquet", index=False)
    h.to_parquet(DOSSIER_BASE / "evenements_horaire.parquet")
    h.to_csv(DOSSIER_BASE / "evenements_horaire.csv", sep=";")

    constat = [f"{len(ev)} événements lus dans {SOURCE.name} ({(ev['verifie'] == 0).sum()} à vérifier)",
               f"Table horaire : {len(h)} heures, {h.index[0]} -> {h.index[-1]}", "",
               "Heures touchées par catégorie (intensité > 0) :"]
    for c in CATEGORIES:
        n = int((h[f"evt_{c}"] > 0).sum())
        constat.append(f"    {c:<14} {n:>7} h  ({n / len(h):.1%})")
    n = int(h["evt_imprevisible"].sum())
    constat += [f"    {'imprévisible':<14} {n:>7} h  (à exclure des variables de prédiction)", "",
                "Événements hors de la période de la base : "
                + (", ".join(map(str, ev.loc[(ev["fin_utc"] <= DEBUT) | (ev["debut_utc"] > FIN), "id"])) or "aucun")]
    (DOSSIER_BASE / "etat_evenements.txt").write_text("\n".join(constat) + "\n", encoding="utf-8")
    print("\n".join(constat))


if __name__ == "__main__":
    main()
