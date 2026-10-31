# Squelette de la base POWER

Document à partager avec l'équipe : tout le monde entraîne ses modèles (CatBoost, LightGBM, XGBoost) sur **la même table**, avec **le même découpage**. Sinon les scores ne sont pas comparables.

## 1. Source de la consommation

- **RTE éCO2mix, données nationales consolidées et définitives**, diffusées sur ODRÉ (jeu `eco2mix-national-cons-def`). Gratuit, sans compte ni clé d'API.
- Pas de temps d'origine : 15 min pour la consommation (30 min pour les filières sur les années anciennes).
- Le fichier brut est gardé tel quel dans `donnees/brut/`. On ne le corrige jamais à la main.

## 2. Une ligne = une heure, repérée en UTC

Pourquoi UTC et pas l'heure de Paris ? Exemple concret, nuit du 31 mars 2019 (passage à l'heure d'été) :

| heure_utc | heure_locale (Paris) |
|---|---|
| 2019-03-31 00:00 | 01:00 |
| 2019-03-31 01:00 | **03:00** (02:00 n'existe pas) |
| 2019-03-31 02:00 | 04:00 |

Et le 27 octobre 2019, l'heure locale 02:00 apparaît **deux fois**. En heure de Paris on aurait donc un trou en mars et un doublon en octobre ; en UTC la suite est parfaite, toujours 1 h d'écart. Les variables de calendrier, elles, sont calculées en heure de Paris, car c'est l'heure de Paris qui règle la vie des gens (lever, bureau, repas).

`heure_utc` = **début de l'heure** (07:00 = de 07:00 à 08:00 UTC). Dans le fichier RTE, la conso n'est remplie qu'à :00 et :30 (vide à :15 et :45) : la valeur horaire est la moyenne de ces deux valeurs, en MW (donc une puissance moyenne sur l'heure). La météo et le calendrier sont aussi horaires : tout se joint directement sur `heure_utc`.

**Changements d'heure dans le fichier RTE** (vérifié sur les vraies données) :
- Au printemps, RTE ajoute des lignes fictives pour l'heure qui n'existe pas (02:00 à 02:45, heure de Paris), avec des valeurs recopiées et le même horodatage UTC que 03:00 à 03:45. Le script les retire (4 lignes par an).
- À l'automne, RTE ne publie qu'une seule fois l'heure 02:xx. Il manque donc une heure par an dans la base (00:00 UTC le jour du passage à l'heure d'hiver, 14 heures sur 2012-2025). Elle reste vide : c'est à Yassine de décider comment la combler.

