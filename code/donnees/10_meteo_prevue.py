"""
Étape 10 : météo PRÉVUE la veille, pour la validation (2024) et le test (2025).

Pourquoi : le modèle s'entraîne sur la météo mesurée (04_meteo_meteofrance.py). Pour
prédire le lendemain, on ne connaît que la prévision météo. Pour un score honnête,
la validation et le test doivent donc utiliser la météo prévue la veille.

Source : Open-Meteo, API des runs précédents (« Previous Runs API »), sans clé.
Variable `<nom>_previous_day1` : la prévision du modèle faite environ un jour avant
(échéances 24 à 47 h de chaque run, recollées en une série horaire). Aucune archive
gratuite de ce type n'existe avant 2024 : le train garde la météo mesurée.

Mêmes stations, mêmes unités et même moyenne France pondérée par la population
que la météo mesurée : les deux fichiers sont interchangeables colonne pour colonne.
  temperature_c       °C, instantanée à l'heure
  humidite_pct        %, instantanée
  vent_ms             m/s à 10 m
  rayonnement_wm2     W/m², moyenne sur l'heure
  ensoleillement_min  minutes de soleil dans l'heure
  precipitation_mm    mm dans l'heure
Les variables cumulées sont recalées sur le début de l'heure (h -> h+1), comme la base.

Sorties (donnees/base/, hors Git) :
  meteo_prevue_horaire.parquet   par région + 'FR', même format long que meteo_observee_horaire
  meteo_prevue_fr_horaire.csv    France seule, prêt à joindre
  etat_meteo_prevue.txt          heures couvertes + écart prévue / mesurée (France)

Usage (depuis le dossier POWER) :
    python code/donnees/10_meteo_prevue.py
    python code/donnees/10_meteo_prevue.py --modele ecmwf_ifs025     # un seul modèle
    python code/donnees/10_meteo_prevue.py --sans-telechargement      # relit le brut
"""

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_BRUT = RACINE / "donnees" / "brut" / "meteo_prevue"
DOSSIER_BASE = RACINE / "donnees" / "base"
STATIONS = RACINE / "donnees" / "stations_meteo.csv"
OBSERVEE = DOSSIER_BASE / "meteo_observee_horaire.parquet"

API = "https://previous-runs-api.open-meteo.com/v1/forecast"
DEBUT = pd.Timestamp("2024-01-01 00:00", tz="UTC")     # validation + test
FIN = pd.Timestamp("2025-12-31 23:00", tz="UTC")

# Open-Meteo -> notre nom. Cumulées : valeur à t = cumul de t-1 à t.
VARIABLES = {
    "temperature_2m": "temperature_c",
    "relative_humidity_2m": "humidite_pct",
    "wind_speed_10m": "vent_ms",
    "shortwave_radiation": "rayonnement_wm2",
    "sunshine_duration": "ensoleillement_min",
    "precipitation": "precipitation_mm",
}
CUMULEES = {"shortwave_radiation", "sunshine_duration", "precipitation"}


def telecharger(st, var: str, jour: int, modele: str | None, fichier: Path) -> None:
    params = {"latitude": st.lat, "longitude": st.lon,
              "hourly": f"{var}_previous_day{jour}",
              "start_date": (DEBUT - pd.Timedelta(days=1)).date().isoformat(),
              "end_date": (FIN + pd.Timedelta(days=1)).date().isoformat(),
              "timezone": "GMT", "wind_speed_unit": "ms"}
    if modele:
        params["models"] = modele
    r = requests.get(API, params=params, timeout=120)
    if r.status_code != 200:
        print(f"    {var} : refusé ({r.status_code}) {r.text[:200]}")
        return
    fichier.write_text(r.text, encoding="utf-8")
    time.sleep(0.5)                                     # reste sous la limite gratuite


def lire(fichier: Path, var: str, jour: int) -> pd.Series | None:
    if not fichier.exists():
        return None
    h = json.loads(fichier.read_text(encoding="utf-8")).get("hourly", {})
    cle = next((k for k in h if k.startswith(var)), None)
    if cle is None:
        return None
    s = pd.Series(h[cle], index=pd.to_datetime(h["time"], utc=True), dtype="float64")
    if var == "sunshine_duration":
        s = s / 60                                      # secondes -> minutes
    if var in CUMULEES:
        s = s.shift(-1)                                 # cumul t-1 -> t  =>  début de l'heure
    return s


