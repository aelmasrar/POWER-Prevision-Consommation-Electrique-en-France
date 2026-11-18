# Pistes d'amélioration et comparaison LightGBM / CatBoost

Document de travail du 17/11/2026 (Momo, avec Claude). Tous les chiffres sont mesurés sur la validation 2024, sur les 8 783 heures où la conso et la prévision RTE existent. Le détail des essais est dans `journal/momo.md`.

---

## 1. Où en est LightGBM

| | MAE | RMSE | R² | MAPE | Biais |
|---|---|---|---|---|---|
| LightGBM (evt_energie seule, sans Tempo) | 949 MW | 1 252 MW | 0,9843 | 1,87 % | −189 MW |
| LightGBM sans aucune evt_* | 1 232 MW | 1 675 MW | 0,9718 | 2,39 % | +782 MW |
| RTE J-1 | 1 428 MW | 1 766 MW | 0,9687 | 2,86 % | −1 146 MW |
| RTE J-1 corrigé de son biais horaire | 849 MW | – | – | 1,69 % | 0 |
| Naïve 7 jours | 3 247 MW | 4 858 MW | 0,7630 | 6,09 % | −164 MW |

---

## 2. Pourquoi on « bat » RTE : deux raisons à dire dans la présentation

### 2.1 Notre météo est mesurée, celle de RTE est prévue
- RTE prévoit la veille avec une température **prévue**, qui se trompe en général d'environ 1 °C en J+1. Nous utilisons la température **mesurée** : nous connaissons la réponse.
- En hiver, la France consomme de l'ordre de **2,4 GW de plus par degré en moins** (ordre de grandeur RTE). 1 °C d'erreur peut donc coûter 1 à 2 GW sur une heure d'hiver, soit l'essentiel de notre avance.
- **Pour mesurer cet avantage :**
  1. *Simple* : ajouter à la température de validation un bruit réaliste (écart d'environ 1 à 1,5 °C, qui persiste plusieurs heures) et regarder de combien le score se dégrade.
  2. *Rigoureux* : récupérer de vraies prévisions météo archivées (Open-Meteo garde les prévisions passées, ce qui couvre 2024 et 2025). On entraîne sur la météo mesurée et on évalue sur la météo prévue, comme en conditions réelles.

### 2.2 RTE a un biais horaire systématique depuis 2022
- Erreur de RTE (MAPE) : environ **1,5 % de 2012 à 2021**, puis 1,98 % (2022), 2,73 % (2023), 2,86 % (2024), 2,81 % (2025).
- Biais RTE : entre −115 et −468 MW avant 2022, puis −1 131 MW (2023) et −1 146 MW (2024).
- Ce biais est **concentré sur certaines heures** : 0 h, 13-15 h et 20-21 h, toujours en sous-estimation. À 0 h, il passe de −1,4 GW (2015) à −2,4 GW (2024). À 5 h il est presque nul.
- Si on retire à RTE son biais propre à chaque heure, il tombe à **849 MW (1,69 %)** et repasse devant LightGBM (949 MW).
- Ce n'est **pas un simple décalage horaire** (piste 4 de la liste des problèmes, à reformuler). Hypothèses à vérifier avec l'équipe :
  - la conso réelle et la prévision J-1 ne portent pas sur le même périmètre (données consolidées ou en temps réel, pompage, autoconsommation…) ;
  - un changement d'usage récent à ces heures (heures creuses, ballons d'eau chaude pilotés, recharge des véhicules) que la prévision de RTE intègre mal.
- La même forme se retrouve chez CatBoost (section 5), ce qui confirme qu'elle vient bien de RTE et pas de notre code.

---

## 3. Pistes pour la sensibilité au froid

Constat (LightGBM) : pires mois janvier (1 652 MW), décembre (1 228), avril (1 190), novembre (1 187). Semaine du 8-15 janvier sous-estimée de 1,8 GW. Les pics au-dessus de 75 GW sont sous-estimés (nuage).

