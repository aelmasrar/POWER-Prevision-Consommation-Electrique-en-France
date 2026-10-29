"""
Étape 3 : charger la base horaire dans la base PostgreSQL partagée du groupe.

Entrées : donnees/base/base_power_horaire.parquet          (sortie de l'étape 2)
          donnees/base/production_filieres_horaire.parquet
          donnees/schema_power.sql                         (création des tables)
Sorties : tables region, conso_rte et production_filiere remplies.

Le script peut être relancé autant de fois que voulu : les lignes déjà présentes
sont mises à jour, jamais dupliquées (clé heure_utc + code_region).
Il ne touche qu'aux tables de maxou ; celles des autres membres restent intactes.

Connexion : l'adresse de la base est lue dans la variable DATABASE_URL, ou dans
le fichier POWER/.env (une ligne DATABASE_URL=postgresql://...). Ce fichier
contient le mot de passe : ne jamais le mettre sur Git.

Usage (depuis le dossier POWER) :
    python code/donnees/03_charger_postgres.py
"""

import os
import sys
from pathlib import Path

import pandas as pd
import psycopg

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_BASE = RACINE / "donnees" / "base"
SCHEMA = RACINE / "donnees" / "schema_power.sql"
REGION = "FR"


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


def valeur(x):
    return None if pd.isna(x) else float(x)


def main() -> None:
    base = pd.read_parquet(DOSSIER_BASE / "base_power_horaire.parquet")
    filieres = pd.read_parquet(DOSSIER_BASE / "production_filieres_horaire.parquet")

    with psycopg.connect(adresse_base()) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
        cur.execute(
            "INSERT INTO region (code_region, nom) VALUES (%s, 'France') "
            "ON CONFLICT (code_region) DO NOTHING", (REGION,))

        n_conso = remplacer(
            cur, "conso_rte", ["heure_utc", "code_region"],
            ["heure_utc", "code_region", "conso_mw", "prevision_j1_mw", "prevision_j_mw"],
            ((h, REGION, valeur(r.conso_mw), valeur(r.prevision_rte_j1_mw),
              valeur(getattr(r, "prevision_rte_j_mw", None)))
             for h, r in zip(base.index, base.itertuples())))

        # Seules les colonnes en MW vont dans cette table (pas le taux de CO2 en g/kWh).
        longue = (filieres[[c for c in filieres.columns if c.endswith("_mw")]]
                  .rename_axis("heure_utc").reset_index()
                  .melt(id_vars="heure_utc", var_name="filiere", value_name="production_mw")
                  .dropna(subset=["production_mw"]))
        longue["filiere"] = longue["filiere"].str.removeprefix("prod_").str.removesuffix("_mw")
        n_prod = remplacer(
            cur, "production_filiere", ["heure_utc", "code_region", "filiere"],
            ["heure_utc", "code_region", "filiere", "production_mw"],
            ((h, REGION, f, float(p)) for h, f, p in longue[["heure_utc", "filiere", "production_mw"]]
             .itertuples(index=False)))

        cur.execute("SELECT count(*), count(conso_mw), min(heure_utc), max(heure_utc) "
                    "FROM conso_rte WHERE code_region = %s", (REGION,))
        total, avec_conso, debut, fin = cur.fetchone()

    print(f"conso_rte : {n_conso} lignes écrites ; dans la base : {total} heures "
          f"({total - avec_conso} sans conso), de {debut} à {fin}")
    print(f"production_filiere : {n_prod} lignes écrites")


if __name__ == "__main__":
    main()
