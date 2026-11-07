# Nettoyage et encodage POWER (Yassine)

Scripts : `code/donnees/05_nettoyer_conso.py` (nettoyage) et `code/donnees/06_encoder_features.py` (encodage). Même conventions que les étapes 1 à 4 : une ligne = une heure, `heure_utc` = début de l'heure, région `FR`, rien n'est modifié dans `donnees/brut/`.

```
python code/donnees/05_nettoyer_conso.py --sans-ecriture   # 1. constat seulement : on lit avant d'écrire
python code/donnees/05_nettoyer_conso.py                   # 2. écrit dans conso_nettoyee
python code/donnees/06_encoder_features.py                 # 3. table encodée pour les modèles
```

Ordre à respecter : étape 3 (conso_rte chargée) → 05 → 06. Après tout rechargement de `conso_rte` (étapes 2-3 relancées), relancer 05 : les valeurs comblées dépendent de leurs voisines.

| Script | Lit | Écrit |
|---|---|---|
| 05 | `conso_rte` (ou `--source parquet` : `base_power_horaire.parquet`) | table `conso_nettoyee`, `conso_nettoyee_horaire.parquet`, `etat_nettoyage.txt` |
| 06 | `v_base_modele` + `conso_nettoyee` (ou `--entree fichier`) | `base_modele_encodee.parquet`, `colonnes_modele.json`, `etat_encodage.txt` |

## 1. Nettoyage

### 1.1 Les 14 heures vides

À l'automne, l'heure locale 02:xx existe deux fois et RTE ne la publie qu'une fois (voir `squelette-base.md`). Il manque donc 00:00 UTC le dernier dimanche d'octobre, une fois par an, 2012 à 2025 : 14 heures. Le script les reconnaît par leur position (00:00 UTC, dimanche, 25 au 31 octobre) et vérifie qu'il en trouve bien 14. Tout autre trou est listé à part dans `etat_nettoyage.txt` : si le constat annonce autre chose que 14 + 0, c'est une information à regarder avant d'aller plus loin.

### 1.2 Méthode : interpolation linéaire

Une heure isolée entre deux heures connues : on prend la moyenne de l'heure d'avant et de l'heure d'après (en UTC, donc les deux voisines physiques). `methode = 'interpolation'`.

Pourquoi combler plutôt que laisser vide :
- un trou dans `conso_mw` en crée deux autres plus loin, dans `conso_lag_48h` (48 h après) et `conso_lag_168h` (7 jours après) : 14 trous deviennent 42 lignes avec une valeur manquante ;
- XGBoost refuse une cible `NaN` : il faudrait retirer ces lignes, donc perdre la continuité horaire dont les retards dépendent.

Pourquoi pas plus sophistiqué : 14 heures sur 122 736 (0,011 %), toutes la nuit, sur une courbe lisse. Une méthode à base de modèle (profil de la semaine précédente, régression) n'améliorerait pas le score, et chaque ligne de code en plus est une ligne à justifier dans le rapport.

Limite à connaître : les deux voisines sont 01:00 heure d'été et 02:00 heure d'hiver, alors que l'heure manquante est 02:00 heure d'été. L'interpolation estime donc plutôt « 01:30 », ce qui est un peu trop haut la nuit, quand la conso baisse. L'erreur est de l'ordre de la variation d'une heure à l'autre en pleine nuit (ordre de grandeur de l'ordre du pour cent de la conso, non mesuré sur les vraies données). Le constat affiche, pour chaque heure comblée, ses voisines et la valeur 7 jours avant, pour juger à l'œil.

### 1.3 Trous de plus de 3 heures

Laissés vides (même règle que la météo, `meteo.md` §4) : inventer plusieurs heures de suite, c'est fabriquer des données. Ils sont listés dans le constat. S'il y en a, il faudra en discuter en groupe (par exemple ne pas les utiliser en entraînement, jamais en test).

### 1.4 Valeurs suspectes : marquées, pas corrigées

