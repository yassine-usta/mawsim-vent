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

## Essayer sur son ordinateur

```
pip install -r requirements.txt
python prepare.py sortie --limit 2
```

## Sources et licence des données

Données météo : [Open-Meteo](https://open-meteo.com) (CC BY 4.0), modèle ICON du Deutscher Wetterdienst.
Open-Meteo ne garantit ni l'exactitude ni la continuité de ces fichiers.