def moyenne_ponderee(par_region: dict[str, pd.DataFrame], poids: pd.Series) -> pd.DataFrame:
    """Même calcul que 04_meteo_meteofrance.py : repondérée si une région manque."""
    noms = next(iter(par_region.values())).columns
    resultat = {}
    for nom in noms:
        valeurs = pd.DataFrame({r: d[nom] for r, d in par_region.items()})
        w = valeurs.notna().mul(poids[valeurs.columns], axis=1)
        resultat[nom] = (valeurs.fillna(0) * w).sum(axis=1) / w.sum(axis=1).replace(0, np.nan)
    return pd.DataFrame(resultat).astype("float32")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--modele", default=None, help="modèle Open-Meteo (défaut : best_match)")
    p.add_argument("--jour", type=int, default=1, help="échéance en jours (1 = prévision de la veille)")
    p.add_argument("--sans-telechargement", action="store_true")
    a = p.parse_args()

    stations = pd.read_csv(STATIONS, sep=";", dtype={"num_poste": str})
    index = pd.date_range(DEBUT, FIN, freq="h", name="heure_utc")
    DOSSIER_BRUT.mkdir(parents=True, exist_ok=True)
    DOSSIER_BASE.mkdir(parents=True, exist_ok=True)
    suffixe = f"{a.modele or 'best_match'}_j{a.jour}"

    par_region = {}
    for st in stations.itertuples():
        print(f"{st.code_region} : {st.nom_station}")
        colonnes = {}
        for var, nom in VARIABLES.items():
            fichier = DOSSIER_BRUT / f"{st.code_region}_{var}_{suffixe}.json"
            if not a.sans_telechargement:
                telecharger(st, var, a.jour, a.modele, fichier)
            s = lire(fichier, var, a.jour)
            colonnes[nom] = s.reindex(index) if s is not None else pd.Series(np.nan, index=index)
        par_region[st.code_region] = pd.DataFrame(colonnes, index=index).astype("float32")

    poids = stations.groupby("code_region")["population_milliers"].first()
    france = moyenne_ponderee(par_region, poids)
    longue = pd.concat([d.assign(code_region=r) for r, d in {**par_region, "FR": france}.items()])
    longue = longue.reset_index().set_index(["heure_utc", "code_region"]).sort_index()
    longue.to_parquet(DOSSIER_BASE / "meteo_prevue_horaire.parquet")
    france.to_csv(DOSSIER_BASE / "meteo_prevue_fr_horaire.csv", sep=";", decimal=",")

    constat = [f"Météo prévue la veille (Open-Meteo, modèle {a.modele or 'best_match'}, "
               f"échéance J-{a.jour})",
               f"Période : {index[0]} -> {index[-1]} ({len(index)} heures)", "",
               "[FR] heures couvertes par variable :"]
    for nom in france.columns:
        ok = france[nom].notna()
        debut = france.index[ok][0] if ok.any() else "aucune"
        constat.append(f"    {nom:<20} {ok.mean():6.1%}   première heure : {debut}")

    if OBSERVEE.exists():
        obs = pd.read_parquet(OBSERVEE).xs("FR", level="code_region").reindex(index)
        constat += ["", "[FR] écart prévue - mesurée (erreur de la prévision météo) :",
                    f"    {'variable':<20}{'MAE':>8}{'biais':>8}"]
        for nom in france.columns:
            e = (france[nom] - obs[nom]).dropna()
            if len(e):
                constat.append(f"    {nom:<20}{e.abs().mean():8.2f}{e.mean():8.2f}")
        t = (france["temperature_c"] - obs["temperature_c"]).dropna()
        if len(t):
            hiver = t[t.index.month.isin([12, 1, 2])]
            constat.append(f"    température, hiver seulement : MAE {hiver.abs().mean():.2f} °C")
    (DOSSIER_BASE / "etat_meteo_prevue.txt").write_text("\n".join(constat) + "\n", encoding="utf-8")
    print("\n".join(constat))


if __name__ == "__main__":
    main()
