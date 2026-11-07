"""
Étape 5 : nettoyer la consommation horaire et remplir la table conso_nettoyee.

Entrée  : table conso_rte (code_region = 'FR') de la base PostgreSQL du groupe
          ou, avec --source parquet, donnees/base/base_power_horaire.parquet.
Sorties : table conso_nettoyee      (une ligne par heure, créée ou mise à jour)
          donnees/base/conso_nettoyee_horaire.parquet
          donnees/base/etat_nettoyage.txt   (constat : ce qui a été comblé, ce qui est suspect)

Ce que fait le script (détails dans donnees/nettoyage-encodage.md) :
1. Repère les heures sans conso. Les 14 heures attendues sont celles que RTE ne publie
   pas au passage à l'heure d'hiver : 00:00 UTC le dernier dimanche d'octobre, 2012 à 2025.
2. Comble les trous courts (<= 3 h) par interpolation linéaire. Les trous plus longs
   sont laissés vides et signalés : inventer plusieurs heures de suite serait trop risqué.
3. Repère les valeurs suspectes (hors bornes physiques, pics isolés) SANS les modifier :
   elles sont seulement marquées (est_aberrante) et listées dans le constat.
4. Écrit le résultat dans conso_nettoyee : une ligne par heure, methode = 'aucune' pour
   les heures non touchées, 'interpolation' pour celles qui ont été comblées.

Pourquoi une ligne par heure, et jamais de NULL pour « supprimer » une valeur : la vue
v_base_modele lit COALESCE(conso_nettoyee.conso_mw, conso_rte.conso_mw). Un NULL dans
conso_nettoyee ferait donc réapparaître la valeur brute.

Le script peut être relancé autant de fois que voulu (mise à jour, jamais de doublon).

Usage (depuis le dossier POWER) :
    python code/donnees/05_nettoyer_conso.py --sans-ecriture   # constat seulement, rien n'est écrit en base
    python code/donnees/05_nettoyer_conso.py                   # constat + écriture dans conso_nettoyee
    python code/donnees/05_nettoyer_conso.py --source parquet  # hors ligne, sans toucher à la base
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_BASE = RACINE / "donnees" / "base"
REGION = "FR"
TZ = "Europe/Paris"

TROU_MAX_H = 3                   # trous plus longs : laissés vides
BORNES_MW = (15_000, 110_000)    # hors de cette plage, la conso française est forcément fausse
SEUIL_PIC = 8.0                  # pic isolé suspect au-delà de 8 « écarts habituels » (robustes)
NB_HEURES_HIVER_ATTENDUES = 14   # un passage à l'heure d'hiver par an, 2012 à 2025


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


def remplacer(cur, table: str, cle: list[str], colonnes: list[str], lignes) -> int:
    """Copie les lignes dans une table temporaire puis les insère dans `table`,
    en mettant à jour celles qui existent déjà (pas de doublon possible)."""
    cur.execute(f"CREATE TEMP TABLE tmp (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DROP")
    with cur.copy(f"COPY tmp ({', '.join(colonnes)}) FROM STDIN") as copie:
        for ligne in lignes:
            copie.write_row(ligne)
    maj = ", ".join(f"{c} = EXCLUDED.{c}" for c in colonnes if c not in cle)
    cur.execute(
        f"INSERT INTO {table} ({', '.join(colonnes)}) "
        f"SELECT {', '.join(colonnes)} FROM tmp "
        f"ON CONFLICT ({', '.join(cle)}) DO UPDATE SET {maj}")
    n = cur.rowcount
    cur.execute("DROP TABLE tmp")
    return n


# ---------------------------------------------------------------- 1. lecture

def lire_depuis_base() -> pd.Series:
    import psycopg
    with psycopg.connect(adresse_base()) as conn, conn.cursor() as cur:
        cur.execute("SELECT heure_utc, conso_mw FROM conso_rte "
                    "WHERE code_region = %s ORDER BY heure_utc", (REGION,))
        lignes = cur.fetchall()
    df = pd.DataFrame(lignes, columns=["heure_utc", "conso_mw"])
    df["heure_utc"] = pd.to_datetime(df["heure_utc"], utc=True)
    return pd.to_numeric(df.set_index("heure_utc")["conso_mw"], errors="coerce").astype("float64")


def lire_depuis_parquet() -> pd.Series:
    chemin = DOSSIER_BASE / "base_power_horaire.parquet"
    if not chemin.exists():
        sys.exit(f"{chemin} introuvable : lancer l'étape 2.")
    base = pd.read_parquet(chemin, columns=["conso_mw"])
    return base["conso_mw"].astype("float64")


def verifier_serie_horaire(s: pd.Series) -> None:
    """Tout le script suppose une ligne par heure, sans trou ni doublon dans l'index."""
    pas = s.index.to_series().diff().dropna()
    if not s.index.is_unique or not s.index.is_monotonic_increasing or (pas != pd.Timedelta("1h")).any():
        sys.exit("L'index n'est pas une suite horaire complète : relancer les étapes 2 et 3.")


