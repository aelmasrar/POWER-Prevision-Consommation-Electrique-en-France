"""
Étape 7 : entraîner XGBoost sur la table encodée et regarder les résultats.

Entrées : donnees/base/base_modele_encodee.parquet   (sortie de l'étape 6)
          donnees/base/colonnes_modele.json
Sorties : resultats/xgboost/metriques.json            chiffres de validation (et de test avec --test)
          resultats/xgboost/predictions_valid.parquet prédiction, réel, erreur, heure par heure
          resultats/xgboost/modele_xgb.json           le modèle entraîné
          resultats/xgboost/importance_variables.csv  importance (gain) de chaque variable
          resultats/xgboost/figures/*.png             4 graphiques
          resultats/xgboost/rapport.txt               tout ce qui est affiché à l'écran

Ce que fait le script :
1. Entraîne sur train (2012-2023), avec arrêt anticipé sur valid (2024).
2. Note sur valid, SANS les heures comblées par interpolation (conso_imputee), face à
   - la prévision RTE J-1 (la référence à battre),
   - deux références naïves : « même heure il y a 7 jours » et « même heure il y a 2 jours ».
3. Découpe l'erreur par heure, par mois, férié / non férié, et liste les pires heures.
4. Importance des variables (gain) et courbe d'apprentissage.
5. Le test (2025) n'est touché QUE si on ajoute --test : une seule fois, à la fin, une fois
   les réglages arrêtés (règle du groupe, squelette-base.md §2).

Installation (une fois) : pip install xgboost scikit-learn matplotlib pandas pyarrow
(scikit-learn est exigé par XGBRegressor ; matplotlib seulement pour les figures.)

Usage (depuis le dossier POWER) :
    python code/modeles/09_xgboost.py                  # entraîne, note sur valid, figures
    python code/modeles/09_xgboost.py --sans-figures   # sans matplotlib
    python code/modeles/09_xgboost.py --test           # ajoute la note sur test (à faire une seule fois)
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBRegressor

RACINE = Path(__file__).resolve().parents[2]
DOSSIER_BASE = RACINE / "donnees" / "base"
SORTIE = RACINE / "resultats" / "xgboost"

PARAMS = dict(
    n_estimators=3000,          # plafond : l'arrêt anticipé coupe bien avant
    learning_rate=0.03,
    max_depth=7,
    min_child_weight=5,
    subsample=0.8,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    tree_method="hist",
    early_stopping_rounds=100,
    random_state=0,
    n_jobs=-1,
)
JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]


# ---------------------------------------------------------------- métriques

def metriques(reel: pd.Series, pred: pd.Series) -> dict:
    """MAE et RMSE en MW, MAPE en %, sur les lignes où les deux existent."""
    ok = reel.notna() & pred.notna()
    e = (pred[ok] - reel[ok]).astype("float64")
    return {"n": int(ok.sum()),
            "MAE_MW": float(e.abs().mean()),
            "RMSE_MW": float(np.sqrt((e ** 2).mean())),
            "MAPE_pct": float((e.abs() / reel[ok].abs()).mean() * 100)}


def tableau_comparaison(t: pd.DataFrame, pred: np.ndarray) -> pd.DataFrame:
    """Modèle, RTE J-1 et références naïves, comparés sur les MÊMES heures."""
    cand = {"XGBoost": pd.Series(pred, index=t.index),
            "RTE J-1": t.get("prevision_rte_j1_mw"),
            "Naïf : même heure -7 j": t["conso_lag_168h"],
            "Naïf : même heure -2 j": t["conso_lag_48h"]}
    cand = {k: v for k, v in cand.items() if v is not None}
    commun = t["conso_mw"].notna()
    for v in cand.values():
        commun &= v.notna()
    lignes = {k: metriques(t.loc[commun, "conso_mw"], v[commun]) for k, v in cand.items()}
    return pd.DataFrame(lignes).T


def decouper(t: pd.DataFrame, pred: np.ndarray) -> dict[str, pd.DataFrame]:
    """Erreur absolue moyenne du modèle (et de RTE J-1) selon l'heure, le mois, le jour, le férié."""
    d = pd.DataFrame({"erreur_abs": np.abs(pred - t["conso_mw"].to_numpy()),
                      "heure": t["heure"], "mois": t["mois"], "jour_semaine": t["jour_semaine"],
                      "est_ferie": t["est_ferie"]}, index=t.index)
    if "prevision_rte_j1_mw" in t:
        d["erreur_rte"] = (t["prevision_rte_j1_mw"] - t["conso_mw"]).abs()
    sorties = {}
    for cle in ("heure", "mois", "jour_semaine", "est_ferie"):
        colonnes = [c for c in ("erreur_abs", "erreur_rte") if c in d]
        g = d.groupby(cle)[colonnes].mean()
        g.columns = ["MAE_xgb"] + (["MAE_rte"] if "erreur_rte" in colonnes else [])
        g["n"] = d.groupby(cle).size()
        sorties[cle] = g
    return sorties


