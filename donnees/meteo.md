# Météo POWER (Amine / Hugo)

Script : `code/donnees/04_meteo_meteofrance.py`. Même conventions que la base RTE : une ligne = une heure, `heure_utc`, rien n'est modifié dans `donnees/brut/`.

```
python code/donnees/04_meteo_meteofrance.py
```

Premier lancement : téléchargement de 20 à 30 fichiers (plusieurs centaines de Mo au total). Ensuite, `--sans-telechargement` relance seulement le traitement.

## 1. Source

Météo-France, « Données climatologiques de base - horaires » (data.gouv.fr). Mesures validées des stations, un fichier par département et par période, heures en UTC.

## 2. Une station par région

Fichier `donnees/stations_meteo.csv` : une grande station synoptique (aéroport en général) pour chacune des 12 régions RTE, avec la population de la région. On peut ajouter des lignes : plusieurs stations d'une même région sont moyennées.

Colonne `num_poste_secours` (facultative) : une 2e station du même département, qui sert **uniquement** à boucher les heures vides de la principale. Utilisée pour Lille-Lesquin, qui ne mesure ni rayonnement ni ensoleillement : Valenciennes les fournit.

Limite connue : en Normandie, aucune station du département 76 ne mesure le rayonnement avant 2022 (Rouen-Boos commence en 2022, Le Havre-Octeville en 2023). Sur 2019-2021, la moyenne France du rayonnement se fait donc sans la Normandie (environ 5 % du poids), les poids des autres régions étant recalculés. Amélioration possible : ajouter une station du Calvados (14) en secours.

Si un numéro de station n'existe pas dans les fichiers, le script prend la station la plus proche qui a des mesures complètes, et le signale.

## 3. Variables

| Colonne | Source Météo-France | Unité | Remarque |
|---|---|---|---|
| `temperature_c` | T | °C | instantanée à l'heure pile |
| `humidite_pct` | U | % | instantanée |
| `vent_ms` | FF | m/s | vent moyen sur 10 min |
| `rayonnement_wm2` | GLO | W/m² | J/cm² sur l'heure × 10 000 / 3 600 |
| `ensoleillement_min` | INS | min | minutes de soleil dans l'heure |
| `precipitation_mm` | RR1 | mm | pluie dans l'heure |

**Pas de nébulosité** : les stations automatiques Météo-France ne la notent que quand elles détectent des nuages (6, 7 ou 8 octas) et laissent la case vide par ciel clair. La moyenne serait donc fausse (~95 %). Le rayonnement et l'ensoleillement mesurent directement la lumière reçue, nuages compris, et la remplacent.

GLO, INS et RR1 sont des cumuls sur l'heure **précédente** (HH-1 → HH) : ils sont recalés sur le début de l'heure, comme la conso (`heure_utc` = début de l'heure).

## 4. Nettoyage (station par station)

1. Code qualité 2 (« douteux ») : valeur retirée.
2. Valeur hors bornes physiques (ex. T hors de [-30, 45] °C) : retirée.
3. Pic de température isolé (> 8 °C d'écart avec l'heure d'avant **et** d'après) : retiré.
4. Heure en double (deux fichiers qui se chevauchent) : on garde la première valeur.
5. Trous de 3 h ou moins : interpolation linéaire. Trous plus longs : **laissés vides**.

Tout est compté, station par station, dans `donnees/base/etat_meteo.txt`.

## 5. France entière (`code_region = 'FR'`)

Moyenne des 12 régions **pondérée par la population**, car c'est là que se trouvent les chauffages. Si une région n'a pas de valeur à une heure, les poids sont recalculés sur les autres (sinon la moyenne chuterait d'un coup).

## 6. Sorties (`donnees/base/`, pas sur Git)

- `meteo_observee_horaire.parquet` : index (`heure_utc`, `code_region`), 12 régions + 'FR'. Même forme que la table `meteo_prevue` du schéma.
- `meteo_fr_horaire.csv` : France seule, pour joindre directement à `base_power_horaire` sur `heure_utc`.
- `meteo_stations_horaire.parquet` : détail par station.
- `etat_meteo.txt` : constat + contrôles d'unités.

## 7. Météo observée ≠ météo prévue

La règle 2 du squelette demande la météo **prévue** la veille. Météo-France ne publie pas gratuitement d'archive de ses prévisions sur 2019-2025. Ce script fournit donc la météo **mesurée**, la seule disponible sur tout l'historique.

Conséquence : le score sera un peu trop optimiste, car en vrai la prévision de température J+1 se trompe d'environ 1 °C. À trancher en groupe :
- assumer « météo observée » et le dire dans le rapport (pratique courante) ;
- ou ajouter un bruit d'environ 1 °C sur la température du test pour simuler l'erreur de prévision ;
- ou, pour la prévision réelle (après janvier), utiliser une API de prévision au même format.
