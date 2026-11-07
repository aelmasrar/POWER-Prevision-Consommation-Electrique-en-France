"""
Étape 6 : encoder les variables pour les modèles (XGBoost, LightGBM, CatBoost).

Entrée  : la vue v_base_modele (région 'FR') + les indicateurs de nettoyage de conso_nettoyee,
          ou, avec --entree, un fichier .parquet / .csv ayant les mêmes colonnes que la vue.
Sorties : donnees/base/base_modele_encodee.parquet   une ligne par heure, prête pour les modèles
          donnees/base/colonnes_modele.json          cible, listes de variables, colonnes à ne pas utiliser
          donnees/base/etat_encodage.txt             contrôles (comptes par split, valeurs manquantes)

Ce que fait le script (détails dans donnees/nettoyage-encodage.md) :
1. Calendrier recalculé en heure de Paris à partir de heure_utc : heure, jour_semaine,
   mois, jour_annee, semaine_iso, annee, est_weekend, est_heure_ete.
2. Encodage cyclique (sin / cos) de heure, jour_semaine, mois, jour_annee, pour les modèles
   qui ne sont pas des arbres (ridge, réseau de neurones...). Les arbres n'en ont pas besoin.
3. Booléens -> entiers 0/1 (int8), NULL -> NaN : psycopg renvoie un booléen avec NULL en
   colonne « object », que XGBoost refuse (et, selon la version, les « boolean » pandas aussi).
4. nb_zones_vacances (0 à 3) : combien de zones scolaires sont en vacances.
5. Retards de conso (48 h et 168 h) calculés sur la conso NETTOYÉE, sur la grille UTC.
6. Découpage chronologique : train 2012-2023, valid 2024, test 2025 (année en heure de Paris).

Ce que le script NE fait PAS, volontairement : pas de one-hot, pas de normalisation, pas de
target encoding (voir le .md), et il ne comble aucune valeur manquante (les arbres les gèrent).

Usage (depuis le dossier POWER) :
    python code/donnees/06_encoder_features.py
    python code/donnees/06_encoder_features.py --sans-retards      # si les retards sont calculés ailleurs
    python code/donnees/06_encoder_features.py --entree donnees/base/ma_vue.parquet   # hors ligne
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_BASE = RACINE / "donnees" / "base"
REGION = "FR"
TZ = "Europe/Paris"

DECOUPAGE = {**{annee: "train" for annee in range(2012, 2024)}, 2024: "valid", 2025: "test"}
NB_ATTENDU = {"train": 105_192, "valid": 8_784, "test": 8_760}   # voir squelette-base.md

BOOLEENNES = ["est_ferie", "est_pont", "vacances_zone_a", "vacances_zone_b",
              "vacances_zone_c", "en_confinement"]
METEO = ["temperature_c", "vent_ms", "nebulosite_pct", "rayonnement_wm2"]
RETARDS_H = {"conso_lag_48h": 48, "conso_lag_168h": 168}          # jamais moins de 48 h (règle 1)
# variable -> (période, valeur de départ) ; angle = 2π (x - départ) / période
CYCLES = {"heure": (24, 0), "jour_semaine": (7, 0), "mois": (12, 1), "jour_annee": (365.25, 1)}

# Variables communes aux deux listes (déjà numériques, rien d'autre à encoder).
COMMUNES = ["est_weekend", "est_heure_ete", "est_ferie", "est_pont",
            "vacances_zone_a", "vacances_zone_b", "vacances_zone_c", "nb_zones_vacances",
            "en_confinement", *METEO, *RETARDS_H]
FEATURES_ARBRES = ["heure", "jour_semaine", "mois", "jour_annee", *COMMUNES]
FEATURES_CYCLIQUES = [f"{n}_{f}" for n in CYCLES for f in ("sin", "cos")] + COMMUNES
# Présentes dans la table mais JAMAIS comme variables d'entrée (voir le .md pour chaque raison).
HORS_FEATURES = ["heure_locale", "split", "annee", "semaine_iso", "prevision_rte_j1_mw",
                 "conso_imputee", "conso_suspecte"]


# ---------------------------------------------------------------- connexion (comme l'étape 3)

def adresse_base() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env = RACINE / ".env"
    if env.exists():
        for ligne in env.read_text(encoding="utf-8").splitlines():
            if ligne.strip().startswith("DATABASE_URL="):
                return ligne.split("=", 1)[1].strip().strip('"')
    sys.exit("DATABASE_URL introuvable : créer POWER/.env avec DATABASE_URL=postgresql://...")


# ---------------------------------------------------------------- 1. lecture

def lire_vue() -> pd.DataFrame:
    import psycopg
    requete = """
        SELECT v.*,
               COALESCE(n.methode, 'aucune') <> 'aucune' AS conso_imputee,
               COALESCE(n.est_aberrante, false)           AS conso_suspecte
        FROM v_base_modele v
        LEFT JOIN conso_nettoyee n USING (heure_utc, code_region)
        WHERE v.code_region = %s
        ORDER BY v.heure_utc"""
    with psycopg.connect(adresse_base()) as conn, conn.cursor() as cur:
        cur.execute(requete, (REGION,))
        colonnes = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=colonnes)


def lire_fichier(chemin: Path) -> pd.DataFrame:
    if chemin.suffix == ".csv":
        df = pd.read_csv(chemin, sep=";", decimal=",")
    else:
        df = pd.read_parquet(chemin)
    return df if "heure_utc" in df.columns else df.reset_index()


# ---------------------------------------------------------------- 2. encodage

def vers_binaire(s: pd.Series) -> pd.Series:
    """True/False/NULL -> 1/0/NaN. En int8 s'il n'y a aucun manquant, sinon en float32."""
    x = s.astype("boolean").astype("float32")
    return x.astype("int8") if x.notna().all() else x


def encoder(df: pd.DataFrame, avec_retards: bool = True) -> tuple[pd.DataFrame, list[str]]:
    avis: list[str] = []
    df = df.copy()
    df["heure_utc"] = pd.to_datetime(df["heure_utc"], utc=True)
    df = df.sort_values("heure_utc").set_index("heure_utc")

    pas = df.index.to_series().diff().dropna()
    if not df.index.is_unique or (pas != pd.Timedelta("1h")).any():
        sys.exit("L'index n'est pas une suite horaire complète (trou ou doublon) : vérifier les étapes 2 et 3.")

    # Heure de Paris (naïve) recalculée depuis l'UTC : c'est elle qui règle la vie des gens.
    local = df.index.tz_convert(TZ).tz_localize(None)
    if "heure_locale" in df.columns:
        diff = int((pd.to_datetime(df["heure_locale"]).to_numpy() != local.to_numpy()).sum())
        if diff:
            avis.append(f"ATTENTION : heure_locale de la vue différente de l'heure de Paris recalculée sur {diff} lignes.")

    out = pd.DataFrame(index=df.index)
    out["conso_mw"] = pd.to_numeric(df["conso_mw"], errors="coerce").astype("float32")
    out["heure_locale"] = local.to_numpy()

    # --- calendrier
    out["heure"] = local.hour.to_numpy().astype("int8")
    out["jour_semaine"] = local.dayofweek.to_numpy().astype("int8")      # 0 = lundi
    out["mois"] = local.month.to_numpy().astype("int8")
    out["jour_annee"] = local.dayofyear.to_numpy().astype("int16")
    out["semaine_iso"] = local.isocalendar()["week"].to_numpy(dtype="int8")
    out["annee"] = local.year.to_numpy().astype("int16")
    out["est_weekend"] = (out["jour_semaine"] >= 5).astype("int8")
    # Heure d'été = Paris a 2 h d'avance sur l'UTC (1 h en heure d'hiver).
    out["est_heure_ete"] = ((local - df.index.tz_localize(None)) == pd.Timedelta(hours=2)).astype("int8")

    # --- encodage cyclique
    for nom, (periode, depart) in CYCLES.items():
        angle = 2 * np.pi * (out[nom].to_numpy(dtype="float64") - depart) / periode
        out[f"{nom}_sin"] = np.sin(angle).astype("float32")
        out[f"{nom}_cos"] = np.cos(angle).astype("float32")

    # --- booléens -> entiers
    for c in BOOLEENNES:
        if c in df.columns:
            out[c] = vers_binaire(df[c])
        else:
            avis.append(f"ATTENTION : colonne {c} absente de l'entrée (ignorée).")
    zones = ["vacances_zone_a", "vacances_zone_b", "vacances_zone_c"]
    if all(c in out.columns for c in zones):
        total = out[zones].sum(axis=1, min_count=3)
        out["nb_zones_vacances"] = total.astype("int8") if total.notna().all() else total.astype("float32")

    # --- météo
    for c in METEO:
        if c in df.columns:
            out[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
        else:
            avis.append(f"ATTENTION : colonne météo {c} absente de l'entrée (ignorée).")

    # --- retards, sur la conso nettoyée, grille UTC complète : décaler de 48 lignes = décaler de 48 h
    if avec_retards:
        for nom, h in RETARDS_H.items():
            out[nom] = out["conso_mw"].shift(h)

    # --- référence RTE, découpage, indicateurs de nettoyage
    if "prevision_j1_mw" in df.columns:
        out["prevision_rte_j1_mw"] = pd.to_numeric(df["prevision_j1_mw"], errors="coerce").astype("float32")
    out["split"] = pd.Categorical(out["annee"].map(DECOUPAGE), categories=["train", "valid", "test"])
    for c in ("conso_imputee", "conso_suspecte"):
        if c in df.columns:
            out[c] = df[c].fillna(False).astype(bool)
        else:
            out[c] = False
            avis.append(f"ATTENTION : {c} absent de l'entrée, mis à False (lancer l'étape 5 puis relire la vue).")

    ordre = ["conso_mw", *[c for c in HORS_FEATURES if c in out.columns]]
    ordre += [c for c in out.columns if c not in ordre]
    return out[ordre], avis


def choisir_colonnes(out: pd.DataFrame) -> tuple[list[str], list[str], list[str]]:
    """Garde les variables présentes et non entièrement vides."""
    avis = []
    def utile(liste):
        gardees = []
        for c in liste:
            if c not in out.columns:
                continue
            if out[c].isna().all():
                avis.append(f"ATTENTION : {c} est vide sur toute la période : retirée des listes de variables.")
                continue
            gardees.append(c)
        return gardees
    arbres, cycliques = utile(FEATURES_ARBRES), utile(FEATURES_CYCLIQUES)
    return arbres, cycliques, list(dict.fromkeys(avis))   # sans doublon, ordre conservé


# ---------------------------------------------------------------- 3. contrôles

def controles(out: pd.DataFrame, arbres: list[str]) -> list[str]:
    c = ["=== Contrôles de la table encodée (région FR) ===",
         f"Lignes : {len(out)}   colonnes : {out.shape[1]}   "
         f"({out.index[0]} -> {out.index[-1]} UTC)",
         f"conso_mw manquante : {int(out['conso_mw'].isna().sum())} "
         "(attendu : 0 après l'étape 5, sauf trous de plus de 3 h signalés dans etat_nettoyage.txt)"]
    c.append("Lignes par split (attendu : " + ", ".join(f"{k} {v}" for k, v in NB_ATTENDU.items()) + ")")
    comptes = out["split"].value_counts().reindex(["train", "valid", "test"])
    for k, n in comptes.items():
        marque = "" if int(n) == NB_ATTENDU[k] else "   <-- différent de l'attendu"
        c.append(f"  {k} : {int(n)}{marque}")
    if out["split"].isna().any():
        c.append(f"  hors découpage : {int(out['split'].isna().sum())} lignes (année hors 2012-2025)")
    c.append(f"Heures comblées par split : "
             f"{out.groupby('split', observed=True)['conso_imputee'].sum().astype(int).to_dict()}")
    c.append(f"Heures suspectes par split : "
             f"{out.groupby('split', observed=True)['conso_suspecte'].sum().astype(int).to_dict()}")
    c.append("Valeurs manquantes par variable (listes « arbres ») :")
    for col in arbres:
        n = int(out[col].isna().sum())
        c.append(f"  {col} : {n}" + ("   (normal : début de 2012, pas de retard avant)" if col.startswith("conso_lag") and n else ""))
    c.append("Types : " + ", ".join(f"{t} x{n}" for t, n in out[arbres].dtypes.astype(str).value_counts().items()))
    return c


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--entree", type=Path, help="fichier .parquet/.csv au format de la vue (sinon : lecture en base)")
    p.add_argument("--sans-retards", action="store_true", help="ne pas calculer conso_lag_48h / conso_lag_168h")
    a = p.parse_args()

    brut = lire_fichier(a.entree) if a.entree else lire_vue()
    out, avis = encoder(brut, avec_retards=not a.sans_retards)
    arbres, cycliques, avis_cols = choisir_colonnes(out)

    DOSSIER_BASE.mkdir(parents=True, exist_ok=True)
    out.to_parquet(DOSSIER_BASE / "base_modele_encodee.parquet")
    (DOSSIER_BASE / "colonnes_modele.json").write_text(json.dumps({
        "cible": "conso_mw",
        "features_arbres": arbres,
        "features_cycliques": cycliques,
        "hors_features": [c for c in HORS_FEATURES if c in out.columns],
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    constat = avis + avis_cols + controles(out, arbres)
    (DOSSIER_BASE / "etat_encodage.txt").write_text("\n".join(constat) + "\n", encoding="utf-8")
    print("\n".join(constat))


if __name__ == "__main__":
    main()
