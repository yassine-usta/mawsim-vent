# Atelier du vent de Mawsim

Prépare le vent mondial en petites tuiles que l'application Mawsim télécharge pour sa carte du vent.

## Ce que fait l'atelier

Toutes les heures, une tâche planifiée regarde si Open-Meteo a publié un nouveau calcul du modèle ICON
(quatre par jour). Si oui, `prepare.py` :

1. télécharge, heure par heure, le fichier mondial de la prévision (un seul téléchargement par heure) ;
2. y lit le vent à 10 m et les rafales ;
3. écrit des tuiles PNG de 181 x 181 points à trois finesses (1°, 0,5° et 0,25°) ;
4. écrit `index.json`, qui décrit le calcul, les heures et les tuiles.

Le tout est publié sur GitHub Pages. Un calcul pèse environ 130 Mo. L'application ne télécharge que les
tuiles de la partie de la carte qu'elle montre : quelques dizaines de Ko par image.

Heures préparées : heure par heure sur 48 h après le calcul, puis toutes les trois heures jusqu'à sept
jours et demi.

## Lire une tuile

Le nord est en haut. Chaque tuile partage sa dernière ligne et sa dernière colonne avec ses voisines.

- rouge : vent vers l'est (u), de -50 à 50 m/s ;
- vert : vent vers le nord (v), de -50 à 50 m/s ;
- bleu : rafales, de 0 à 70 m/s.

Codage par une racine, fin près de zéro : pour le rouge et le vert, `x = (octet - 127,5) / 127,5`, puis
`valeur = signe(x) * x² * 50` ; pour le bleu, `x = octet / 255`, puis `valeur = x² * 70`.

## La mer

`mer.py` ajoute deux familles de tuiles, prises dans les modèles océaniques de Météo-France que publie
Open-Meteo (un douzième de degré, mondiaux) :

- `m/` : température de l'eau, toutes les 6 heures. Rouge et vert : centièmes de degré depuis -5 °C, sur
  deux octets (fort puis faible).
- `c/` : courants de surface, toutes les 3 heures. Rouge : courant vers l'est ; vert : courant vers le nord,
  de -3 à 3 m/s, codés par la même racine que le vent.

Dans les deux, le bleu dit ce que vaut le point : 0, pas de valeur (la terre) ; 255, le large (les quatre
mailles du modèle autour du point sont en mer) ; 128, une valeur de bord. Les valeurs sont prolongées de
trois mailles vers la terre, pour que l'application colorie la mer jusqu'à la vraie côte et efface
elle-même ce qui dépasse. Elle ne garde que les eaux reliées au large : une lagune fermée, que le modèle
ne voit pas, reste sans couleur.

La mer a un niveau de plus que le vent, le niveau 3, au douzième de degré : tuiles de 15° de côté (288 pour
le monde). `index.json` décrit ces familles sous `layers.sea` et `layers.current`, avec leurs propres
niveaux et leurs heures. Une famille qui n'a pas pu être préparée est simplement absente de l'index.

## Essayer sur son ordinateur

```
pip install -r requirements.txt
python prepare.py sortie --limit 2
```

## Sources et licence des données

Données météo : [Open-Meteo](https://open-meteo.com) (CC BY 4.0), modèle ICON du Deutscher Wetterdienst.
Open-Meteo ne garantit ni l'exactitude ni la continuité de ces fichiers.
