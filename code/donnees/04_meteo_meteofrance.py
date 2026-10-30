"""
Étape 4 : météo horaire Météo-France, par région et pour la France entière.

Source : Météo-France, « Données climatologiques de base - horaires »
(data.gouv.fr / meteo.data.gouv.fr). Un fichier csv.gz par département et par
période, toutes les stations, heures en UTC. Pas de compte ni de clé.

Méthode (détails dans donnees/meteo.md) :
1. une station de référence par région (donnees/stations_meteo.csv, modifiable) ;
2. nettoyage station par station : codes qualité « douteux », valeurs hors bornes,
   sauts impossibles, doublons, petits trous (<= 3 h) bouchés par interpolation ;
3. région = moyenne de ses stations ; France ('FR') = moyenne des 12 régions
   pondérée par leur population (repondérée si une région manque à une heure).

Attention : ce sont des mesures (météo OBSERVÉE). Le schéma prévoit une météo PRÉVUE :
Météo-France ne publie pas d'archive gratuite de ses prévisions. Voir meteo.md.

Entrées : donnees/stations_meteo.csv
Sorties : donnees/brut/meteofrance/*.csv.gz            fichiers bruts (jamais modifiés)
          donnees/base/meteo_stations_horaire.parquet  par station, nettoyé
          donnees/base/meteo_observee_horaire.parquet  par région + 'FR' (format long)
          donnees/base/meteo_fr_horaire.csv            France seule, prêt à joindre
          donnees/base/etat_meteo.txt                  constat (trous, bornes, unités)

Usage (depuis le dossier POWER) :
    python code/donnees/04_meteo_meteofrance.py
    python code/donnees/04_meteo_meteofrance.py --debut 2018-12-20 --fin 2026-01-02
    python code/donnees/04_meteo_meteofrance.py --sans-telechargement   # brut déjà là
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_BRUT = RACINE / "donnees" / "brut" / "meteofrance"
DOSSIER_BASE = RACINE / "donnees" / "base"
STATIONS = RACINE / "donnees" / "stations_meteo.csv"

JEU = "donnees-climatologiques-de-base-horaires"
API = "https://www.data.gouv.fr/api/2/datasets/{jeu}/"

# Colonne Météo-France -> (nom dans notre base, borne min, borne max)
VARIABLES = {
    "T": ("temperature_c", -30.0, 45.0),      # °C, instantanée à l'heure HH
    "U": ("humidite_pct", 0.0, 100.0),        # %, instantanée
    "FF": ("vent_ms", 0.0, 60.0),             # m/s, vent moyen sur 10 min
    "GLO": ("rayonnement_wm2", 0.0, 1400.0),  # J/cm² sur l'heure -> W/m²
    "INS": ("ensoleillement_min", 0.0, 60.0), # min de soleil dans l'heure
    "RR1": ("precipitation_mm", 0.0, 150.0),  # mm dans l'heure
}
# Valeurs cumulées sur l'heure précédente (HH-1 -> HH) : on les recale sur le
# début de l'heure, comme la conso de la base (heure_utc = début de l'heure).
CUMULEES = {"GLO", "INS", "RR1"}
CODE_DOUTEUX = 2           # code qualité Météo-France « douteuse, en vérification »
SAUT_MAX_TEMP = 8.0        # °C en 1 h : au-delà, valeur isolée considérée fausse
TROU_MAX_H = 3             # trous plus longs laissés vides (constat dans etat_meteo.txt)


# ---------------------------------------------------------------- 1. téléchargement

def lister_fichiers(departements: set[str], an_debut: int, an_fin: int) -> list[dict]:
    """Liste les fichiers du jeu Météo-France utiles (département + années)."""
    r = requests.get(API.format(jeu=JEU), timeout=60)
    r.raise_for_status()
    page = r.json()["resources"]["href"] + "&page_size=200&type=main"
    trouves = []
    while page:
        r = requests.get(page, timeout=60)
        r.raise_for_status()
        contenu = r.json()
        for res in contenu["data"]:
            texte = f"{res.get('title', '')} {res.get('url', '')}"
            dep = re.search(r"(?:departement_|/H_)(\d{2,3}|2A|2B)_", texte)
            if not dep or dep.group(1) not in departements:
                continue
            annees = re.search(r"(\d{4})-(\d{4})", texte)
            if annees and (int(annees.group(2)) < an_debut or int(annees.group(1)) > an_fin):
                continue
            trouves.append({"departement": dep.group(1), "url": res["url"],
                            "nom": Path(res["url"].split("?")[0]).name})
        page = contenu.get("next_page")
    return trouves


def telecharger(fichiers: list[dict]) -> None:
    DOSSIER_BRUT.mkdir(parents=True, exist_ok=True)
    for f in fichiers:
        sortie = DOSSIER_BRUT / f["nom"]
        print(f"  {f['nom']} ...", end=" ", flush=True)
        with requests.get(f["url"], stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(sortie, "wb") as out:
                for bloc in r.iter_content(chunk_size=1 << 20):
                    out.write(bloc)
        print(f"{sortie.stat().st_size / 1e6:.1f} Mo")


# ---------------------------------------------------------------- 2. lecture

def fichiers_du_departement(dep: str) -> list[Path]:
    motif = re.compile(rf"(?:departement_|^H_){re.escape(dep)}_")
    return sorted(p for p in DOSSIER_BRUT.glob("*.csv*") if motif.search(p.name))


def lire_departement(dep: str, num_poste: str, lat: float, lon: float,
                     debut: pd.Timestamp, fin: pd.Timestamp,
                     secours: str | None = None) -> tuple[pd.DataFrame, str, pd.DataFrame | None]:
    """Lit les fichiers d'un département et garde la station principale
    (+ la station de secours éventuelle, renvoyée à part).

    Si la station prévue n'existe pas (ou n'a pas de température sur la période),
    on prend la station la plus proche parmi celles qui ont au moins 80 % des
    mesures de température de la meilleure du département.
    """
    fichiers = fichiers_du_departement(dep)
    if not fichiers:
        sys.exit(f"Aucun fichier brut pour le département {dep} dans {DOSSIER_BRUT}.")

    utiles = {"NUM_POSTE", "NOM_USUEL", "LAT", "LON", "AAAAMMJJHH",
              *VARIABLES, *(f"Q{v}" for v in VARIABLES)}
    morceaux, stats, morceaux_secours = [], [], []
    for f in fichiers:
        for bloc in pd.read_csv(f, sep=";", usecols=lambda c: c in utiles,
                                dtype={"NUM_POSTE": str, "AAAAMMJJHH": str},
                                chunksize=500_000, low_memory=False):
            bloc["NUM_POSTE"] = bloc["NUM_POSTE"].str.zfill(8)
            bloc["heure_utc"] = pd.to_datetime(bloc["AAAAMMJJHH"], format="%Y%m%d%H", utc=True)
            bloc = bloc[(bloc["heure_utc"] >= debut) & (bloc["heure_utc"] <= fin)]
            if secours:
                morceaux_secours.append(bloc[bloc["NUM_POSTE"] == secours])
            if bloc.empty:
                continue
            stats.append(bloc.groupby("NUM_POSTE").agg(
                nom=("NOM_USUEL", "first"), lat=("LAT", "first"), lon=("LON", "first"),
                nb_t=("T", "count")))
            morceaux.append(bloc)

    if not morceaux:
        sys.exit(f"Département {dep} : aucune donnée entre {debut} et {fin}.")
    stats = (pd.concat(stats).groupby(level=0)
             .agg(nom=("nom", "first"), lat=("lat", "first"), lon=("lon", "first"),
                  nb_t=("nb_t", "sum")))

    choisie = num_poste
    if num_poste not in stats.index or stats.loc[num_poste, "nb_t"] == 0:
        candidates = stats[stats["nb_t"] >= 0.8 * stats["nb_t"].max()].copy()
        candidates["dist"] = np.hypot(candidates["lat"] - lat,
                                      (candidates["lon"] - lon) * np.cos(np.radians(lat)))
        choisie = candidates["dist"].idxmin()
        print(f"  ATTENTION : station {num_poste} absente du dép. {dep} : "
              f"remplacée par {choisie} ({stats.loc[choisie, 'nom']})")

    df = pd.concat(m[m["NUM_POSTE"] == choisie] for m in morceaux)
    df_secours = None
    if secours:
        df_secours = pd.concat(morceaux_secours) if morceaux_secours else pd.DataFrame()
        if df_secours.empty:
            print(f"  ATTENTION : station de secours {secours} introuvable dans le dép. {dep}")
            df_secours = None
    return df, f"{choisie} {stats.loc[choisie, 'nom']}", df_secours


# ---------------------------------------------------------------- 3. nettoyage

def nettoyer_station(brut: pd.DataFrame, index: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict]:
    """Une station -> série horaire propre, sur l'index complet. Renvoie aussi le constat."""
    df = brut.copy()
    constat = {"doublons": int(df.duplicated("heure_utc").sum())}
    # En cas de doublon (même heure dans deux fichiers), on garde la 1re valeur non vide.
    df = df.groupby("heure_utc").first()

    for col, (nom, mini, maxi) in VARIABLES.items():
        if col not in df.columns:
            df[col] = np.nan
            continue
        x = pd.to_numeric(df[col], errors="coerce")
        q = f"Q{col}"
        if q in df.columns:
            douteux = pd.to_numeric(df[q], errors="coerce") == CODE_DOUTEUX
            constat[f"{nom}_douteux"] = int((douteux & x.notna()).sum())
            x = x.mask(douteux)
        if col == "GLO":
            x = x * 1e4 / 3600                    # J/cm² sur 1 h -> W/m² moyen
        hors = (x < mini) | (x > maxi)
        constat[f"{nom}_hors_bornes"] = int(hors.sum())
        df[nom] = x.mask(hors)

    # Pic isolé de température : écart > SAUT_MAX_TEMP avec l'heure d'avant ET d'après.
    t = df["temperature_c"].reindex(index)
    pic = ((t - t.shift(1)).abs() > SAUT_MAX_TEMP) & ((t - t.shift(-1)).abs() > SAUT_MAX_TEMP)
    constat["temperature_c_pics"] = int(pic.sum())

    propre = pd.DataFrame(index=index)
    for col, (nom, _, _) in VARIABLES.items():
        s = df[nom].reindex(index)
        if col == "T":
            s = s.mask(pic)
        if col in CUMULEES:
            # valeur cumulée de HH-1 à HH -> rattachée à l'heure qui commence en HH-1
            s = df[nom].copy()
            s.index = s.index - pd.Timedelta(hours=1)
            s = s.reindex(index)
        trous = s.isna()
        # longueur du trou auquel appartient chaque heure vide
        groupe = (~trous).cumsum()
        longueur = trous.groupby(groupe).transform("sum")
        court = trous & (longueur <= TROU_MAX_H)
        s = s.where(~court, s.interpolate(limit_area="inside"))
        constat[f"{nom}_bouches"] = int((trous & s.notna()).sum())
        constat[f"{nom}_vides"] = int(s.isna().sum())
        propre[nom] = s.astype("float32")
    return propre, constat


