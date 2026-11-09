# Journal de bord : Hugo

## 2026-10-30 : script météo Météo-France

- **Objectif** : récupérer une météo horaire par région et pour la France, alignée sur la base RTE (UTC, pas horaire).
- **Ce qui a été fait** : écriture de `code/donnees/04_meteo_meteofrance.py`, avec une station par région (`donnees/stations_meteo.csv`) et une moyenne France pondérée par la population. Nettoyage : codes douteux, valeurs hors bornes, pics de température, doublons, trous de 3 h au plus bouchés.
- **Résultat** : Marche. 36 fichiers téléchargés, les 12 stations trouvées.
- **Chiffres** : période 2018-12-20 → 2026-01-01 (61 680 h), aucune heure sans température pour la France, rayonnement moyen à midi en juin de 691 W/m².
- **Suite** : nébulosité inutilisable (voir l'entrée suivante).

## 2026-10-30 : nébulosité et stations de secours

- **Objectif** : comprendre une nébulosité France moyenne de 95 % et des trous de rayonnement.
- **Ce qui a été fait** : lecture des fichiers bruts. Les stations automatiques ne notent la nébulosité que par temps nuageux (6, 7 ou 8 octas), et laissent la case vide par ciel clair. Ajout d'une station de secours par région.
- **Résultat** : Partiel. Nébulosité retirée. Lille : Valenciennes en secours, le rayonnement passe de 100 % vide à 56 h vides. Normandie : aucune station du 76 ne mesure le rayonnement avant 2022, le secours Le Havre a été retiré.
- **Chiffres** : Lille 61 680 h vides → 56 h vides.
- **Suite** : ajouter éventuellement une station du Calvados en secours pour la Normandie.

## 2026-10-30 : bug pandas 3 sur la station de secours

- **Objectif** : lancer le script sur le Mac.
- **Ce qui a été fait** : le script plantait (`'float' object has no attribute 'zfill'`) : avec pandas 3, une case vide est lue comme NaN. Correction avec `pd.notna`.
- **Résultat** : Marche après correction.
- **Chiffres** : aucun.
- **Suite** : ne plus pousser le code sans l'avoir lancé.

## 2026-11-02 : météo étendue à 2012

- **Objectif** : aligner la météo sur la base, passée à 2012-2025.
- **Ce qui a été fait** : début par défaut du script changé en 2011-12-20, `donnees/meteo.md` mis à jour.
- **Résultat** : à vérifier, le traitement sur 2012-2025 n'a pas encore été lancé.
- **Chiffres** : aucun.
- **Suite** : lancer le script et vérifier `etat_meteo.txt` avant de pousser.

## 2026-11-04 : présentation

- **Objectif** : finaliser la présentation du groupe.
- **Ce qui a été fait** : affichage progressif sur toutes les slides, slides XGBoost d'Amine remises au style commun, slides LightGBM et base de données allégées, nouvelles slides d'encodage, frise retirée de la comparaison, point 5 ajouté au sommaire.
- **Résultat** : Marche. `Présentation/POWER_presentation_complete.pdf`, 48 pages.
- **Chiffres** : aucun.
- **Suite** : sans les `.tex` d'origine, les étapes sont faites en masquant le PDF ; repartir des sources pour les modifications lourdes.

## 2026-11-09 : feature événements exceptionnels

- **Objectif** : marquer les événements qui font sortir la conso de la normale (crises, grèves, compétitions, météo extrême), sous une forme facile à encoder.
- **Ce qui a été fait** : liste de 73 événements 2012-2025 dans `donnees/evenements.csv` (une ligne par période, heure de Paris, intensité 1 à 3, colonnes `connu_la_veille` et `verifie`), et script `code/donnees/07_evenements.py` qui produit `donnees/base/evenements_horaire.parquet` et `.csv` (une colonne `evt_<categorie>` par catégorie, entiers 0 à 3) et `evenements_periodes.parquet` au format de la table `evenement` du schéma.
- **Résultat** : Marche. 8 catégories : sanitaire, energie, geopolitique, social, reglementaire, sport, meteo, choc_national.
- **Chiffres** : 122 736 heures ; 14 événements à vérifier (surtout les dates des canicules) ; 148 h marquées imprévisibles (attentats, à exclure des variables de prédiction).
- **Suite** : vérifier les dates des canicules sur Météo-France ; mesurer l'apport de chaque catégorie par ablation.

## 2026-11-09 : vérification des dates des événements

- **Objectif** : sourcer les 14 événements marqués « à vérifier » et retirer ceux qu'on ne peut pas confirmer.
- **Ce qui a été fait** : recherche dans les bilans climatiques Météo-France (étés 2015, 2019, 2022, 2023, 2025, année 2017), Santé publique France (2018), Enedis (tempête Ciarán), RTE (bilan électrique 2024) et INSEE (confinement 2020).
- **Résultat** : Marche. 13 événements confirmés, avec dates corrigées de 1 à 2 jours pour certaines canicules. Froid de février 2021 retiré : pas une vague de froid nationale, dates discordantes selon les sources. Ajout de la 2e vague de chaleur de 2015 (15-22 juillet).
- **Chiffres** : 73 événements, 0 à vérifier. Confinement 1 : conso environ 14 % sous la normale (RTE). Conso 2024 environ 6 % sous la moyenne 2014-2019 (RTE).
- **Suite** : slides de présentation de la feature ; ablation par catégorie.

## 2026-11-09 : feature jours Tempo

- **Objectif** : ajouter la couleur Tempo du jour (bleu, blanc, rouge) comme indicateur, et savoir si elle varie selon la région.
- **Ce qui a été fait** : script `code/donnees/08_tempo.py`. Il télécharge le calendrier Tempo de RTE éCO2mix par saison (secours : jourtempo.fr, depuis 2014), puis écrit `donnees/base/tempo_jours.parquet/.csv` et `tempo_horaire.parquet/.csv` (colonnes `tempo_bleu`, `tempo_blanc`, `tempo_rouge` en 0/1, jour Tempo de 6 h à 6 h, heure de Paris convertie en UTC) et `etat_tempo.txt`. Testé seulement sur des données fictives : le réseau était bloqué depuis la session Claude.
- **Résultat** : Partiel. Tempo est national (une couleur pour toute la France) : pas de colonne région. Téléchargement réel pas encore lancé.
- **Chiffres** : aucun pour l'instant (attendu par saison : 22 rouges, 43 blancs).
- **Suite** : lancer le script, vérifier `etat_tempo.txt`. La couleur est choisie par RTE d'après sa prévision de conso : à tester par ablation, et seulement pour prévoir J+1.

## 2026-11-09 : PDF séparé événements et Tempo

- **Objectif** : présenter les deux nouveaux indicateurs sans toucher à la présentation existante.
- **Ce qui a été fait** : `Présentation/POWER_features_evenements_tempo.pdf` (titre + slides, même DA, affichage progressif) : méthode événements, catégories, frise 2012-2025, principe Tempo. Slides événements retirées de `POWER_presentation_complete.pdf`, remise à la version du dépôt.
- **Résultat** : Marche. 17 pages avec les étapes, puis slide « Tempo : comment on l'a construit » retirée à la demande : 13 pages, 4 slides + titre.
- **Chiffres** : aucun.
- **Suite** : ajouter un calendrier Tempo réel une fois le script lancé.
