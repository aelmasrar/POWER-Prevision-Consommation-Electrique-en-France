"""LightGBM pour le projet POWER : entraînement, scores et figures.

Découpage du groupe : entraînement <= 2023, validation 2024.
2025 est le jeu de test : il n'est PAS touché ici.

Le script affiche un diagnostic des données et les scores comparés à la
prévision J-1 de RTE, puis enregistre dans `figures/` six graphiques :

  lightgbm_01_semaines.png       réel, LightGBM et RTE, 8-15 janv. et juil.
  lightgbm_02_mae_par_mois.png   erreur moyenne par mois
  lightgbm_03_mae_par_heure.png  erreur moyenne par heure de Paris
  lightgbm_04_importance.png     importance des variables, par famille
  lightgbm_05_apprentissage.png  RMSE selon le nombre d'arbres
  lightgbm_06_erreurs.png        distribution des erreurs et nuage réel / prédit

Lancer :  python train_lightgbm.py
Variante qui ajoute Tempo et evt_imprevisible (hors règle des 48 h) :
          VARIANTE=avec_tempo python train_lightgbm.py
"""

import os

import lightgbm as lgb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dotenv import load_dotenv

DATE_COL = "heure_utc"
TARGET_COL = "conso_mw"
EXCLURE = {TARGET_COL, "prevision_j1_mw", "prevision_j_mw", "code_region",
           "heure_locale", "mois_num",
           # Doublons 0/1 de tempo_couleur, qui est gardée en catégorie.
           "tempo_bleu", "tempo_blanc", "tempo_rouge"}
# Variables écartées par la règle des 48 h (connues trop tard la veille) :
#  - tempo_couleur : couleur du jour J publiée vers 10 h 30 la veille, et
#    choisie d'après la prévision de conso (elle contient donc une prévision) ;
#  - evt_imprevisible : attentats, par définition inconnus à l'avance.
# Mettre VARIANTE = "avec_tempo" pour mesurer ce qu'elles apporteraient.
FUITE_48H = {"tempo_couleur", "evt_imprevisible"}
# Seule colonne evt_* gardée. Test du 17/11/2026 (une colonne à la fois) :
# evt_energie seule fait tout le gain (MAPE 2,39 -> 1,83 %) ; evt_geopolitique
# fait doublon ; les autres evt_* n'apportent rien ou dégradent.
EVT_GARDES = {"evt_energie"}
VARIANTE = os.environ.get("VARIANTE", "standard")
# Toute colonne texte devient une catégorie : LightGBM les gère nativement
# (pas de one-hot), mais refuse le texte brut.
DOSSIER = "figures"
PREFIXE = "lightgbm"

PARAMS = dict(n_estimators=3000, learning_rate=0.03, num_leaves=63,
              min_child_samples=50, subsample=0.8, subsample_freq=1,
              colsample_bytree=0.8, random_state=42, verbose=-1)

# Couleurs : LightGBM en bleu, RTE en orange, réel en noir.
BLEU, ORANGE, NOIR, GRIS = "#3333B2", "#D9692B", "#1A1A1A", "#9A9AA8"
VERT = "#4B7B3A"

plt.rcParams.update({
    "figure.dpi": 130,
    "savefig.dpi": 160,
    "savefig.bbox": "tight",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.6,
    "legend.frameon": False,
})


def scores(y, p):
    """MAE, RMSE, R², MAPE, biais et nombre d'heures comparées.

    Calcul avec pandas sur les seules lignes où la valeur comparée existe.
    Biais = moyenne de (prédiction - réel) : négatif = sous-estimé.
    """
    p = pd.Series(np.asarray(p, dtype="float64"), index=y.index)
    m = y.notna() & p.notna()
    ecart = p[m] - y[m]
    r2 = 1 - (ecart ** 2).sum() / ((y[m] - y[m].mean()) ** 2).sum()
    return dict(mae=float(ecart.abs().mean()),
                rmse=float(np.sqrt((ecart ** 2).mean())),
                r2=float(r2),
                mape=float((ecart.abs() / y[m]).mean() * 100),
                biais=float(ecart.mean()),
                n=int(m.sum()))