`est_aberrante = True` (colonne `conso_suspecte` dans la table encodée) si :
- la conso sort de [15 000 ; 110 000] MW (la France n'a jamais été ni sous 15 GW ni au-dessus de 110 GW sur la période) ;
- ou c'est un pic isolé : l'écart entre l'heure et la moyenne de ses deux voisines dépasse 8 fois l'écart habituel **à cette heure de la journée** (échelle robuste par la médiane des écarts, pour que les pics ne faussent pas leur propre seuil).

Ces valeurs ne sont **pas modifiées** : une consommation réelle extrême (vague de froid, 8 février 2012) n'est pas une erreur. Le constat les liste avec leur score, et c'est à toi de décider, valeur par valeur, si c'en est une. Pour en corriger une : la remplacer par une valeur dans `conso_nettoyee.conso_mw` avec `methode = 'imputation'` (jamais par NULL, voir §1.5).

### 1.5 Table `conso_nettoyee` : une ligne par heure

La table contient toute la période (122 736 lignes), `methode = 'aucune'` pour les heures non touchées. La vue `v_base_modele` lit `COALESCE(n.conso_mw, c.conso_mw)` : un NULL dans `conso_nettoyee` ferait **réapparaître la valeur brute**. Pour « retirer » une valeur, il faut donc la remplacer, pas la vider. Poids estimé : de l'ordre de 10 à 15 Mo avec l'index (estimation, à vérifier avec `pg_total_relation_size('conso_nettoyee')`), négligeable sur les 0,5 Go de Neon.

### 1.6 Conséquence pour l'évaluation

Deux heures comblées tombent hors du train : **27/10/2024 00:00 UTC (valid)** et **26/10/2025 00:00 UTC (test)**. Leur « vraie » valeur est une interpolation : les noter ne mesure pas la qualité du modèle. Les exclure du score avec `~base["conso_imputee"]` (voir l'exemple §3). À dire dans le rapport : 1 heure sur 8 784 en validation, 1 sur 8 760 en test.

## 2. Encodage

Principe : les modèles à arbres (XGBoost, LightGBM, CatBoost) découpent une variable par seuils. Ils n'ont besoin ni de normalisation, ni de one-hot, et gèrent les valeurs manquantes. L'encodage consiste donc surtout à **mettre les types au bon format** et à calculer le calendrier sans erreur d'heure.

| Variable | Traitement | Pourquoi |
|---|---|---|
| `heure`, `jour_semaine`, `mois`, `jour_annee`, `semaine_iso`, `annee` | calculés depuis `heure_utc` converti en heure de Paris | le calendrier suit l'heure de Paris, pas l'UTC (`squelette-base.md` §2). Le script compare avec `heure_locale` de la vue et prévient en cas de différence |
| `est_weekend`, `est_heure_ete` | entiers 0/1 ; heure d'été = Paris a 2 h d'avance sur l'UTC | |
| `est_ferie`, `est_pont`, `vacances_zone_a/b/c`, `en_confinement` | booléen → `int8` (0/1), NULL → `NaN` | `psycopg` renvoie un booléen avec NULL en colonne `object`, que XGBoost refuse |
| `nb_zones_vacances` | somme des trois zones (0 à 3) | signal direct pour la conso nationale ; les arbres pourraient le retrouver, mais plus lentement |
| `temperature_c`, `vent_ms`, `rayonnement_wm2` (`nebulosite_pct`) | `float32` ; une colonne entièrement vide est retirée des listes (la nébulosité, voir `meteo.md` §3) | |
| `conso_lag_48h`, `conso_lag_168h` | conso nettoyée décalée de 48 et 168 lignes | règle 1 : jamais moins de 48 h. Premières lignes de 2012 : `NaN`, laissé tel quel |
| `*_sin`, `*_cos` de `heure` (24), `jour_semaine` (7), `mois` (12), `jour_annee` (365,25) | $\sin,\cos(2\pi (x - x_0)/T)$ | pour les modèles qui ne sont **pas** des arbres (ridge, réseau) : 23 h et 0 h deviennent voisines. Un arbre les sépare avec deux seuils, il n'en a pas besoin |
| `split` | train 2012-2023, valid 2024, test 2025, selon l'année en heure de Paris | même découpage pour tout le groupe (`squelette-base.md` §2) |

### Ce que le script ne fait pas, volontairement

- **Pas de one-hot** de `heure` / `mois` / `jour_semaine` : 43 colonnes à la place de 3, et l'ordre (de 0 h à 23 h) est perdu.
- **Pas de normalisation** : sans effet sur des arbres.
- **Pas de target encoding** : il utilise la cible pour fabriquer une variable, donc il fuit vers le futur si on se trompe de découpage.
- **Aucune valeur manquante comblée** dans les variables d'entrée : les arbres les gèrent.

### Listes de variables (`colonnes_modele.json`)

- `features_arbres` : 18 variables (calendrier en entiers, booléens, météo, retards). À utiliser pour XGBoost, LightGBM, CatBoost.
- `features_cycliques` : mêmes variables, avec sin/cos à la place de `heure`, `jour_semaine`, `mois`, `jour_annee`. Pour un modèle non arbre, retirer aussi les lignes avec retards manquants (début de 2012).
- `hors_features` : colonnes présentes dans la table mais **jamais variables d'entrée** :

| Colonne | Raison |
|---|---|
| `annee` | un arbre ne sait pas extrapoler : 2025 n'est pas dans le train, il traiterait 2025 comme 2023. C'est aussi un proxy de tendance, pas une cause |
| `semaine_iso` | redondante avec `mois` et `jour_annee`, et les semaines 1 et 53 chevauchent deux années |
| `prevision_rte_j1_mw` | la référence à battre, pas une entrée (`squelette-base.md` §3) |
| `conso_imputee`, `conso_suspecte` | comptabilité du nettoyage, pas une information sur la conso |
| `heure_locale`, `split` | repérage |

## 3. Utilisation avec XGBoost (testé sur données synthétiques)

```python
import json
import pandas as pd
from xgboost import XGBRegressor

base = pd.read_parquet("donnees/base/base_modele_encodee.parquet")
cols = json.load(open("donnees/base/colonnes_modele.json", encoding="utf-8"))
X, y = cols["features_arbres"], cols["cible"]

base = base.dropna(subset=[y])                       # au cas où un trou de plus de 3 h resterait
train = base[base["split"] == "train"]
valid = base[base["split"] == "valid"]

modele = XGBRegressor(n_estimators=2000, learning_rate=0.05, max_depth=8,
                      early_stopping_rounds=50, tree_method="hist")
modele.fit(train[X], train[y], eval_set=[(valid[X], valid[y])], verbose=False)

# On ne note pas sur une heure comblée : sa valeur est inventée par l'interpolation.
v = valid[~valid["conso_imputee"]]
mae_modele = (modele.predict(v[X]) - v[y]).abs().mean()
mae_rte = (v["prevision_rte_j1_mw"] - v[y]).abs().mean()
```

LightGBM et CatBoost : mêmes `train` / `valid` et mêmes colonnes `X` ; seul le test (`split == "test"`) reste à n'utiliser qu'**une fois**, à la fin.

## 4. Limites connues

- **Retards en UTC** (décalage de 48 et 168 lignes) : conforme au squelette, mais pendant la semaine qui suit un changement d'heure, « 7 jours avant » correspond à une heure locale décalée d'une heure. Environ 2 semaines par an, effet faible.
- **Météo observée, pas prévue** : le score sera un peu optimiste (`meteo.md` §7).
- **Seuils du repérage de pics** (8 écarts robustes, bornes 15-110 GW) : choisis sans avoir vu les vraies données. À régler dans les constantes en tête de `05_nettoyer_conso.py` si le constat liste trop ou trop peu de valeurs.
- **Les 14 heures d'octobre restent une estimation** : elles sont signalées (`methode`, `conso_imputee`) et exclues du score, pas cachées.
