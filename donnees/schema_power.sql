-- Schéma de la base POWER (PostgreSQL)
-- Règle : une table par source de données, toutes reliées par (heure_utc, code_region).
-- Chaque membre n'écrit que dans ses tables ; la vue v_base_modele assemble le tout.
--   maxou        : region, conso_rte, production_filiere
--   Yassine      : conso_nettoyee
--   Amine / Hugo : calendrier, meteo_prevue, evenement (colonnes à compléter librement)

-- Référentiel des zones : 'FR' = France entière, puis les 12 régions métropolitaines.
CREATE TABLE IF NOT EXISTS region (
    code_region     TEXT PRIMARY KEY,          -- 'FR', 'IDF', 'ARA', ...
    nom             TEXT NOT NULL,             -- 'France', 'Île-de-France', ...
    zone_vacances   CHAR(1),                   -- 'A', 'B' ou 'C' (NULL pour 'FR')
    population      INTEGER                    -- pour pondérer la météo
);

-- [maxou] Consommation RTE éCO2mix, mise au pas horaire, NON corrigée.
CREATE TABLE IF NOT EXISTS conso_rte (
    heure_utc           TIMESTAMPTZ NOT NULL,  -- toujours en UTC
    code_region         TEXT NOT NULL REFERENCES region,
    conso_mw            REAL,                  -- cible (NULL si RTE n'a rien publié)
    prevision_j1_mw     REAL,                  -- prévision RTE de la veille : score à battre
    prevision_j_mw      REAL,                  -- prévision RTE du jour même
    nature              TEXT,                  -- 'définitive', 'consolidée', 'temps réel'
    nb_points           SMALLINT,              -- nb de pas de 15/30 min moyennés dans l'heure
    PRIMARY KEY (heure_utc, code_region)
);

-- [maxou] Production par filière (même fichier RTE). Format « long » :
-- une ligne par filière, donc une nouvelle filière n'oblige pas à changer la table.
-- Sert au dashboard, PAS aux modèles (inconnue la veille).
CREATE TABLE IF NOT EXISTS production_filiere (
    heure_utc       TIMESTAMPTZ NOT NULL,
    code_region     TEXT NOT NULL REFERENCES region,
    filiere         TEXT NOT NULL,             -- 'nucleaire', 'eolien', 'solaire', ...
    production_mw   REAL,
    PRIMARY KEY (heure_utc, code_region, filiere)
);

-- [Yassine] Conso après nettoyage (trous, doublons, valeurs aberrantes).
CREATE TABLE IF NOT EXISTS conso_nettoyee (
    heure_utc           TIMESTAMPTZ NOT NULL,
    code_region         TEXT NOT NULL,
    conso_mw            REAL,
    methode             TEXT,                  -- 'aucune', 'interpolation', 'imputation', ...
    est_aberrante       BOOLEAN,
    PRIMARY KEY (heure_utc, code_region),
    FOREIGN KEY (heure_utc, code_region) REFERENCES conso_rte
);

-- [Amine / Hugo] Calendrier : identique pour toutes les régions, d'où la clé heure seule.
-- Colonnes données à titre d'exemple, à compléter.
CREATE TABLE IF NOT EXISTS calendrier (
    heure_utc           TIMESTAMPTZ PRIMARY KEY,
    heure_locale        TIMESTAMP NOT NULL,    -- heure de Paris
    est_ferie           BOOLEAN,
    est_pont            BOOLEAN,
    vacances_zone_a     BOOLEAN,
    vacances_zone_b     BOOLEAN,
    vacances_zone_c     BOOLEAN
    -- ... autres variables calendaires
);

-- [Amine / Hugo] Météo PRÉVUE (pas mesurée), par région ; 'FR' = moyenne pondérée.
CREATE TABLE IF NOT EXISTS meteo_prevue (
    heure_utc           TIMESTAMPTZ NOT NULL,
    code_region         TEXT NOT NULL REFERENCES region,
    temperature_c       REAL,
    vent_ms             REAL,
    nebulosite_pct      REAL,
    rayonnement_wm2     REAL,
    -- ... autres variables météo
    PRIMARY KEY (heure_utc, code_region)
);

-- [Amine / Hugo] Événements et ruptures de série, stockés comme des PÉRIODES
-- (un confinement = 1 ligne, pas 1 300 lignes horaires).
CREATE TABLE IF NOT EXISTS evenement (
    id              SERIAL PRIMARY KEY,
    type            TEXT NOT NULL,             -- 'confinement', 'sobriete', 'greve', 'vague_froid', ...
    debut_utc       TIMESTAMPTZ NOT NULL,
    fin_utc         TIMESTAMPTZ NOT NULL,
    code_region     TEXT REFERENCES region,    -- NULL = toute la France
    description     TEXT
);

-- Table finale lue par CatBoost, LightGBM et XGBoost. Rien n'y est stocké :
-- elle est recalculée à chaque lecture, donc toujours à jour.
CREATE OR REPLACE VIEW v_base_modele AS
SELECT
    c.heure_utc,
    c.code_region,
    COALESCE(n.conso_mw, c.conso_mw)    AS conso_mw,
    c.prevision_j1_mw,
    k.heure_locale, k.est_ferie, k.est_pont,
    k.vacances_zone_a, k.vacances_zone_b, k.vacances_zone_c,
    m.temperature_c, m.vent_ms, m.nebulosite_pct, m.rayonnement_wm2,
    EXISTS (SELECT 1 FROM evenement e
            WHERE c.heure_utc >= e.debut_utc AND c.heure_utc < e.fin_utc
              AND (e.code_region IS NULL OR e.code_region = c.code_region)
              AND e.type = 'confinement')  AS en_confinement
FROM conso_rte c
LEFT JOIN conso_nettoyee n USING (heure_utc, code_region)
LEFT JOIN calendrier     k USING (heure_utc)
LEFT JOIN meteo_prevue   m USING (heure_utc, code_region);