def charger():
    """Lit la vue v_base_modele sur Neon."""
    from sqlalchemy import create_engine

    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL introuvable : vérifie le fichier .env")
    df = pd.read_sql(
        f"SELECT * FROM v_base_modele ORDER BY {DATE_COL} ASC",
        create_engine(url),
    )
    df[DATE_COL] = pd.to_datetime(df[DATE_COL], utc=True)
    df = df.set_index(DATE_COL).sort_index()
    print(f"{len(df)} lignes, de {df.index.min()} à {df.index.max()}")
    return df


def diagnostic(df):
    """Ce qui manque dans la base, avant toute suppression de lignes."""
    print("\n--- Valeurs manquantes par colonne ---")
    manquantes = df.isna().sum().sort_values(ascending=False)
    print(manquantes[manquantes > 0].head(10) if manquantes.any() else "aucune")
    print("\n--- Lignes complètes par année ---")
    print(df.notna().all(axis=1).groupby(df.index.year).agg(["sum", "size"]))


def preparer(df):
    """Fabrique les variables, nettoie et renvoie (df, liste des variables)."""
    # L'heure de la journée n'est pas une colonne de la vue : on la fabrique.
    # Calendrier en heure de Paris, car c'est l'heure qui règle la vie des gens.
    locale = df["heure_locale"]
    df["heure"] = locale.dt.hour
    df["jour_annee"] = locale.dt.day_of_year
    df["mois_num"] = locale.dt.month

    # Règle du projet : rien d'inconnu la veille, donc au moins 48 h de recul.
    # Grille horaire complète pour que les décalages restent justes même s'il
    # manque des heures (changements d'heure).
    grille = pd.date_range(df.index.min(), df.index.max(), freq="h", tz="UTC")
    conso = df[TARGET_COL].reindex(grille)
    for h in (48, 168):
        df[f"lag_{h}h"] = conso.shift(h).reindex(df.index)
    df["moy_7j_48h"] = (
        conso.shift(48).rolling(168, min_periods=120).mean().reindex(df.index)
    )

    # Températures lissées (comme CatBoost) : les bâtiments ont de l'inertie,
    # la conso dépend du froid des heures et des jours précédents.
    # Moyenne glissante des 24 h / 72 h précédentes, heure courante incluse.
    temp = df["temperature_c"].reindex(grille)
    for h in (24, 72):
        df[f"temp_moy_{h}h"] = (
            temp.rolling(h, min_periods=int(h * 0.75)).mean().reindex(df.index)
        )

    for col in df.select_dtypes(include=["bool"]).columns:
        df[col] = df[col].astype(int)

    # Colonnes texte -> catégories, sur TOUT le DataFrame avant le découpage
    # pour que train et validation aient les mêmes codes. Les vides deviennent
    # "aucun" (nom_ferie hors jours fériés, tempo_couleur avant sept. 2014).
    texte = [c for c in df.columns if c not in EXCLURE
             and (df[c].dtype == object or pd.api.types.is_string_dtype(df[c]))]
    for col in texte:
        df[col] = df[col].fillna("aucun").astype(str).astype("category")

    exclues = EXCLURE | (set() if VARIANTE == "avec_tempo" else FUITE_48H)
    exclues |= {c for c in df.columns
                if c.startswith("evt_") and c not in EVT_GARDES | FUITE_48H}
    features = [c for c in df.columns if c not in exclues]
    cat = [c for c in features if isinstance(df[c].dtype, pd.CategoricalDtype)]

    restantes = [c for c in features if c not in cat
                 and (df[c].dtype == object
                      or pd.api.types.is_string_dtype(df[c]))]
    assert not restantes, f"colonnes texte non converties : {restantes}"

    # Garde-fou : une colonne presque toujours vide viderait le jeu de données.
    part_vide = df[features].isna().mean()
    for col, part in part_vide[part_vide > 0.5].items():
        print(f"ATTENTION : {col} est vide à {part:.0%}. À remplir ou à exclure.")

    avant = len(df)
    df = df.dropna(subset=[TARGET_COL] + features)
    print(f"\nVariante : {VARIANTE}")
    print(f"Après suppression des lignes incomplètes : {len(df)} lignes "
          f"({avant - len(df)} retirées)")
    print(f"{len(features)} variables : {', '.join(features)}")
    print(f"Catégorielles : {', '.join(cat)}")
    return df, features, cat


