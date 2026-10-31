"""
Étape 2 : construire la base horaire du projet POWER à partir du fichier brut RTE.

Entrée  : donnees/brut/eco2mix-national-cons-def_*.csv (sortie de l'étape 1)
Sorties : donnees/base/base_power_horaire.parquet (+ .csv)   -> table des modèles
          donnees/base/production_filieres_horaire.parquet   -> pour le dashboard
          donnees/base/etat_du_brut.txt                      -> constat pour le nettoyage

Règles (voir donnees/squelette-base.md) :
- une ligne = une heure, index en UTC (pas de trou ni de doublon aux changements d'heure) ;
- la valeur horaire = moyenne des valeurs publiées dans l'heure (en MW). En pratique RTE
  ne remplit la conso qu'à :00 et :30 : c'est la moyenne de ces deux valeurs ;
- seule correction : les lignes fictives que RTE ajoute pour l'heure qui n'existe pas
  au passage à l'heure d'été sont retirées (voir retirer_heures_fictives) ;
- AUCUN autre nettoyage ici : les trous et valeurs bizarres restent tels quels. Le script
  écrit seulement un constat (etat_du_brut.txt) pour la personne chargée du nettoyage ;
- les colonnes prévues mais remplies par d'autres membres (nettoyage, calendrier,
  retards, vacances, météo) sont créées VIDES, avec leur type.

Usage (depuis le dossier POWER) :
    python code/donnees/02_construire_base.py
"""

import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_BRUT = RACINE / "donnees" / "brut"
DOSSIER_BASE = RACINE / "donnees" / "base"

TZ = "Europe/Paris"
# Du 1er janvier 2012 00:00 au 31 décembre 2025 23:00, heure de Paris, au pas horaire.
PAS = "h"
DEBUT = pd.Timestamp("2012-01-01 00:00", tz=TZ).tz_convert("UTC")
FIN = pd.Timestamp("2025-12-31 23:00", tz=TZ).tz_convert("UTC")

DECOUPAGE = {**{annee: "train" for annee in range(2012, 2024)}, 2024: "valid", 2025: "test"}

# Nom normalisé dans le fichier source -> nom dans notre base.
# Couvre l'export de l'API (« consommation ») et l'export du site
# (« Consommation (MW) »).
COLONNES_CONSO = {
    "consommation": "conso_mw",
    "prevision_j_1": "prevision_rte_j1_mw",
    "prevision_j1": "prevision_rte_j1_mw",
    "prevision_j": "prevision_rte_j_mw",
}
COLONNES_FILIERES = {
    "nucleaire": "prod_nucleaire_mw", "eolien": "prod_eolien_mw",
    "solaire": "prod_solaire_mw", "hydraulique": "prod_hydraulique_mw",
    "gaz": "prod_gaz_mw", "charbon": "prod_charbon_mw", "fioul": "prod_fioul_mw",
    "bioenergies": "prod_bioenergies_mw", "pompage": "pompage_mw",
    "ech_physiques": "echanges_physiques_mw", "taux_de_co2": "taux_co2_g_kwh",
    "taux_co2": "taux_co2_g_kwh",
}

# Colonnes prévues dans le schéma mais remplies par l'équipe features (vides ici).
CALENDRIER_A_REMPLIR = {
    "heure": "Int8", "jour_semaine": "Int8", "mois": "Int8", "jour_annee": "Int16",
    "semaine_iso": "Int8", "annee": "Int16", "est_weekend": "boolean",
    "est_heure_ete": "boolean", "est_ferie": "boolean", "nom_ferie": "string",
    "est_pont": "boolean",
    "vacances_zone_a": "boolean", "vacances_zone_b": "boolean", "vacances_zone_c": "boolean",
}
NETTOYAGE_A_REMPLIR = {"conso_imputee": "boolean", "conso_suspecte": "boolean"}
RETARDS_A_REMPLIR = ["conso_lag_48h", "conso_lag_168h"]
METEO_A_REMPLIR = ["temp_prevue_c", "nebulosite_prevue_pct",
                   "vent_prevu_ms", "rayonnement_prevu_wm2"]


def normaliser(nom: str) -> str:
    """'Prévision J-1 (MW)' -> 'prevision_j_1'"""
    nom = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    nom = re.sub(r"\(.*?\)", "", nom.lower())
    return re.sub(r"[^a-z0-9]+", "_", nom).strip("_")


def lire_brut() -> pd.DataFrame:
    fichiers = sorted(DOSSIER_BRUT.glob("eco2mix-national-*.csv"))
    if not fichiers:
        sys.exit(f"Aucun fichier brut dans {DOSSIER_BRUT} : lancer l'étape 1.")
    df = pd.concat([pd.read_csv(f, sep=";", low_memory=False) for f in fichiers])
    df.columns = [normaliser(c) for c in df.columns]
    # Deux téléchargements qui se chevauchent donnent les mêmes lignes : on n'en garde qu'une.
    cle = ["date", "heure"] if {"date", "heure"} <= set(df.columns) else ["date_heure"]
    df = df.drop_duplicates(subset=cle, keep="last")
    if "date_heure" not in df.columns:
        sys.exit("Colonne 'date_heure' absente : format de fichier inattendu.")
    df["date_heure"] = pd.to_datetime(df["date_heure"], utc=True)
    return df