Période : du 1er janvier 2012 00:00 au 31 décembre 2025 23:00 (heure de Paris), soit **122 736 lignes** (14 ans, dont 12 hivers d'entraînement) : train 105 192, valid 8 784, test 8 760.

Découpage (colonne `split`) : **train 2012-2023, valid 2024, test 2025**. Les trois périodes sont dans la base. La validation sert à régler les modèles, le test n'est utilisé qu'une fois à la fin pour comparer CatBoost, LightGBM et XGBoost.

RTE n'a **aucune donnée avant le 1er janvier 2012**. Les premiers jours de 2012 n'ont donc pas de retard : pas de lag 48 h sur les 2 premiers jours (48 heures), pas de lag 7 jours sur la première semaine (168 heures), soit 0,16 % du train. On les laisse vides : CatBoost, LightGBM et XGBoost gèrent les valeurs manquantes.

## 3. Colonnes de la table `base_power_horaire.parquet`

Le squelette fixe **toutes** les colonnes dès maintenant, pour que chacun sache où écrire. Celles marquées « vide » existent avec le bon type mais sont remplies par le membre indiqué, selon la répartition du groupe : créer la base = maxou, nettoyage et encodage = Yassine, choix des features = Amine et Hugo.

| Colonne | Type | Rôle | Rempli par | Exemple |
|---|---|---|---|---|
| `heure_utc` (index) | datetime UTC | clé de jointure | base (maxou) | 2024-01-15 07:00+00:00 |
| `heure_locale` | datetime | lecture humaine | base (maxou) | 2024-01-15 08:00 |
| `split` | catégorie | train 2012-2023, valid 2024, test 2025 (convention de groupe) | base (maxou) | valid |
| **`conso_mw`** | float32 | **cible** | base (maxou) | 78 450 |
| `conso_imputee` | bool | heure dont la conso a été corrigée/imputée | vide, nettoyage (Yassine) | False |
| `conso_suspecte` | bool | valeur aberrante repérée | vide, nettoyage (Yassine) | False |
| `prevision_rte_j1_mw` | float32 | prévision J-1 de RTE : **référence à battre, pas une variable d'entrée** | base (maxou) | 77 900 |
| `prevision_rte_j_mw` | float32 | prévision du jour même de RTE (référence) | base (maxou) | 78 200 |
| `conso_lag_48h`, `conso_lag_168h` | float32 | conso 48 h et 7 jours avant (48 et 168 lignes) | vide, features (Amine / Hugo) | 76 100 |
| `heure`, `jour_semaine` (0 = lundi), `mois`, `jour_annee`, `semaine_iso`, `annee` | entiers | calendrier, en heure de Paris | vide, features (Amine / Hugo) | 8, 0, 1, 15, 3, 2024 |
| `est_weekend`, `est_heure_ete`, `est_ferie`, `est_pont` | bool | calendrier | vide, features (Amine / Hugo) | False |
| `nom_ferie` | texte | nom du jour férié | vide, features (Amine / Hugo) | "" |
| `vacances_zone_a/b/c` | bool | vacances scolaires | vide, features (Amine / Hugo) | |
| `temp_prevue_c`, `nebulosite_prevue_pct`, `vent_prevu_ms`, `rayonnement_prevu_wm2` | float32 | météo **prévue** | vide, features (Amine / Hugo) | |

Production par filière (nucléaire, éolien, solaire, hydraulique, gaz, etc.) : dans un fichier à part, `production_filieres_horaire.parquet`, même index. Elle sert au dashboard (le sujet demande de choisir une filière), **pas aux modèles** : la production de l'heure H n'est pas connue la veille, l'utiliser comme variable serait de la triche.

## 4. Trois règles à ne pas casser

1. **Pas de variable inconnue au moment de la prévision.** On prévoit la veille pour le lendemain : la conso de la veille n'est pas encore entièrement mesurée, d'où des retards d'au moins 48 h. Un `lag_24h` donnerait un score trop beau et faux.
2. **Météo prévue, pas mesurée.** La veille, on ne connaît que la prévision de température.
3. **Découpage chronologique, pas aléatoire.** Mélanger les heures au hasard laisse le modèle « voir » le voisin de l'heure à prédire.

## 5. Qualité des données (pour Yassine)

La base livrée contient la conso **brute** horaire : rien n'est corrigé. Le script écrit `donnees/base/etat_du_brut.txt`, un simple constat pour démarrer le nettoyage : doublons, pas de temps rencontrés, heures sans conso par année, min / moyenne / max, et vérification des jours de 23 h et 25 h (changements d'heure).

## 6. Ce qui reste aux autres membres (Yassine, Amine, Hugo)

- Calendrier, jours fériés, ponts, vacances scolaires, retards de conso : à calculer **en heure de Paris** pour le calendrier, à partir de `heure_utc` pour les retards.
- Météo **prévue** à l'heure pour la France, **à partir de 2012** (par exemple moyenne de plusieurs grandes villes pondérée par la population), jointe sur `heure_utc`.
- Option : version régionale (le sujet évoque « une région ou la France entière »), avec le jeu éCO2mix régional.

## 7. Lancer les scripts (depuis le dossier POWER)

```
pip install -r code/requirements.txt
python code/donnees/01_telecharger_rte.py
python code/donnees/02_construire_base.py
python code/donnees/03_charger_postgres.py   # charge dans PostgreSQL, a besoin de POWER/.env
```

Le fichier `POWER/.env` contient une seule ligne `DATABASE_URL=postgresql://...` (adresse et mot de passe de la base du groupe). Il ne doit jamais aller sur Git.