def famille(nom):
    """Range une variable dans une famille, pour colorer le graphique."""
    if nom.startswith(("lag_", "moy_")):
        return "Passé de la conso"
    if nom.startswith("temp_moy_") or nom in {
            "temperature_c", "humidite_pct", "vent_ms", "rayonnement_wm2",
            "ensoleillement_min", "precipitation_mm"}:
        return "Météo"
    if nom == "en_confinement" or nom.startswith(("evt_", "tempo_")):
        return "Événements"
    return "Calendrier"


COULEUR_FAMILLE = {"Passé de la conso": ORANGE, "Météo": VERT,
                   "Calendrier": BLEU, "Événements": GRIS}


def fig_semaines(valid, pred, chemin):
    """Réel, LightGBM et RTE sur une semaine d'hiver et une d'été."""
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5))
    periodes = [("2024-01-08", "2024-01-15", "Une semaine de janvier 2024"),
                ("2024-07-08", "2024-07-15", "Une semaine de juillet 2024")]
    for ax, (debut, fin, titre) in zip(axes, periodes):
        m = (valid.index >= debut) & (valid.index < fin)
        if m.sum() == 0:
            ax.set_visible(False)
            continue
        x = valid.index[m].tz_convert("Europe/Paris")
        ax.plot(x, valid[TARGET_COL][m] / 1000, color=NOIR, lw=1.8,
                label="Réel", zorder=3)
        ax.plot(x, pred[m] / 1000, color=BLEU, lw=1.5, label="LightGBM")
        ax.plot(x, valid["prevision_j1_mw"][m] / 1000, color=ORANGE, lw=1.3,
                ls="--", label="RTE J-1")
        ax.set_title(titre, loc="left")
        ax.set_ylabel("GW")
    axes[0].legend(ncol=3, loc="upper right")
    fig.suptitle("Réel, LightGBM et prévision RTE sur deux semaines de 2024",
                 fontsize=14, fontweight="bold", y=0.98)
    fig.tight_layout()
    fig.savefig(chemin)
    plt.close(fig)


def fig_par_heure(valid, pred, chemin):
    """Erreur absolue moyenne selon l'heure de Paris."""
    heure = valid.index.tz_convert("Europe/Paris").hour
    err_m = pd.Series(np.abs(valid[TARGET_COL] - pred).values).groupby(heure).mean()
    err_r = pd.Series(np.abs(valid[TARGET_COL] - valid["prevision_j1_mw"]).values).groupby(heure).mean()

    fig, ax = plt.subplots(figsize=(10, 4.6))
    ax.plot(err_m.index, err_m.values, color=BLEU, lw=2.2, marker="o", ms=4,
            label="LightGBM")
    ax.plot(err_r.index, err_r.values, color=ORANGE, lw=1.8, marker="s", ms=4,
            ls="--", label="RTE J-1")
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel("Heure (Paris)")
    ax.set_ylabel("Erreur absolue moyenne (MW)")
    ax.set_ylim(bottom=0)
    ax.set_title("Erreur moyenne selon l'heure de la journée · validation 2024",
                 loc="left")
    ax.legend()
    fig.savefig(chemin)
    plt.close(fig)


def fig_par_mois(valid, pred, chemin):
    """Erreur absolue moyenne par mois."""
    mois = valid["mois_num"].values
    err_m = pd.Series(np.abs(valid[TARGET_COL] - pred).values).groupby(mois).mean()
    err_r = pd.Series(np.abs(valid[TARGET_COL] - valid["prevision_j1_mw"]).values).groupby(mois).mean()
    noms = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août",
            "sept.", "oct.", "nov.", "déc."]

    fig, ax = plt.subplots(figsize=(10, 4.6))
    x = np.arange(len(err_m))
    ax.bar(x - 0.2, err_m.values, 0.4, color=BLEU, label="LightGBM")
    ax.bar(x + 0.2, err_r.values, 0.4, color=ORANGE, label="RTE J-1")
    ax.set_xticks(x)
    ax.set_xticklabels([noms[i - 1] for i in err_m.index])
    ax.set_ylabel("Erreur absolue moyenne (MW)")
    ax.set_title("Erreur moyenne par mois · validation 2024", loc="left")
    ax.legend()
    fig.savefig(chemin)
    plt.close(fig)