# ---------------------------------------------------------------- 4. agrégation

def moyenne_ponderee(par_region: dict[str, pd.DataFrame], poids: pd.Series) -> pd.DataFrame:
    """Moyenne des régions pondérée par la population, repondérée heure par heure
    si une région n'a pas de valeur (sinon une région manquante ferait chuter la moyenne)."""
    noms = next(iter(par_region.values())).columns
    resultat = {}
    for nom in noms:
        valeurs = pd.DataFrame({r: d[nom] for r, d in par_region.items()})
        w = valeurs.notna().mul(poids[valeurs.columns], axis=1)
        resultat[nom] = (valeurs.fillna(0) * w).sum(axis=1) / w.sum(axis=1).replace(0, np.nan)
    return pd.DataFrame(resultat).astype("float32")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--debut", default="2018-12-20")   # marge pour lissages et retards
    p.add_argument("--fin", default="2026-01-02")
    p.add_argument("--sans-telechargement", action="store_true")
    a = p.parse_args()

    debut = pd.Timestamp(a.debut, tz="UTC")
    fin = pd.Timestamp(a.fin, tz="UTC") - pd.Timedelta(hours=1)
    index = pd.date_range(debut, fin, freq="h", name="heure_utc")

    stations = pd.read_csv(STATIONS, sep=";",
                           dtype={"departement": str, "num_poste": str, "num_poste_secours": str})
    stations["num_poste"] = stations["num_poste"].str.zfill(8)
    if "num_poste_secours" not in stations:
        stations["num_poste_secours"] = None
    stations["num_poste_secours"] = stations["num_poste_secours"].where(
        stations["num_poste_secours"].notna(), None)
    stations["departement"] = stations["departement"].str.zfill(2)

    if not a.sans_telechargement:
        print("Recherche des fichiers sur data.gouv.fr ...")
        fichiers = lister_fichiers(set(stations["departement"]), debut.year, fin.year)
        print(f"{len(fichiers)} fichiers à télécharger")
        telecharger(fichiers)

    DOSSIER_BASE.mkdir(parents=True, exist_ok=True)
    constat = [f"Période : {index[0]} -> {index[-1]} UTC ({len(index)} heures)", ""]
    par_station, par_region = [], {}

    for _, st in stations.iterrows():
        print(f"{st.code_region} : dép. {st.departement} ...")
        secours = st.num_poste_secours.zfill(8) if st.num_poste_secours else None
        brut, station, brut_secours = lire_departement(
            st.departement, st.num_poste, st.lat, st.lon,
            index[0] - pd.Timedelta(hours=1), index[-1] + pd.Timedelta(hours=1), secours)
        propre, c = nettoyer_station(brut, index)
        if brut_secours is not None:
            # la station de secours ne sert qu'à boucher les heures vides de la principale
            propre_secours, _ = nettoyer_station(brut_secours, index)
            for nom in propre.columns:
                vide = propre[nom].isna()
                propre.loc[vide, nom] = propre_secours.loc[vide, nom]
                c[f"{nom}_par_secours"] = int((vide & propre[nom].notna()).sum())
                c[f"{nom}_vides"] = int(propre[nom].isna().sum())
            station += f" (secours {secours})"
        par_station.append(propre.assign(code_region=st.code_region, station=station))
        constat.append(f"[{st.code_region}] station {station}")
        for _, (nom, _, _) in VARIABLES.items():
            details = [f"{k.removeprefix(nom + '_')} {v}" for k, v in c.items()
                       if k.startswith(nom) and v]
            if details:
                constat.append(f"    {nom} : " + ", ".join(details))
        if c["doublons"]:
            constat.append(f"    heures en double : {c['doublons']}")
        # moyenne si plusieurs stations pour une même région
        par_region.setdefault(st.code_region, []).append(propre)

    par_region = {r: (sum(l) / len(l) if len(l) > 1 else l[0]) for r, l in par_region.items()}
    poids = stations.groupby("code_region")["population_milliers"].first()
    france = moyenne_ponderee(par_region, poids)

    longue = pd.concat([d.assign(code_region=r) for r, d in {**par_region, "FR": france}.items()])
    longue = longue.reset_index().set_index(["heure_utc", "code_region"]).sort_index()
    longue.to_parquet(DOSSIER_BASE / "meteo_observee_horaire.parquet")
    pd.concat(par_station).to_parquet(DOSSIER_BASE / "meteo_stations_horaire.parquet")
    france.to_csv(DOSSIER_BASE / "meteo_fr_horaire.csv", sep=";", decimal=",")

    # Constat France + contrôle des unités (à vérifier à l'oeil)
    constat += ["", "[FR] moyenne pondérée par la population",
                f"    heures sans température : {france['temperature_c'].isna().sum()}"]
    for nom in france.columns:
        s = france[nom]
        constat.append(f"    {nom} : min {s.min():.1f} / moy {s.mean():.1f} / max {s.max():.1f}")
    midi_ete = france["rayonnement_wm2"][(france.index.month == 6) & (france.index.hour == 11)]
    constat += ["", "Contrôles d'unité :",
                f"    rayonnement moyen à midi en juin : {midi_ete.mean():.0f} W/m² "
                "(attendu ~500-800 ; sinon revoir la conversion de GLO)",
                f"    température moyenne : {france['temperature_c'].mean():.1f} °C (attendu ~12-13)"]

    (DOSSIER_BASE / "etat_meteo.txt").write_text("\n".join(constat) + "\n", encoding="utf-8")
    print("\n".join(constat))


if __name__ == "__main__":
    main()