1. **Températures lissées** : moyennes sur 24 h, 48 h et 72 h, ou lissage exponentiel. Les bâtiments ont de l'inertie : la conso dépend du froid des jours précédents. → **Déjà testé par CatBoost** : `temp_moy_24h` est sa 2e variable (13,9 %) et `temp_moy_72h` la 7e (6,1 %). C'est la piste la plus prometteuse.
2. **Degrés-jours de chauffage** : `max(0, 15 − T)`. Vaut 0 quand il fait doux et grandit avec le froid, ce qui facilite le travail des arbres.
3. **Écart à la normale saisonnière** : température moins la moyenne historique du même jour.
4. **Température pondérée par la population** : vérifier comment la température nationale est construite à partir de `donnees/stations_meteo.csv`. Un froid à Paris pèse plus qu'un froid en Lozère.
5. **Le défaut des arbres** : ils ne prédisent pas au-delà de ce qu'ils ont vu, d'où le plafond au-dessus de 75 GW. Deux solutions :
   - `linear_tree=True` dans LightGBM (une petite droite dans chaque feuille) ;
   - un modèle en deux étages : régression linéaire sur la température, puis le modèle d'arbres sur le reste.
6. **Prédire la variation** (conso − `lag_168h`) plutôt que le niveau : les décalages pèsent moins, et la météo doit expliquer l'écart.
7. **Modèle sans décalages**, pour forcer le modèle à s'appuyer sur la météo et voir ce qu'elle explique seule.
8. **Un indicateur dédié** : calculer l'erreur sur les seules heures froides (moins de 5 °C) pour juger chaque piste là où on est faibles.
9. Ajouter `lag_336h` (deux semaines), utilisé par CatBoost (2,7 %).

---

## 4. Pistes contre le sur-apprentissage

Constat : LightGBM RMSE 542 MW en entraînement contre 1 252 MW en validation (2,3 fois plus). La validation est presque plate dès environ 1 000 arbres, alors que l'arrêt se fait à 2 747. CatBoost montre exactement la même chose (426 contre 968 MW, 2,3 fois ; de 1 035 à 3 106 arbres, entraînement −39 %, validation −3 %).

1. **Des arbres plus simples** : `num_leaves` 31 au lieu de 63, `min_child_samples` 100 à 200, éventuellement `max_depth` 8.
2. **Pénalités** : `reg_lambda` (1 à 10), `min_split_gain`.
3. **Plus de hasard** : `colsample_bytree` 0,6.
4. **Moins de variables** : retirer celles au gain quasi nul (`fin_annee`, `vacances_zone_a/b/c`, `est_weekend`, `mois`) et vérifier que le score ne bouge pas.
5. **Période d'entraînement** : commencer en 2015, ou donner plus de poids aux années récentes (`sample_weight`).
6. **Validation glissante** : à force de tout régler sur 2024, on finit par sur-apprendre la validation elle-même. Entraîner jusqu'en 2021 et valider sur 2022, puis jusqu'en 2022 et valider sur 2023, puis jusqu'en 2023 et valider sur 2024, et moyenner. Un réglage qui gagne sur les trois années est fiable.
7. **À relativiser** : un écart entre entraînement et validation est normal pour ces modèles ; ce qui compte, c'est le score en validation. Les derniers arbres n'abîment pas 2024, ils sont juste inutiles et coûtent du temps.

---

## 5. Comparaison LightGBM / CatBoost

### 5.1 Ce qui concorde (bon signe : mêmes données, mêmes heures)
- **RTE identique** chez les deux : biais −1,15 GW (CatBoost) contre −1 146 MW (LightGBM), mêmes pics d'erreur à 0 h, 3 h, 13-16 h et 20-21 h, mai et janvier parmi les pires mois de RTE.
- **Erreur du modèle stable selon l'heure** chez les deux (CatBoost entre environ 1,3 et 1,8 %, LightGBM entre 805 et 1 095 MW).
- **Même sur-apprentissage** : rapport validation / entraînement de 2,3 chez les deux, plateau de validation dès environ 1 000 arbres.
- **Même faiblesse sur les pics** : les deux nuages passent sous la diagonale au-dessus d'environ 75 GW, et les deux sous-estiment la journée du 9 janvier.
- **Même rôle de `evt_energie`** : chez CatBoost, 5,6 % de l'importance (SHAP), plus 3,8 % pour `evt_geopolitique`.