def importance_shap(model, X):
    """Part de chaque variable dans la moyenne des |SHAP| sur X, en %.

    Même méthode que CatBoost (TreeSHAP), calculée nativement par LightGBM :
    pred_contrib renvoie une colonne par variable, plus une colonne de base.
    """
    contrib = model.predict(X, pred_contrib=True)[:, :-1]
    moy = np.abs(contrib).mean(axis=0)
    return (pd.Series(100 * moy / moy.sum(), index=X.columns)
            .sort_values(ascending=False))


def fig_importance(part, chemin, n=15):
    """Importance SHAP en % du total, colorée par famille."""
    imp = part.head(n).iloc[::-1]
    fig, ax = plt.subplots(figsize=(9.5, 6.0))
    couleurs = [COULEUR_FAMILLE[famille(v)] for v in imp.index]
    ax.barh(imp.index, imp.values, color=couleurs)
    for y, p in enumerate(imp.values):
        ax.text(p + 0.2, y, f"{p:.1f} %".replace(".", ","), va="center",
                fontsize=9, color=GRIS)
    ax.set_xlabel("Part de l'importance totale (moyenne de |SHAP| sur 2024, %)")
    ax.set_xlim(0, imp.max() * 1.18)
    ax.grid(axis="y", visible=False)
    ax.set_title(f"Sur quoi LightGBM s'appuie ({n} premières variables)",
                 loc="left")
    total = part.groupby(part.index.map(famille)).sum()
    vues = [f for f in COULEUR_FAMILLE if f in total.index]
    ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, color=COULEUR_FAMILLE[f])
                       for f in vues],
              labels=[f"{f} : {total[f]:.0f} %" for f in vues],
              title="Total par famille", loc="lower right")
    reste = part.iloc[n:].sum()
    fig.text(0.01, -0.02, f"Les {len(part) - n} autres variables pèsent "
             f"{reste:.1f} % au total.".replace(".", ",", 1),
             fontsize=9, color=GRIS)
    fig.savefig(chemin)
    plt.close(fig)


def fig_apprentissage(historique, best_iter, chemin):
    """RMSE sur l'entraînement et la validation selon le nombre d'arbres."""
    fig, ax = plt.subplots(figsize=(9, 4.6))
    for cle, (couleur, nom) in {"train": (GRIS, "entraînement"),
                                "valid": (BLEU, "validation 2024")}.items():
        if cle in historique:
            rmse = np.sqrt(np.array(historique[cle]["l2"]))
            ax.plot(np.arange(1, len(rmse) + 1), rmse, color=couleur, lw=1.8,
                    label=nom)
    # Zoom sur le plateau, sinon la chute initiale écrase tout le reste.
    fin = min(np.sqrt(np.array(h["l2"]))[-1] for h in historique.values())
    ax.set_ylim(fin * 0.85, fin * 3.0)
    n_total = max(len(h["l2"]) for h in historique.values())
    ax.axvline(best_iter, color=ORANGE, ls="--", lw=1.4)
    a_droite = best_iter < 0.7 * n_total
    ax.annotate(f"arrêt : {best_iter} arbres",
                xy=(best_iter, fin * 2.6),
                xytext=(6 if a_droite else -6, 0), textcoords="offset points",
                ha="left" if a_droite else "right", va="center",
                color=ORANGE, fontsize=10)
    ax.set_xlabel("Nombre d'arbres")
    ax.set_ylabel("RMSE (MW)")
    ax.set_title("L'erreur cesse de baisser : l'arrêt anticipé fixe le nombre "
                 "d'arbres", loc="left")
    ax.legend(loc="upper right")
    fig.savefig(chemin)
    plt.close(fig)


def fig_erreurs(valid, pred, chemin):
    """Distribution des erreurs et nuage réel / prédit."""
    err = (pred - valid[TARGET_COL]) / 1000
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11.5, 4.6))

    a1.hist(err, bins=60, color=BLEU, alpha=0.85)
    a1.axvline(0, color=NOIR, lw=1.2)
    a1.set_xlabel("Erreur (GW) · négatif = sous-estimé")
    a1.set_ylabel("Nombre d'heures")
    a1.set_title(f"Répartition des erreurs (biais moyen {err.mean():+.2f} GW)",
                 loc="left")

    y = valid[TARGET_COL] / 1000
    a2.scatter(y, pred / 1000, s=4, alpha=0.25, color=BLEU, edgecolors="none")
    lim = [min(y.min(), (pred / 1000).min()), max(y.max(), (pred / 1000).max())]
    a2.plot(lim, lim, color=NOIR, lw=1.2, ls="--")
    a2.set_xlabel("Consommation réelle (GW)")
    a2.set_ylabel("Prédiction (GW)")
    a2.set_title("Prédiction contre réalité", loc="left")
    a2.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(chemin)
    plt.close(fig)