# ---------------------------------------------------------------- 2. trous

def est_heure_hiver(t: pd.Timestamp) -> bool:
    """00:00 UTC le dernier dimanche d'octobre = l'heure que RTE ne publie pas
    (l'heure locale 02:xx existe deux fois, RTE ne la publie qu'une fois)."""
    return t.hour == 0 and t.month == 10 and t.weekday() == 6 and t.day >= 25


def lister_trous(s: pd.Series) -> pd.DataFrame:
    """Un trou = suite d'heures consécutives sans conso."""
    vide = s.isna()
    numero = (vide != vide.shift(fill_value=False)).cumsum()
    lignes = []
    for _, g in s[vide].groupby(numero[vide]):
        debut = g.index[0]
        lignes.append({"debut_utc": debut, "heures": len(g),
                       "heure_hiver": len(g) == 1 and est_heure_hiver(debut)})
    return pd.DataFrame(lignes, columns=["debut_utc", "heures", "heure_hiver"])


def combler(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Interpolation linéaire des trous de TROU_MAX_H heures ou moins.
    Renvoie la série comblée et le masque des heures comblées."""
    vide = s.isna()
    longueur = vide.groupby((~vide).cumsum()).transform("sum")   # taille du trou de chaque heure vide
    interp = s.interpolate(method="linear", limit_area="inside")  # pas de comblement aux extrémités
    comble = vide & (longueur <= TROU_MAX_H) & interp.notna()
    return s.where(~comble, interp), comble


# ---------------------------------------------------------------- 3. valeurs suspectes

def reperer_suspects(s: pd.Series, comble: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Marque (sans corriger) les valeurs suspectes.

    - hors bornes : conso < 15 GW ou > 110 GW ;
    - pic isolé : l'écart entre une heure et la moyenne de ses deux voisines est
      anormalement grand pour cette heure de la journée (heure de Paris). L'échelle est
      robuste (médiane des écarts, MAD) pour que les pics ne gonflent pas leur propre seuil.
      Un vrai pic crée aussi un écart (moitié moins grand) sur ses deux voisines : on ne
      garde que le maximum local.
    """
    h = np.asarray(s.index.tz_convert(TZ).hour)
    hors_bornes = ((s < BORNES_MW[0]) | (s > BORNES_MW[1])).fillna(False)

    ecart = s - (s.shift(1) + s.shift(-1)) / 2
    typique = ecart.groupby(h).transform("median")
    echelle = 1.4826 * (ecart - typique).abs().groupby(h).transform("median")
    z = (ecart - typique) / echelle.replace(0, np.nan)
    az = z.abs()
    pic = ((az > SEUIL_PIC)
           & (az >= az.shift(1).fillna(0))
           & (az >= az.shift(-1).fillna(0)))

    suspect = (hors_bornes | pic) & ~comble & s.notna()
    return z, suspect, hors_bornes, pic


# ---------------------------------------------------------------- 4. constat

def valeur(s: pd.Series, t: pd.Timestamp) -> float:
    return float(s.get(t, np.nan))


def rediger_constat(brute, propre, trous_avant, comble, z, suspect, hors_bornes, pic) -> list[str]:
    c = ["=== Constat du nettoyage de la conso (région FR) ===",
         f"Heures dans la série : {len(brute)} ({brute.index[0]} -> {brute.index[-1]} UTC)",
         "", "[1] Trous avant nettoyage",
         f"Heures sans conso : {int(brute.isna().sum())}"]
    hiver = trous_avant[trous_avant["heure_hiver"]]
    autres = trous_avant[~trous_avant["heure_hiver"]]
    c.append(f"  dont heures « passage à l'heure d'hiver » (00:00 UTC, dernier dimanche d'octobre) : {len(hiver)} "
             f"(attendu : {NB_HEURES_HIVER_ATTENDUES})")
    if len(hiver) != NB_HEURES_HIVER_ATTENDUES:
        c.append("  ATTENTION : ce n'est pas le nombre attendu, regarder la liste ci-dessous.")
    c.append(f"  dont autres trous : {len(autres)} trou(s), {int(autres['heures'].sum())} heure(s)")
    for _, r in autres.head(30).iterrows():
        c.append(f"    {r.debut_utc} : {r.heures} h")

    c += ["", f"[2] Comblement (interpolation linéaire, trous de {TROU_MAX_H} h ou moins)",
          f"Heures comblées : {int(comble.sum())}",
          f"Heures encore vides : {int(propre.isna().sum())}"]
    if comble.any():
        c.append("  heure_utc | valeur comblée | heure avant | heure après | même heure 7 j avant (écart)")
        for t in propre.index[comble][:30]:
            v, av, ap = valeur(propre, t), valeur(propre, t - pd.Timedelta("1h")), valeur(propre, t + pd.Timedelta("1h"))
            j7 = valeur(propre, t - pd.Timedelta("168h"))
            ecart = f"{(v / j7 - 1) * 100:+.1f} %" if np.isfinite(j7) and j7 else "n/a"
            c.append(f"    {t} | {v:.0f} | {av:.0f} | {ap:.0f} | {j7:.0f} ({ecart})")

    c += ["", "[3] Valeurs suspectes (marquées, NON modifiées)",
          f"Hors bornes ({BORNES_MW[0]} - {BORNES_MW[1]} MW) : {int((hors_bornes & suspect).sum())}",
          f"Pics isolés (> {SEUIL_PIC:g} écarts robustes) : {int((pic & suspect).sum())}"]
    if suspect.any():
        c.append("  heure_utc | heure Paris | conso (MW) | score")
        suspects = suspect[suspect].index
        # les valeurs hors bornes n'ont pas forcément de score : on les met en tête
        ordre = z.abs().reindex(suspects).fillna(np.inf).sort_values(ascending=False).index
        for t in ordre[:30]:
            score = f"{z[t]:+.1f}" if np.isfinite(z[t]) else "hors bornes"
            c.append(f"    {t} | {t.tz_convert(TZ):%Y-%m-%d %H:%M} | {valeur(propre, t):.0f} | {score}")
    c += ["", f"Conso min / moy / max après nettoyage : {propre.min():.0f} / {propre.mean():.0f} / {propre.max():.0f} MW"]
    return c


# ---------------------------------------------------------------- 5. écriture en base

def ecrire_dans_base(sortie: pd.DataFrame) -> list[str]:
    import psycopg
    colonnes = ["heure_utc", "code_region", "conso_mw", "methode", "est_aberrante"]
    lignes = ((h, REGION, None if pd.isna(v) else float(v), m, bool(a))
              for h, v, m, a in zip(sortie.index, sortie["conso_mw"], sortie["methode"],
                                    sortie["est_aberrante"]))
    with psycopg.connect(adresse_base()) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('conso_nettoyee'), to_regclass('v_base_modele')")
        table, vue = cur.fetchone()
        if table is None:
            sys.exit("La table conso_nettoyee n'existe pas : lancer d'abord l'étape 3.")
        n = remplacer(cur, "conso_nettoyee", ["heure_utc", "code_region"], colonnes, lignes)
        cur.execute("SELECT count(*), count(*) FILTER (WHERE methode <> 'aucune'), "
                    "count(*) FILTER (WHERE est_aberrante) FROM conso_nettoyee WHERE code_region = %s",
                    (REGION,))
        total, comblees, suspectes = cur.fetchone()
        verif = [f"conso_nettoyee : {n} lignes écrites ; dans la table : {total} heures, "
                 f"{comblees} comblées, {suspectes} suspectes"]
        if vue is not None:
            cur.execute("SELECT count(*), count(*) FILTER (WHERE conso_mw IS NULL) "
                        "FROM v_base_modele WHERE code_region = %s", (REGION,))
            lues, sans_conso = cur.fetchone()
            verif.append(f"v_base_modele : {lues} heures, dont {sans_conso} sans conso")
    return verif


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=["base", "parquet"], default="base",
                   help="d'où lire la conso brute (défaut : base PostgreSQL)")
    p.add_argument("--sans-ecriture", action="store_true",
                   help="ne rien écrire dans PostgreSQL (le constat et le parquet sont quand même produits)")
    a = p.parse_args()

    brute = lire_depuis_base() if a.source == "base" else lire_depuis_parquet()
    verifier_serie_horaire(brute)

    trous_avant = lister_trous(brute)
    propre, comble = combler(brute)
    z, suspect, hors_bornes, pic = reperer_suspects(propre, comble)

    sortie = pd.DataFrame({
        "conso_mw": propre.astype("float32"),
        "methode": np.where(comble, "interpolation", "aucune"),
        "est_aberrante": suspect,
    })
    DOSSIER_BASE.mkdir(parents=True, exist_ok=True)
    sortie.to_parquet(DOSSIER_BASE / "conso_nettoyee_horaire.parquet")

    constat = rediger_constat(brute, propre, trous_avant, comble, z, suspect, hors_bornes, pic)
    if a.source == "base" and not a.sans_ecriture:
        constat += ["", "[4] Écriture dans PostgreSQL"] + ecrire_dans_base(sortie)
    else:
        constat += ["", "[4] Rien écrit dans PostgreSQL "
                        "(--sans-ecriture ou --source parquet) : relancer sans cette option pour écrire."]

    (DOSSIER_BASE / "etat_nettoyage.txt").write_text("\n".join(constat) + "\n", encoding="utf-8")
    print("\n".join(constat))


if __name__ == "__main__":
    main()
