# Journal de bord : Momo

## 2026-11-17 : LightGBM relancé sur la base mise à jour (événements + Tempo)

- **Objectif** : recharger `v_base_modele` après l'ajout des événements exceptionnels et des jours Tempo, adapter l'encodage des catégories, relancer LightGBM et refaire les 6 figures.
- **Ce qui a été fait** :
  - Base rechargée : 122 736 lignes, 39 colonnes, 2012 → 2025. Nouvelles colonnes : `tempo_couleur` (texte) + `tempo_bleu/blanc/rouge` (0/1), et 9 colonnes `evt_*` (entiers 0 à 3 : sanitaire, énergie, géopolitique, social, réglementaire, sport, météo, choc national, imprévisible).
  - Constats : Tempo vide de 2012 à septembre 2014 (19 % des lignes) ; `conso_mw` n'a plus de valeur vide (14 avant) ; `prevision_j1_mw` a toujours 14 vides. Avec pandas 3, les textes arrivent en dtype `str`, que LightGBM refuse.
  - `train_lightgbm.py` modifié : toute colonne texte → `fillna("aucun")` → `category` sur tout le DataFrame avant le découpage (pas de one-hot) ; assert qu'aucune colonne texte ne reste ; `tempo_bleu/blanc/rouge` exclues (doublons de `tempo_couleur`) ; `prevision_j_mw` exclue.
  - Exclues par la règle des 48 h : `tempo_couleur` (publiée vers 10 h 30 la veille, et choisie d'après une prévision de conso) et `evt_imprevisible` (attentats). Variante `VARIANTE=avec_tempo` pour les tester quand même.
  - Ajout du R² et du biais ; scores et figures sur les seules heures où conso et prévision RTE existent (8 783 h) ; figures renommées `lightgbm_01_semaines.png` … `lightgbm_06_erreurs.png`, ordre de la convention (02 = par mois, 03 = par heure).
  - Mêmes paramètres qu'avant. Trois entraînements : version retenue, contrôle sans `evt_*` ni Tempo, variante avec Tempo.
- **Résultat** : Marche. Les colonnes d'événements font passer la MAPE de 2,39 % à 1,87 % et corrigent le biais (+782 → −144 MW). LightGBM bat RTE tous les mois de 2024, hiver compris. Mais l'arrêt anticipé ne se déclenche plus (3 000 arbres atteints) et l'écart train / validation grandit (RMSE 497 contre 1 251 MW).
- **Chiffres** (validation 2024, 8 783 h) :
  - Retenu (34 variables) : MAE 952 MW, RMSE 1 251 MW, R² 0,9843, MAPE 1,87 %, biais −144 MW, 3 000 arbres (pas d'arrêt), RMSE train 497 MW.
  - Contrôle sans `evt_*` ni Tempo (26 variables) : MAE 1 232 MW, RMSE 1 675 MW, R² 0,9718, MAPE 2,39 %, biais +782 MW, arrêt à 1 454 arbres, RMSE train 741 MW.
  - Avec Tempo et `evt_imprevisible` (hors règle) : MAE 913 MW, RMSE 1 194 MW, R² 0,9857, MAPE 1,80 %, biais −128 MW, 3 000 arbres.
  - RTE J-1 : MAE 1 428 MW, RMSE 1 766 MW, R² 0,9687, MAPE 2,86 %, biais −1 146 MW. Naïve 7 j : MAPE 6,09 %.
  - Importance (gain), version retenue : lag_168h 59 %, lag_48h 23 %, temperature_c 6 %, heure 3 %, est_jour_ouvre 3 %. Les `evt_*` ont un gain faible (< 0,3 %) alors qu'ils changent nettement le score.
  - Durée : environ 12 min par entraînement, 9 min pour lire la base sur Neon.
- **Suite** :
  - Le gain vient sans doute de `evt_energie` et `evt_geopolitique`, à 1 en continu depuis 2022 : ils jouent le rôle d'un indicateur « après 2022 », ce qui corrige le biais. À confirmer en les retirant un par un.
  - Tempo n'apporte que 0,07 point de MAPE et viole la règle des 48 h : on le laisse de côté.
  - Monter `n_estimators` ou le learning_rate pour retrouver un arrêt anticipé, puis tester num_leaves 31 / min_child_samples 100 contre le sur-apprentissage.
  - Vérifier avec l'équipe comment `evt_meteo` a été daté (vigilance annoncée à l'avance, ou périodes constatées après coup ?).

## 2026-11-17 : quelle colonne evt_* fait gagner LightGBM ?

- **Objectif** : savoir d'où vient le gain de MAPE (2,39 % → 1,87 %) obtenu en ajoutant les colonnes `evt_*`.
- **Ce qui a été fait** : même préparation et mêmes paramètres que `train_lightgbm.py`, early stopping sur 2024. Un modèle de base sans aucun `evt_*`, puis la base + un seul `evt_*` à la fois (8 essais, `evt_imprevisible` toujours exclu), puis la base + `evt_energie` et `evt_geopolitique` ensemble. Scores sur les 8 783 heures communes avec RTE. Pour aller vite, pas d'évaluation sur le train pendant l'entraînement.
- **Résultat** : Marche. Tout le gain vient de `evt_energie` ou de `evt_geopolitique` : chacun seul suffit, et les deux ensemble n'apportent rien de plus. Ces deux colonnes valent 1 sans interruption depuis 2022 (fév. 2022 pour la géopolitique, oct. 2022 pour l'énergie), donc pendant toute la validation 2024 : elles servent d'indicateur « période après 2022 » et corrigent le biais dû à la baisse de la conso. Les autres `evt_*` ne changent rien ; `evt_sport` et `evt_choc_national` ne sont même jamais utilisés (scores identiques à la base) ; `evt_sanitaire` dégrade légèrement.
- **Chiffres** (validation 2024) :

  | Ajout à la base | MAE (MW) | RMSE (MW) | R² | MAPE | Biais (MW) | Arbres |
  |---|---|---|---|---|---|---|
  | aucun | 1 232 | 1 675 | 0,9718 | 2,39 % | +782 | 1 454 |
  | evt_sanitaire | 1 258 | 1 716 | 0,9704 | 2,43 % | +824 | 763 |
  | evt_energie | 928 | 1 217 | 0,9851 | 1,83 % | −194 | 2 436 |
  | evt_geopolitique | 936 | 1 245 | 0,9844 | 1,85 % | −100 | 1 597 |
  | evt_social | 1 231 | 1 674 | 0,9719 | 2,38 % | +789 | 1 018 |
  | evt_reglementaire | 1 229 | 1 670 | 0,9720 | 2,38 % | +778 | 1 039 |
  | evt_sport | 1 232 | 1 675 | 0,9718 | 2,39 % | +782 | 1 454 |
  | evt_meteo | 1 236 | 1 680 | 0,9716 | 2,39 % | +787 | 1 312 |
  | evt_choc_national | 1 232 | 1 675 | 0,9718 | 2,39 % | +782 | 1 454 |
  | energie + geopolitique | 932 | 1 227 | 0,9849 | 1,84 % | −179 | 2 740 |

  Durée : 11 à 31 s par entraînement sans évaluation sur le train, contre environ 12 min avec : c'est le suivi du RMSE train à chaque arbre qui coûtait le temps.