### 5.2 Ce qui diffère
| | LightGBM | CatBoost |
|---|---|---|
| RMSE validation | 1 252 MW | 968 MW |
| Biais | −0,19 GW | −0,26 GW |
| Arbres à l'arrêt | 2 747 | 3 106 |
| Pire mois | janvier | mai |
| Températures lissées 24 h / 72 h | non | oui |
| `lag_336h` | non | oui |
| `evt_*` gardées | evt_energie | evt_energie + evt_geopolitique (+ d'autres ?) |
| Mesure d'importance | gain | SHAP |

- **CatBoost est nettement meilleur** (RMSE 968 contre 1 252 MW), surtout en hiver : son mois de janvier est à 1,5 % alors que c'est notre pire mois. L'explication la plus probable : `temp_moy_24h` et `temp_moy_72h`. C'est la piste 3.1 à tester tout de suite dans LightGBM.
- **L'importance n'est pas comparable en l'état** : nous mesurons le gain (décalages 80 %), CatBoost mesure SHAP (décalages 21 %, météo 31 %, calendrier 37 %). Sa note dit « même méthode (SHAP) pour les trois modèles » → **il faut passer LightGBM en SHAP**.

### 5.3 Conventions à harmoniser avant la présentation
1. **Importance** : SHAP pour les trois modèles (LightGBM à refaire).
2. **Unités des graphiques par mois et par heure** : la convention dit MAE en MW ; CatBoost affiche la MAPE en %. Choisir l'une des deux pour les trois modèles.
3. **Couleurs** :
   - LightGBM et CatBoost sont tous les deux en bleu → donner une couleur fixe et différente à chaque modèle ;
   - familles de variables : la convention dit passé orange, météo vert, calendrier bleu ; CatBoost utilise violet, vert, jaune. Attention, l'orange est aussi la couleur de RTE.
4. **Semaines** : CatBoost trace du 08/01 au 16/01 (jour du 15 inclus), LightGBM s'arrête au 15/01 à 0 h. Se mettre d'accord.
5. **Variables d'événements et Tempo** : la famille « Tempo et événements » de CatBoost doit être vérifiée. Si `tempo_couleur` est utilisée, elle viole la règle des 48 h (couleur publiée vers 10 h 30 la veille et choisie d'après une prévision de conso). Utiliser les mêmes `evt_*` dans les trois modèles, sinon la comparaison n'est pas équitable (2,39 % contre 1,87 % chez LightGBM selon la présence de `evt_energie`).
6. **Tableau de scores commun** : MAE, RMSE, R², MAPE, biais pour les trois modèles et RTE ; CatBoost n'affiche pas encore sa MAPE globale.

---

## 6. Questions pour l'équipe
- CatBoost : quelles variables exactement (Tempo ? quelles `evt_*` ?), quels paramètres, et quel score global (MAE, MAPE, R²) ?
- Comment `evt_meteo` a-t-il été daté : alertes annoncées à l'avance, ou périodes constatées après coup ?
- Pourquoi la prévision RTE a-t-elle un biais à 0 h, 13-15 h et 20-21 h depuis 2022 (périmètre ? usages ?) ?
- Que s'est-il passé le 25/11/2024 (pire jour de LightGBM, 4,4 GW d'erreur moyenne) ?
- Tempo avant septembre 2014 : introuvable en données ouvertes (fichiers éCO2mix tout bleus). Le script `code/donnees/08_tempo.py` laisserait passer ces fichiers : à corriger.

---

## 7. Ordre proposé pour la suite (LightGBM)
1. Ajouter `temp_moy_24h`, `temp_moy_72h` et `lag_336h` (reprendre ce qui marche chez CatBoost).
2. Retirer les variables au gain nul.
3. Régularisation (`num_leaves` 31, `min_child_samples` 100, `reg_lambda`).
4. Importance en SHAP.
5. Mesurer l'avantage de la météo mesurée (bruit sur la température, puis vraies prévisions).
6. Harmoniser les conventions avec les deux autres modèles, puis préparer la présentation.