def main(df=None, figures=True):
    if df is None:
        df = charger()
        diagnostic(df)
    df, features, cat = preparer(df)

    train = df[df.index.year <= 2023]
    valid = df[df.index.year == 2024]
    print(f"\nEntraînement (<= 2023) : {len(train)} lignes")
    print(f"Validation (2024)      : {len(valid)} lignes")
    if len(valid) < 1000:
        print("ATTENTION : très peu de lignes en 2024, le score ne voudra pas "
              "dire grand-chose.")

    print("\nEntraînement LightGBM...")
    historique = {}
    model = lgb.LGBMRegressor(**PARAMS)
    model.fit(
        train[features], train[TARGET_COL],
        eval_set=[(train[features], train[TARGET_COL]),
                  (valid[features], valid[TARGET_COL])],
        eval_names=["train", "valid"],
        eval_metric="l2",
        categorical_feature=cat,
        callbacks=[lgb.early_stopping(100, verbose=False),
                   lgb.record_evaluation(historique)],
    )
    print(f"Arrêt à l'itération {model.best_iteration_}")

    # Convention commune aux 3 modèles : scores et figures sur les mêmes
    # heures, celles où la conso ET la prévision RTE existent.
    valid = valid[valid["prevision_j1_mw"].notna()]
    pred = pd.Series(model.predict(valid[features]), index=valid.index)
    y = valid[TARGET_COL]

    print("\n" + "=" * 78)
    print(f"RÉSULTATS SUR LA VALIDATION 2024 ({len(valid)} heures communes)")
    print("=" * 78)
    s_train = scores(train[TARGET_COL], model.predict(train[features]))
    print(f"(LightGBM sur l'entraînement : RMSE {s_train['rmse']:.0f} MW)")
    for nom, p in [("LightGBM     ", pred),
                   ("Prévision RTE", valid["prevision_j1_mw"]),
                   ("Naïve 7 jours", valid["lag_168h"])]:
        s = scores(y, p)
        print(f"{nom}  MAE {s['mae']:6.0f} MW  RMSE {s['rmse']:6.0f} MW  "
              f"R² {s['r2']:.4f}  MAPE {s['mape']:5.2f} %  "
              f"biais {s['biais']:+6.0f} MW")
    print("=" * 78)

    # Le gain mesure la baisse d'erreur apportée, contrairement au comptage de
    # coupures affiché par défaut, qui favorise les variables continues.
    importance = pd.DataFrame({
        "variable": features,
        "gain": model.booster_.feature_importance(importance_type="gain"),
    }).sort_values("gain", ascending=False)
    importance["part_%"] = 100 * importance["gain"] / importance["gain"].sum()
    print("\nTop 15 des variables (par gain) :")
    print(importance.head(15).to_string(
        index=False, formatters={"gain": "{:,.0f}".format,
                                 "part_%": "{:.1f}".format}))

    # SHAP : méthode commune aux trois modèles, sur la validation 2024.
    shap = importance_shap(model, valid[features])
    print("\nImportance SHAP sur 2024 (% du total) :")
    print(shap.round(2).to_string())
    print("Par famille :", shap.groupby(shap.index.map(famille)).sum()
          .round(1).to_dict())

    if not figures:
        return model, valid, pred

    os.makedirs(DOSSIER, exist_ok=True)
    f = f"{DOSSIER}/{PREFIXE}"
    fig_semaines(valid, pred, f"{f}_01_semaines.png")
    fig_par_mois(valid, pred, f"{f}_02_mae_par_mois.png")
    fig_par_heure(valid, pred, f"{f}_03_mae_par_heure.png")
    fig_importance(shap, f"{f}_04_importance.png")
    fig_apprentissage(historique, model.best_iteration_,
                      f"{f}_05_apprentissage.png")
    fig_erreurs(valid, pred, f"{f}_06_erreurs.png")
    print(f"\n6 figures enregistrées dans {DOSSIER}/")
    return model, valid, pred


if __name__ == "__main__":
    main()