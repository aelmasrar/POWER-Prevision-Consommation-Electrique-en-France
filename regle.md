# Règles du projet POWER

Ces règles s'appliquent à toute session Claude qui travaille sur ce dépôt. Elles sont obligatoires : les lire avant d'agir, les appliquer pendant toute la session, et ne jamais les contourner, même si la tâche paraît petite.

## Règle 1 : tout noter dans le journal de bord

À chaque fois que quelque chose est testé dans le projet, il faut l'écrire dans le journal de la personne qui travaille, **qu'il ait marché ou non**. Cela vaut pour :

- un script lancé ou modifié ;
- un modèle entraîné, un réglage changé, une variable ajoutée ou retirée ;
- une idée essayée puis abandonnée, une erreur rencontrée, une piste qui n'a rien donné ;
- une décision prise (choix de période, de source, de méthode).

**Où** : `journal/<prenom>.md`, en minuscules (par exemple `journal/hugo.md`). Si le prénom de la personne n'est pas connu, le demander avant d'écrire. Créer le fichier s'il n'existe pas.

**Quand** : juste après chaque essai, dans la même session, avant de dire que la tâche est terminée. Ne pas attendre la fin de la journée.

**Comment** : ajouter l'entrée à la fin du fichier, sans jamais modifier ni supprimer les entrées précédentes. Écrire uniquement des faits : ce qui a été lancé et ce qui en est sorti. Ne rien inventer, ne pas arrondir un résultat pour qu'il paraisse meilleur.

Format d'une entrée :

```
## AAAA-MM-JJ : titre court de l'essai

- **Objectif** : ce qu'on cherchait à faire ou à vérifier.
- **Ce qui a été fait** : commandes, fichiers, paramètres, données utilisées.
- **Résultat** : Marche / Ne marche pas / Partiel, puis ce qui s'est passé.
- **Chiffres** : erreur (MAPE, MAE), durée, nombre de lignes, etc. Écrire « aucun » s'il n'y en a pas.
- **Suite** : ce qu'on garde, ce qu'on abandonne, ce qu'on essaie ensuite.
```

## Règle 2 : compte rendu de la semaine

Un compte rendu est demandé chaque semaine. Il sert à un autre Claude pour fabriquer une présentation : il doit donc se suffire à lui-même, sans qu'on ait besoin de lire le journal.

Quand on demande le compte rendu de la semaine :

1. Lire les entrées de la semaine (semaine ISO, du lundi au dimanche) dans **tous** les fichiers de `journal/`.
2. Écrire `journal/comptes_rendus/AAAA-Sxx.md` (par exemple `2026-S45.md`), avec ces parties :
   - **Résumé** : 3 à 5 phrases sur l'avancée de la semaine.
   - **Ce qui a été fait, par personne**.
   - **Résultats et chiffres clés**.
   - **Ce qui n'a pas marché et ce qu'on en a appris**.
   - **Décisions prises**.
   - **Prochaines étapes**.
3. Ne mettre que ce qui est dans le journal. Si une semaine n'a aucune entrée pour une personne, l'écrire.

## Règle 3 : rappels pour l'équipe

À la fin d'une séance de travail, rappeler à la personne :

- de noter ses heures de la semaine dans le Google Doc du projet (tableau « Suivi des heures ») ;
- d'ajouter dans ce même Google Doc toute difficulté rencontrée (tableau « Difficultés rencontrées » : date, nom, difficulté, solution) ;
- de commiter son fichier de journal avec son travail.