---

## 8. Ajout de XGBoost (18/11/2026)

D'après les 3 diapos XGBoost reçues (validation 2024, 8 783 heures, 17 variables, 702 arbres, profondeur 7, pas 0,03).

| | MAE | RMSE | MAPE | Biais |
|---|---|---|---|---|
| XGBoost | 1 254 MW | n. c. | 2,43 % | +838 MW |
| LightGBM | 800 MW | 1 065 MW | 1,58 % | −127 MW |
| CatBoost | n. c. | 968 MW | n. c. | −260 MW |
| RTE J-1 | 1 428 MW | 1 766 MW | 2,86 % | −1 146 MW |

- **Mêmes heures** : RTE (1 428 MW, 2,86 %) et naïve 7 j (3 247 MW, 6,09 %) identiques chez XGBoost et LightGBM.
- **XGBoost ressemble à notre LightGBM sans `evt_*`** (2,39 %, +782 MW) : pas de `evt_energie` dans ses 17 variables, d'où la surestimation depuis 2022. Ajouter `evt_energie` et `temp_moy_24h`/`72h` devrait le rapprocher des deux autres.
- **`est_heure_ete` à 20 % du gain** : variable absente chez nous ; probablement un indicateur de saison. À comparer en SHAP.
- **Importance par gain** chez XGBoost : non comparable avec SHAP (le gain gonfle les décalages, cf. LightGBM 72 % en gain contre 36 % en SHAP).
- **Erreur par heure** : régulière (930 à 1 500 MW), même forme RTE que chez les deux autres.
- **Arrêt à 702 arbres** : plus tôt que LightGBM (3 000) et CatBoost (3 106), arbres plus profonds. Validation plate dès ≈ 300 arbres.
- **Question importante** : la diapo XGBoost annonce une « météo prévue la veille ». La météo de la base est mesurée. Soit XGBoost a une autre source (à récupérer pour les trois modèles), soit c'est une erreur d'étiquette. À clarifier avant la soutenance.

## 9. Tableau de bord (Streamlit)
- Navigateur local (127.0.0.1:8501) → `app.py` → 3 pages : Vue d'ensemble, Prédictions, Performances.
- Python + Streamlit, Plotly + Pandas ; `pages/`, `ui.py`, `styles.css`.
- Les métriques de la page Performances sont « à calculer plus tard » : y brancher les prévisions 2024 des trois modèles et de RTE.

## 10. Présentation courte
`Présentation/comparaison_modeles/comparaison_trois_modeles.tex` : 12 diapos avec apparitions progressives. Les pistes détaillées (sections 2 à 4) restent ici et ne sont pas toutes dans les diapos. L'ancienne version longue (`comparaison_lightgbm_catboost.tex`) est conservée.

## 11. Ajouts SHAP, Tempo et météo (18/11/2026)
- **SHAP (diapos de l'équipe, CatBoost)** : cascade du 10/01/2024 à 19 h (moyenne 53,4 GW → prévision 83,9 GW, réel 85,3 GW ; `temp_moy_24h` +8,0 GW, lag 48 h +5,5 GW, température +5,3 GW). Native contre SHAP : `lag_168h` 20 % → 7,4 %, `evt_energie` 0,7 % → 5,6 % (native apprise sur 2012-2023, SHAP calculé sur 2024). Décision : SHAP pour les trois modèles.
- **Tempo** : retiré (LightGBM 1,87 % sans, 1,85 % décalé de 48 h avec poids 0,0 %, 1,82 % « triche » du jour).
- **Météo** : plan d'équipe = entraîner sur la météo mesurée, évaluer 2024 et 2025 sur la météo prévue la veille (Open-Meteo, archive gratuite seulement depuis 2024). Ce qui explique sans doute la mention « météo prévue » de XGBoost. Les scores LightGBM et CatBoost actuels sont encore en météo mesurée : à refaire avec la météo prévue pour une comparaison équitable.