def retirer_heures_fictives(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Supprime les lignes que RTE ajoute pour l'heure qui n'existe pas au
    passage à l'heure d'été (ex. 31/03/2019 de 02:00 à 02:45, heure de Paris).

    RTE leur donne le même horodatage UTC que les vraies lignes de 03:00 à 03:45,
    avec des valeurs recopiées : les garder fausserait la moyenne horaire.
    """
    if not {"date", "heure"} <= set(df.columns):
        return df, 0
    local = pd.to_datetime(df["date"].astype(str) + " " + df["heure"].astype(str))
    inexistante = local.dt.tz_localize(
        TZ, ambiguous=np.ones(len(df), dtype=bool), nonexistent="NaT").isna()
    return df[~inexistante.to_numpy()], int(inexistante.sum())


def vers_horaire(df: pd.DataFrame, correspondance: dict) -> pd.DataFrame:
    cols = {src: dst for src, dst in correspondance.items() if src in df.columns}
    sous = df[["date_heure", *cols]].rename(columns=cols)
    sous = sous.T.groupby(level=0).first().T  # fusionne les alias éventuels
    sous["date_heure"] = pd.to_datetime(sous["date_heure"], utc=True)
    valeurs = [c for c in sous.columns if c != "date_heure"]
    sous[valeurs] = sous[valeurs].apply(pd.to_numeric, errors="coerce")
    sous["heure_utc"] = sous["date_heure"].dt.floor(PAS)
    return sous.groupby("heure_utc")[valeurs].mean()


def main() -> None:
    DOSSIER_BASE.mkdir(parents=True, exist_ok=True)
    brut, nb_fictives = retirer_heures_fictives(lire_brut())
    pas = brut["date_heure"].sort_values().diff().dt.total_seconds().div(60)
    constat = [
        f"Lignes fictives retirées (heure inexistante au passage à l'heure d'été) : {nb_fictives}",
        f"Lignes brutes gardées : {len(brut)}",
        f"Horodatages en double dans le brut : {brut.duplicated(subset='date_heure').sum()}",
        f"Pas de temps trouvés (min) : {pas.value_counts().head(4).to_dict()}",
    ]

    index = pd.date_range(DEBUT, FIN, freq=PAS, tz="UTC", name="heure_utc")
    base = vers_horaire(brut, COLONNES_CONSO).reindex(index)
    local = base.index.tz_convert(TZ)
    base["heure_locale"] = local.tz_localize(None)
    base["split"] = pd.Series(local.year, index=base.index).map(DECOUPAGE).astype("category")

    for col, type_ in {**NETTOYAGE_A_REMPLIR, **CALENDRIER_A_REMPLIR}.items():
        base[col] = pd.Series(pd.NA, index=base.index, dtype=type_)
    for col in RETARDS_A_REMPLIR + METEO_A_REMPLIR:
        base[col] = np.float32(np.nan)

    ordre = (["heure_locale", "split", "conso_mw", *NETTOYAGE_A_REMPLIR,
              "prevision_rte_j1_mw", "prevision_rte_j_mw",
              *RETARDS_A_REMPLIR, *CALENDRIER_A_REMPLIR, *METEO_A_REMPLIR])
    base = base[[c for c in ordre if c in base.columns]]
    mw = [c for c in base.columns if c.endswith("_mw") or c.startswith("conso_lag")]
    base[mw] = base[mw].astype("float32")

    # Constat (on ne corrige rien)
    jours_locaux = base.groupby(base["heure_locale"].dt.date).size()
    constat += [
        f"Heures dans la base : {len(base)} "
        f"({base.index[0]} -> {base.index[-1]} UTC)",
        f"Heures sans conso : {base['conso_mw'].isna().sum()}",
        f"Conso min / moy / max : {base['conso_mw'].min():.0f} / "
        f"{base['conso_mw'].mean():.0f} / {base['conso_mw'].max():.0f} MW",
        "Jours de 23 h (passage à l'heure d'été) : "
        f"{[str(d) for d in jours_locaux[jours_locaux == 23].index]}",
        "Jours de 25 h (passage à l'heure d'hiver) : "
        f"{[str(d) for d in jours_locaux[jours_locaux == 25].index]}",
        f"Heures par split : {base['split'].value_counts().sort_index().to_dict()}",
    ]
    for annee, n in base.groupby(local.year)["conso_mw"].apply(
            lambda s: s.isna().sum()).items():
        constat.append(f"  {annee} : {n} h sans conso")

    base.to_parquet(DOSSIER_BASE / "base_power_horaire.parquet")
    base.to_csv(DOSSIER_BASE / "base_power_horaire.csv", sep=";", decimal=",")

    filieres = vers_horaire(brut, COLONNES_FILIERES).reindex(base.index)
    filieres = filieres.astype("float32")
    filieres.insert(0, "heure_locale", base["heure_locale"])
    filieres.to_parquet(DOSSIER_BASE / "production_filieres_horaire.parquet")

    (DOSSIER_BASE / "etat_du_brut.txt").write_text("\n".join(constat) + "\n",
                                                    encoding="utf-8")
    print("\n".join(constat))


if __name__ == "__main__":
    main()