# ---------------------------------------------------------------- figures

def figures(valid: pd.DataFrame, pred: np.ndarray, modele: XGBRegressor,
            importance: pd.Series, par_bloc: dict, dossier: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dossier.mkdir(parents=True, exist_ok=True)
    bleu, orange, gris = "#3333B0", "#D9822B", "#888888"
    v = valid.assign(pred=pred)

    # 1. Réel / prédit sur une semaine d'hiver et une semaine d'été de la validation
    fig, axes = plt.subplots(2, 1, figsize=(11, 6.5))
    for ax, (mois, titre) in zip(axes, [(1, "janvier 2024"), (7, "juillet 2024")]):
        s = v[(v["annee"] == 2024) & (v["mois"] == mois)].iloc[24 * 7: 24 * 14]   # 2e semaine du mois
        ax.plot(s["heure_locale"], s["conso_mw"] / 1000, color="black", lw=1.6, label="réel")
        ax.plot(s["heure_locale"], s["pred"] / 1000, color=bleu, lw=1.3, label="XGBoost")
        if "prevision_rte_j1_mw" in s:
            ax.plot(s["heure_locale"], s["prevision_rte_j1_mw"] / 1000, color=orange, lw=1.0, ls="--", label="RTE J-1")
        ax.set_title(f"Une semaine de {titre}", fontsize=10, loc="left")
        ax.set_ylabel("GW"); ax.grid(alpha=0.25)
    axes[0].legend(ncol=3, fontsize=8, loc="upper right")
    fig.tight_layout(); fig.savefig(dossier / "1_reel_vs_predit.png", dpi=140); plt.close(fig)

    # 2. Erreur par heure de la journée
    fig, ax = plt.subplots(figsize=(8, 4))
    h = par_bloc["heure"]
    ax.plot(h.index, h["MAE_xgb"], color=bleu, marker="o", ms=3, label="XGBoost")
    if "MAE_rte" in h:
        ax.plot(h.index, h["MAE_rte"], color=orange, marker="o", ms=3, ls="--", label="RTE J-1")
    ax.set_xlabel("heure (Paris)"); ax.set_ylabel("MAE (MW)"); ax.set_xticks(range(0, 24, 2))
    ax.set_title("Erreur absolue moyenne par heure, validation 2024", fontsize=10, loc="left")
    ax.grid(alpha=0.25); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(dossier / "2_erreur_par_heure.png", dpi=140); plt.close(fig)

    # 3. Importance des variables (gain)
    fig, ax = plt.subplots(figsize=(8, 5.5))
    imp = importance.sort_values().tail(18)
    ax.barh(imp.index, imp.values / imp.sum() * 100, color=bleu)
    ax.set_xlabel("part du gain total (%)")
    ax.set_title("Importance des variables (gain)", fontsize=10, loc="left")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout(); fig.savefig(dossier / "3_importance_variables.png", dpi=140); plt.close(fig)

    # 4. Courbe d'apprentissage (train vs valid) et arbre retenu
    res = modele.evals_result()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(res["validation_0"]["rmse"], color=gris, label="train")
    ax.plot(res["validation_1"]["rmse"], color=bleu, label="valid")
    ax.axvline(modele.best_iteration, color=orange, ls="--", lw=1, label=f"arrêt : {modele.best_iteration} arbres")
    ax.set_xlabel("nombre d'arbres"); ax.set_ylabel("RMSE (MW)"); ax.set_yscale("log")
    ax.set_title("Courbe d'apprentissage", fontsize=10, loc="left")
    ax.grid(alpha=0.25); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(dossier / "4_courbe_apprentissage.png", dpi=140); plt.close(fig)


# ---------------------------------------------------------------- main

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--test", action="store_true",
                   help="note aussi sur le test (2025) : à faire UNE SEULE FOIS, réglages arrêtés")
    p.add_argument("--sans-figures", action="store_true", help="ne pas produire les graphiques")
    a = p.parse_args()

    chemin = DOSSIER_BASE / "base_modele_encodee.parquet"
    if not chemin.exists():
        sys.exit(f"{chemin} introuvable : lancer l'étape 6.")
    base = pd.read_parquet(chemin)
    cols = json.loads((DOSSIER_BASE / "colonnes_modele.json").read_text(encoding="utf-8"))
    X, y = cols["features_arbres"], cols["cible"]

    sans_cible = int(base[y].isna().sum())
    base = base.dropna(subset=[y])
    train = base[base["split"] == "train"]
    valid = base[base["split"] == "valid"]
    test = base[base["split"] == "test"]

    log: list[str] = []
    def dire(*lignes):
        for l in lignes:
            print(l); log.append(str(l))

    dire("=== XGBoost : entraînement sur train, réglage sur valid ===",
         f"Variables d'entrée : {len(X)}",
         f"Lignes : train {len(train)}, valid {len(valid)}, test {len(test)} (test non utilisé sans --test)")
    if sans_cible:
        dire(f"ATTENTION : {sans_cible} heures sans conso retirées (trous de plus de 3 h, voir etat_nettoyage.txt).")

    # ---- entraînement
    modele = XGBRegressor(**PARAMS)
    modele.fit(train[X], train[y], eval_set=[(train[X], train[y]), (valid[X], valid[y])], verbose=False)
    dire(f"Arbres retenus (arrêt anticipé) : {modele.best_iteration} sur {PARAMS['n_estimators']} possibles")
    if modele.best_iteration >= 0.95 * PARAMS["n_estimators"]:
        dire("ATTENTION : le plafond d'arbres est presque atteint, l'arrêt anticipé n'a pas joué : "
             "augmenter n_estimators ou learning_rate dans PARAMS.")

    # ---- validation, hors heures comblées
    v = valid[~valid["conso_imputee"]]
    pred_v = modele.predict(v[X])
    dire("", f"=== Validation 2024 ({len(v)} heures ; {len(valid) - len(v)} heure(s) comblée(s) écartée(s)) ===")
    comp = tableau_comparaison(v, pred_v)
    dire(comp.round({"MAE_MW": 0, "RMSE_MW": 0, "MAPE_pct": 2}).to_string())
    gain = 1 - comp.loc["XGBoost", "MAE_MW"] / comp.loc["RTE J-1", "MAE_MW"] if "RTE J-1" in comp.index else None
    if gain is not None:
        dire(f"MAE XGBoost vs RTE J-1 : {gain * 100:+.1f} % (positif = XGBoost meilleur)")
    dire("Attention : la validation a servi à l'arrêt anticipé, elle est un peu optimiste. Le test reste intact.")

    # ---- découpage de l'erreur
    par_bloc = decouper(v, pred_v)
    dire("", "=== Erreur par heure de Paris (MAE, MW) ===",
         par_bloc["heure"].round(0).astype({"n": int}).to_string())
    dire("", "=== Erreur par mois ===", par_bloc["mois"].round(0).astype({"n": int}).to_string())
    jr = par_bloc["jour_semaine"].round(0).astype({"n": int}); jr.index = [JOURS[i] for i in jr.index]
    dire("", "=== Erreur par jour de la semaine ===", jr.to_string())
    dire("", "=== Férié (1) / non férié (0) ===", par_bloc["est_ferie"].round(0).astype({"n": int}).to_string())

    pires = pd.DataFrame({"heure_paris": v["heure_locale"], "reel": v["conso_mw"],
                          "prevu": pred_v, "erreur": pred_v - v["conso_mw"]})
    pires = pires.reindex(pires["erreur"].abs().sort_values(ascending=False).index).head(10)
    pires[["reel", "prevu", "erreur"]] = pires[["reel", "prevu", "erreur"]].round(0)
    dire("", "=== 10 plus grosses erreurs (validation) ===", pires.to_string())

    # ---- importance
    imp = pd.Series(modele.get_booster().get_score(importance_type="gain")).reindex(X).fillna(0.0)
    dire("", "=== Importance des variables (part du gain, %) ===",
         (imp / imp.sum() * 100).sort_values(ascending=False).round(1).to_string())

    # ---- sorties
    SORTIE.mkdir(parents=True, exist_ok=True)
    resultats = {"parametres": {k: v_ for k, v_ in PARAMS.items()}, "arbres_retenus": int(modele.best_iteration),
                 "validation": comp.to_dict(orient="index")}
    pd.DataFrame({"reel": v["conso_mw"], "prevu": pred_v, "erreur": pred_v - v["conso_mw"],
                  "prevision_rte_j1": v.get("prevision_rte_j1_mw")}).to_parquet(SORTIE / "predictions_valid.parquet")
    imp.sort_values(ascending=False).rename("gain").to_csv(SORTIE / "importance_variables.csv")
    modele.save_model(SORTIE / "modele_xgb.json")
    if not a.sans_figures:
        try:
            figures(v, pred_v, modele, imp, par_bloc, SORTIE / "figures")
            dire("", f"Figures : {SORTIE / 'figures'}")
        except ImportError:
            dire("", "matplotlib absent : pip install matplotlib (ou --sans-figures)")

    # ---- test : une seule fois
    if a.test:
        t = test[~test["conso_imputee"]]
        comp_t = tableau_comparaison(t, modele.predict(t[X]))
        resultats["test"] = comp_t.to_dict(orient="index")
        dire("", f"=== TEST 2025 ({len(t)} heures) : à ne plus relancer pour régler quoi que ce soit ===",
             comp_t.round({"MAE_MW": 0, "RMSE_MW": 0, "MAPE_pct": 2}).to_string())

    (SORTIE / "metriques.json").write_text(json.dumps(resultats, indent=2, ensure_ascii=False, default=float),
                                           encoding="utf-8")
    (SORTIE / "rapport.txt").write_text("\n".join(log) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