- **Suite** :
  - Le gain est légitime (la crise énergétique et la guerre sont connues à l'avance), mais il faut le présenter honnêtement : c'est un **indicateur de régime**, appris sur seulement 2 ans (2022-2023), et non l'effet d'un événement précis. Il suppose que 2025 reste dans le même régime.
  - Garder un seul des deux (`evt_energie` donne le meilleur score) pour simplifier ; retirer `evt_sport`, `evt_choc_national` et `evt_sanitaire`, qui n'apportent rien.
  - Comparer avec l'autre piste prévue contre le biais : `sample_weight` sur les années récentes, sans `evt_*`.
  - Dans `train_lightgbm.py`, calculer la courbe RMSE train après coup (prédictions par paliers d'arbres) au lieu de l'évaluer à chaque itération, pour passer de 12 min à moins d'une minute.
  - Prévenir l'équipe (XGBoost, CatBoost) : sans `evt_energie` ou `evt_geopolitique`, la comparaison entre modèles ne sera pas équitable.

## 2026-11-17 : chercher les couleurs Tempo manquantes (2012 → août 2014)

- **Objectif** : remplir les 19 % de lignes où Tempo est vide dans `v_base_modele` (du 31/12/2011 au 31/08/2014).
- **Ce qui a été fait** :
  - Lu `code/donnees/08_tempo.py` : il prend d'abord le calendrier Tempo d'éCO2mix (RTE), saison par saison, et se rabat sur jourtempo.fr (historique depuis le 01/09/2014 seulement).
  - Retéléchargé à la main les fichiers éCO2mix des saisons 2011-2012, 2012-2013, 2013-2014 et 2014-2015 (`downloadCalendrierTempo?season=aa-bb`) : réponses correctes, un .xls tabulé par saison.
  - Compté les couleurs par saison et comparé la saison 2014-2015 à la base (jour Tempo de 6 h à 6 h, heure de Paris).
  - Recherche web d'une autre source (data.gouv.fr, jourtempo, sites de comparateurs, forums).
- **Résultat** : Ne marche pas. Les fichiers éCO2mix d'avant septembre 2014 sont complets en dates mais contiennent **uniquement des jours BLEU** (0 blanc, 0 rouge), ce qui est impossible : une saison compte 22 rouges et 43 blancs. Ce sont des fichiers vides de sens, à ne surtout pas utiliser. Explication probable : avant le 1er septembre 2014, c'est EDF qui fixait les couleurs ; RTE ne les publie que depuis. Aucune source ouverte trouvée pour les vraies couleurs de 2012 à 2014.
- **Chiffres** :
  - éCO2mix : 2011-2012 = 366 bleus, 2012-2013 = 365 bleus, 2013-2014 = 365 bleus ; 2014-2015 = 300 bleus, 43 blancs, 22 rouges, aucun rouge le week-end.
  - Saison 2014-2015 : 365 jours communs avec la base, 0 désaccord (la base est juste à partir de là).
- **Suite** :
  - Laisser Tempo vide avant septembre 2014 (catégorie « aucun » dans LightGBM), ou n'entraîner la variante Tempo qu'à partir de 2015.
  - De toute façon, `tempo_couleur` est exclue du modèle retenu (règle des 48 h) : ce manque ne change rien au modèle principal.
  - Prévenir le collègue qui a écrit `08_tempo.py` : son test « moins de 300 jours » laisserait passer ces fichiers tout bleus. Vérifier comment la base a fini vide sur cette période (filtre ajouté à la main ?).
  - Seule piste restante pour les vraies couleurs : demander les archives à EDF ou à la CRE.

## 2026-11-17 : Tempo décalé de 48 h

- **Objectif** : voir si une couleur Tempo qui respecte la règle des 48 h (couleur d'il y a 2 jours) aide le modèle.
- **Ce qui a été fait** : nouvelle variable `tempo_lag_48h` = `tempo_couleur` décalée de 48 h sur la grille horaire complète, en catégorie. Trois entraînements avec les paramètres de `train_lightgbm.py` (early stopping 2024) : modèle retenu sans Tempo, + Tempo décalé, + Tempo du jour (hors règle, pour référence). Scores sur les 8 783 h communes avec RTE.
- **Résultat** : Ne marche pas vraiment. Tempo décalé fait gagner 0,02 point de MAPE, mais le modèle ne s'en sert presque pas (gain d'importance arrondi à 0,0 %) : la petite différence vient sans doute du hasard de l'entraînement (sous-échantillonnage des lignes et colonnes), pas de Tempo. Même la couleur du jour, qui triche, ne fait gagner que 0,05 point.
- **Chiffres** (validation 2024) :
  - Sans Tempo : MAE 952 MW, RMSE 1 251 MW, R² 0,9843, MAPE 1,87 %, biais −144 MW, 2 999 arbres.
  - + Tempo décalé 48 h : MAE 938 MW, RMSE 1 233 MW, R² 0,9847, MAPE 1,85 %, biais −159 MW, 2 453 arbres, part du gain de Tempo 0,0 %.
  - + Tempo du jour (hors règle) : MAE 922 MW, RMSE 1 206 MW, R² 0,9854, MAPE 1,82 %, biais −144 MW, 2 985 arbres, part du gain de Tempo 0,7 %.
- **Suite** : Tempo abandonné, sous les deux formes. `lag_48h` et la température portent déjà l'information.

## 2026-11-17 : piste à voir, pondérer les années récentes (sample_weight)

- **Objectif** : corriger le biais dû à la baisse de la conso depuis 2022 sans passer par une étiquette posée à la main (`evt_energie` / `evt_geopolitique`).
- **Ce qui a été fait** : rien encore, piste notée. Constat de départ : en 2024, `evt_energie` et `evt_geopolitique` sont constants toute l'année ; ils ne disent au modèle que « on est après 2022 », ce qui baisse son niveau d'environ 0,8 GW.
- **Résultat** : non testé.
- **Chiffres** : aucun. Repères à battre : sans `evt_*` MAPE 2,39 % (biais +782 MW) ; avec `evt_energie` 1,83 % (biais −194 MW).
- **Suite** :
  - Donner plus de poids aux années récentes via `sample_weight` dans `fit` (par exemple un poids qui croît avec l'année, ou plus fort à partir de 2022), sans mettre l'année en variable (les arbres n'extrapolent pas).
  - Comparer trois versions : `sample_weight` seul, `evt_energie` seul, les deux ensemble.
  - Si `sample_weight` fait aussi bien, le préférer : il ne suppose pas que 2025 reste dans le même « régime » étiqueté.

## 2026-11-17 : LightGBM final (evt_energie seul) et lecture des 6 figures

- **Objectif** : refaire les figures avec les choix retenus (Tempo exclu, une seule colonne d'événement) et comprendre ce que dit chaque graphique.
- **Ce qui a été fait** :
  - `train_lightgbm.py` : ajout de `EVT_GARDES = {"evt_energie"}`, toutes les autres `evt_*` sont exclues ; Tempo reste exclu (règle des 48 h). 27 variables, mêmes paramètres.
  - Relance complète, 6 figures `figures/lightgbm_01` à `06` régénérées, plus des tableaux par mois, par heure, l'importance complète, les pires jours.
  - Analyse à part de l'erreur de la prévision RTE par année et par heure (2011 → 2025).
- **Résultat** : Marche. Le modèle garde le score de la version avec toutes les `evt_*`, avec 7 variables de moins.
- **Chiffres** (validation 2024, 8 783 h) :
  - LightGBM : MAE 949 MW, RMSE 1 252 MW, R² 0,9843, MAPE 1,87 %, biais −189 MW ; arrêt à 2 747 arbres ; RMSE train 542 MW.
  - RTE J-1 : MAE 1 428 MW, MAPE 2,86 %, biais −1 146 MW. Naïve 7 j : MAPE 6,09 %.
  - Importance (gain) : lag_168h 54,1 %, lag_48h 26,5 %, temperature_c 6,3 %, heure 3,6 %, est_jour_ouvre 3,1 %, moy_7j_48h 2,0 %, jour_semaine 2,0 %, jour_annee 0,9 % ; les 19 autres variables font moins de 0,25 % chacune (fin_annee 0,00 %, vacances_zone_a/b/c ≤ 0,02 %, est_weekend 0,03 %, mois 0,03 %). evt_energie : 0,16 % de gain alors qu'elle fait gagner 0,5 point de MAPE.
  - Par mois (MAE LightGBM / RTE, MW) : janv. 1 652 / 1 959, févr. 984 / 1 660, mars 988 / 1 353, avr. 1 190 / 1 611, mai 1 011 / 1 696, juin 557 / 1 302, juil. 534 / 1 257, août 706 / 912, sept. 662 / 1 256, oct. 687 / 1 117, nov. 1 187 / 1 373, déc. 1 228 / 1 652. LightGBM meilleur tous les mois ; ses pires mois : janvier, décembre, avril, novembre.
  - Par heure : LightGBM stable entre 805 (23 h) et 1 095 MW (7 h et 13 h). RTE très irrégulier : 2 411 MW à 0 h, 2 210 à 13 h, 2 008 à 20 h, 748 à 5 h. RTE est meilleur que LightGBM de 5 h à 9 h (ex. 868 contre 1 089 MW à 8 h).
  - Semaine 8-15 janv. : MAE LightGBM 1 978 / RTE 2 132 MW, biais −1 801 / −1 846 MW (vague de froid, les deux sous-estiment). Semaine 8-15 juil. : 396 / 1 350 MW.
  - Erreurs LightGBM : médiane −234 MW, 90 % entre −2 171 et +1 943 MW, 241 heures au-delà de 3 GW. Pires jours : 25/11/2024 (4 397 MW en moyenne), 09/01 (3 511), 18/01 (3 371), 10/05 (2 920).
  - RTE par année : MAPE autour de 1,5 % de 2012 à 2021, puis 1,98 % (2022), 2,73 % (2023), 2,86 % (2024), 2,81 % (2025) ; biais de −115 à −468 MW avant 2022, puis −1 131 MW (2023) et −1 146 MW (2024). Biais RTE à 0 h : −1 372 MW (2015) → −2 408 MW (2024) ; à 13 h : −487 → −2 167 MW.
  - RTE 2024 corrigé de son biais moyen : MAE 1 039 MW. Corrigé du biais propre à chaque heure : MAE 849 MW, MAPE 1,69 %, soit mieux que LightGBM.
- **Suite** :
  - Ne pas dire « LightGBM bat RTE » sans nuance : RTE a un biais systématique, par heure, qui a grossi depuis 2022 (0 h, 13-15 h, 20-21 h). Une fois ce biais retiré, RTE repasse devant. Ce n'est pas un simple décalage horaire (le problème 4 est à reformuler) : chercher avec l'équipe si la conso réelle et la prévision J-1 ont le même périmètre, ou si un changement d'usage (heures creuses, ballons d'eau chaude) explique ces heures.
  - Variables à retirer pour simplifier (gain quasi nul) : fin_annee, vacances_zone_a/b/c (garder nb_zones_vacances), est_weekend, mois ; puis vérifier que le score ne bouge pas.
  - Sur-apprentissage : RMSE train 542 contre 1 252 MW en validation, et la validation est presque plate dès ~1 000 arbres → tester num_leaves 31 et min_child_samples 100.
  - Grands froids (janvier) et pics au-dessus de 75 GW sous-estimés : enrichir la météo (température moyenne sur 24 h / 72 h, écart à la normale).
  - Regarder ce qui s'est passé le 25/11/2024.

## 2026-11-17 : biais horaire de la prévision RTE J-1, depuis quand ?

- **Objectif** : vérifier l'affirmation « RTE a un biais horaire depuis 2022, une fois retiré RTE descend à 1,69 % ».
- **Ce qui a été fait** : sur `v_base_modele` (heures où `prevision_j1_mw` existe), erreur = prévision RTE − réel, moyenne par heure de Paris et par année (2012-2025). Puis correction de la MAPE RTE 2024 en retirant ce biais heure par heure, estimé de deux façons : sur 2022-2023 (honnête) et sur 2024 lui-même (connaît les réponses).
- **Résultat** : Partiel. « Depuis 2022 » est à nuancer :
  - le motif selon l'heure existe depuis 2012 (RTE sous-estime la nuit et l'après-midi, surestime un peu vers 22 h) ;
  - ce qui change : tout le biais descend à partir de 2022, et fortement en 2023, surtout entre 13 h et 15 h ;
  - en 2024, l'écart entre les heures grandit aussi.
  Le 1,69 % est retrouvé, mais il corrige 2024 avec les erreurs de 2024 : ce n'est pas utilisable. La version honnête donne 1,81 %, un peu meilleure que notre LightGBM (1,87 %).
- **Chiffres** :
  - Biais moyen RTE par an (MW) : 2012 −240, 2013 −470, 2014 −450, 2015 −280, 2016 −190, 2017 −130, 2018 −400, 2019 −230, 2020 −120, 2021 −230, 2022 −570, 2023 −1 130, 2024 −1 150, 2025 −970.
  - Biais à 0 h : −1 280 MW (2012), −1 100 (2021), −1 870 (2023), −2 410 (2024). À 13 h : −290 (2012), −620 (2021), −1 720 (2023), −2 170 (2024).
  - Écart-type du biais selon l'heure : environ 380-490 MW de 2012 à 2023, 770 MW en 2024, 810 MW en 2025.
  - MAPE RTE 2024 : brute 2,86 % ; corrigée avec le biais 2022-2023 : 1,81 % ; corrigée avec le biais 2024 : 1,69 %.
- **Suite** :
  - Dans la présentation, remplacer 1,69 % par 1,81 % et dire qu'une bonne part de l'avance de LightGBM sur RTE vient de ce biais de RTE.
  - Chercher la cause du creusement à 13-15 h (hypothèse : solaire, autoconsommation) ; reste lié à la suspicion de décalage horaire dans `prevision_j1_mw` (problème ouvert n°4), à voir avec l'équipe.
  - Prévenir l'équipe pour que les 3 modèles comparent au même RTE.

## 2026-11-17 : comparaison avec les graphiques CatBoost et liste des pistes

- **Objectif** : comparer les 6 figures CatBoost du collègue aux figures LightGBM, et rassembler toutes les pistes d'amélioration dans un document.
- **Ce qui a été fait** : lecture des 6 figures CatBoost (semaines, MAPE par mois, MAPE par heure, importance SHAP, courbe d'apprentissage, erreurs) ; comparaison point par point avec LightGBM ; rédaction de `journal/pistes_et_comparaison.md` (pistes météo mesurée, froid, sur-apprentissage, biais RTE, comparaison, conventions à harmoniser, questions pour l'équipe).
- **Résultat** : Marche. Les deux modèles concordent sur RTE (biais −1,15 GW, mêmes pics à 0 h, 13-16 h, 20-21 h), sur le sur-apprentissage et sur la sous-estimation des pics. CatBoost est nettement meilleur et ne souffre pas en janvier, probablement grâce à ses températures lissées.
- **Chiffres** (lus sur les figures CatBoost) : RMSE validation 968 MW (LightGBM 1 252), entraînement 426 MW, arrêt à 3 106 arbres, biais −0,26 GW ; MAPE semaine de janvier 1,68 %, de juillet 1,08 % (RTE 2,87 % et 2,97 %) ; pire mois CatBoost mai (≈ 2,1 %), janvier ≈ 1,5 % ; importance SHAP : heure 14,4 %, temp_moy_24h 13,9 %, est_jour_ouvre 11,3 %, conso_lag_48h 10,7 %, conso_lag_168h 7,4 %, temperature_c 7,2 %, temp_moy_72h 6,1 %, evt_energie 5,6 %, evt_geopolitique 3,8 % ; familles : passé 21 %, météo 31 %, calendrier 37 %, Tempo et événements 10 %.
- **Suite** :
  - Ajouter à LightGBM `temp_moy_24h`, `temp_moy_72h`, `lag_336h`, puis la régularisation.
  - Passer l'importance de LightGBM en SHAP (le gain n'est pas comparable).
  - Harmoniser avec l'équipe : MAE en MW ou MAPE en %, couleurs par modèle (LightGBM et CatBoost sont tous deux en bleu), couleurs des familles, bornes des semaines, mêmes `evt_*`, Tempo exclu (règle des 48 h).
  - Demander au collègue ses variables exactes et son score global.

## 2026-11-17 : températures lissées 24 h / 72 h et importance SHAP

- **Objectif** : reprendre dans LightGBM les températures lissées qui marchent chez CatBoost, et mesurer l'importance avec SHAP comme lui pour pouvoir comparer.
- **Ce qui a été fait** :
  - `train_lightgbm.py` : ajout de `temp_moy_24h` et `temp_moy_72h` (moyenne glissante de `temperature_c` sur les 24 h / 72 h précédentes, heure courante incluse, sur la grille horaire complète, au moins 75 % d'heures présentes). 29 variables, mêmes paramètres.
  - Importance SHAP : `model.predict(X, pred_contrib=True)` (TreeSHAP natif de LightGBM, même méthode que le paquet `shap`), moyenne des |SHAP| sur la validation 2024, en % du total ; figure 04 refaite avec le total par famille. Le gain reste affiché dans la console.
- **Résultat** : Marche, gros gain. Mais l'arrêt anticipé ne se déclenche plus (3 000 arbres atteints) et la semaine de juillet se dégrade.
- **Chiffres** (validation 2024, 8 783 h) :
  - Avant / après : MAE 949 → 800 MW, RMSE 1 252 → 1 065 MW, R² 0,9843 → 0,9886, MAPE 1,87 → 1,58 %, biais −189 → −127 MW ; RMSE train 542 → 402 MW.
  - Par mois (MAE, MW) : janv. 1 652 → 1 350, déc. 1 228 → 1 093, nov. 1 187 → 918, avr. 1 190 → 881, juin 557 → 460 ; août 706 → 751 (seul mois en recul). MAPE par mois : janv. 2,09 %, mai 1,99 %, avr. 1,80 %, déc. 1,78 %, juil. 1,07 %.
  - Heures froides (< 5 °C, 739 h) : MAE LightGBM 1 322 MW, RTE 1 931 MW.
  - Semaine 8-15 janv. : MAE 1 978 → 1 506 MW, MAPE 2,05 % (CatBoost 1,68 %, RTE 2,87 %). Semaine 8-15 juil. : MAE 396 → 534 MW, MAPE 1,20 % (CatBoost 1,08 %, RTE 2,97 %).
  - Erreurs : 90 % entre −1 679 et +1 701 MW ; 149 heures au-delà de 3 GW (contre 241). Pires jours : 10/05 (3 480 MW), 09/01 (3 217), 28/01, 28/03.
  - SHAP (% du total) : lag_168h 20,5, lag_48h 14,3, temp_moy_24h 13,6, heure 10,7, evt_energie 8,6, est_jour_ouvre 7,4, temperature_c 5,3, jour_semaine 4,9, temp_moy_72h 3,5. Par famille : passé de la conso 35,8 %, calendrier 29,2 %, météo 26,2 %, événements 8,9 %. Avec le gain, les décalages faisaient 72 % : le gain exagère leur poids.
  - Contrôle : la MAPE de RTE par mois et par heure est identique à celle des figures CatBoost (ex. 4,91 % à 0 h, 3,95 % en mai) → les deux modèles sont bien évalués sur les mêmes heures.
- **Suite** :
  - Limite à signaler : `temp_moy_24h` / `72h` sont calculées sur la température **mesurée**, y compris les heures du lendemain qu'on cherche à prévoir. En conditions réelles, une partie de la fenêtre serait une prévision météo. Même réserve que pour `temperature_c`, en plus fort.
  - Retrouver un arrêt anticipé (plus d'arbres ou learning_rate plus haut) et tester la régularisation.
  - Comprendre pourquoi août et la semaine de juillet se dégradent.
  - Écart restant avec CatBoost (RMSE 968 contre 1 065 MW) : il a aussi `lag_336h` et `evt_geopolitique` ; à tester.

## 2026-11-17 : présentation commune LightGBM / CatBoost (Beamer)

- **Objectif** : une présentation qui compare LightGBM et CatBoost (ce qui est identique, ce qui diffère, hypothèses), explique pourquoi on bat RTE et liste les pistes.
- **Ce qui a été fait** : `Présentation/comparaison_modeles/comparaison_lightgbm_catboost.tex` (Beamer 16:9, style des présentations du groupe : bandeau gris, titre sur fond bleu), 17 diapos, avec `figures/` (6 figures LightGBM + 6 figures CatBoost converties en PNG). Installation de Tectonic (Homebrew) pour compiler en local ; vérification visuelle de chaque page.
- **Résultat** : Marche. PDF de 17 pages sans erreur. Corrigé en route : police trop grande (11 → 10 pt), figures trop hautes, guillemets, « °C » et « œ » mal rendus par Tectonic.
- **Chiffres** : aucun nouveau (reprend les entrées précédentes).
- **Suite** : compléter la MAE, le R² et la MAPE globales de CatBoost (marqués n. c.) ; ajouter XGBoost quand ses figures seront prêtes ; harmoniser MW / % et les couleurs entre modèles.

## 2026-11-18 : présentation courte à trois modèles (XGBoost, LightGBM, CatBoost) et tableau de bord

**Objectif** : raccourcir la présentation, y intégrer les résultats XGBoost et la présentation du tableau de bord, avec des apparitions progressives.

**Ce qui a été fait**
- Figures XGBoost extraites du PDF reçu (semaines, MAE par heure, importance, apprentissage) dans `Présentation/comparaison_modeles/figures/xgboost_*.png`.
- Nouveau fichier `comparaison_trois_modeles.tex` : 12 diapos (53 étapes d'apparition), style Beamer de l'équipe. Comparaison seulement sur les indicateurs communiqués ; R² retiré.
- Dashboard repris en 2 diapos (architecture, trois pages).
- Notes détaillées complétées dans `journal/pistes_et_comparaison.md` (sections 8 à 10).

**Résultat** : PDF compilé avec tectonic, dernière étape de chaque diapo vérifiée visuellement.

**Chiffres (validation 2024)** : XGBoost MAE 1 254 MW, MAPE 2,43 %, biais +838 MW ; LightGBM MAE 800 MW, MAPE 1,58 % ; CatBoost RMSE 968 MW ; RTE MAE 1 428 MW, MAPE 2,86 %.

**Suite** : vérifier la « météo prévue » annoncée par XGBoost ; ajouter `evt_energie` et températures lissées à XGBoost ; importance SHAP pour XGBoost.

## 2026-11-18 : ajout des diapos SHAP, Tempo et météo à la présentation

**Objectif** : intégrer les diapos reçues (SHAP sur CatBoost, Tempo retiré, météo mesurée / prévue) à la présentation à trois modèles.

**Ce qui a été fait** : figures SHAP extraites du PDF (`figures/shap_cascade_10janvier.png`, `figures/shap_natif_vs_shap.png`) ; 4 diapos ajoutées dans `comparaison_trois_modeles.tex` (Tempo après le cadre, SHAP avant et après l'importance, météo après « Pourquoi on fait mieux que RTE »), avec apparitions progressives ; encadré XGBoost « météo prévue » relié à la diapo météo.

**Résultat** : 16 diapos (70 étapes), compilées, nouvelles diapos vérifiées visuellement. Reste un débordement de 3 pt sur la diapo SHAP, invisible.

**Chiffres** : aucun nouveau.

**Suite** : évaluer LightGBM sur la météo prévue 2024 (Open-Meteo) pour une comparaison équitable avec RTE.

## 2026-11-18 : planning, difficultés et répartition des rôles dans la présentation

**Objectif** : ajouter à la présentation le diagramme de Gantt mis à jour, une diapo sur les difficultés et une diapo « qui a fait quoi ».

**Ce qui a été fait**
- Diapo « Qui a fait quoi » (2e) : Hugo météo → Momo LightGBM, Maxime CatBoost + SHAP, Yassine XGBoost → Amine tableau de bord.
- Diapo « Les difficultés rencontrées » : données (prévisions météo archivées payantes en grande quantité, archive ECMWF à télécharger pendant des semaines, Tempo absent avant 2014, base modifiée en cours de route), LightGBM (colonnes texte, 12 min d'entraînement, surestimation post-2022, gain trompeur, pas d'arrêt avant 3 000 arbres), comparaison (conventions, indicateurs manquants, météo mesurée).
- Planning repris en TikZ, couleurs plus visibles (vert terminé, bleu en cours, gris à venir, losange rouge jalon, trait orange « aujourd'hui »). Mis à jour au 18/11 : features historiques et Tempo terminés ; SHAP et analyse des erreurs commencés dès la semaine 2 ; ajout de « Météo prévue (Open-Meteo) » et « Test final 2025 (une fois) ».

**Résultat** : 19 diapos (82 étapes), compilées et vérifiées visuellement.

**Chiffres** : aucun nouveau.

**Suite** : faire valider le planning par l'équipe (dates de la météo prévue et du test final proposées, pas décidées).

## 2026-11-18 : retouches de la présentation

**Objectif** : alléger la diapo des difficultés et finir sur le planning.

**Ce qui a été fait** : colonne « La comparaison » retirée de « Les difficultés rencontrées » (restent données et LightGBM, sur deux colonnes) ; planning déplacé en dernière diapo, après « À retenir ».

**Résultat** : 19 diapos (81 étapes), compilées et vérifiées visuellement.

**Chiffres** : aucun.

**Suite** : rien.
