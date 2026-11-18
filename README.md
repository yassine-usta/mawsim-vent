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

## Le feu

`feu.py` ajoute la famille `f/` : le danger de feu, donné par l'indice forêt-météo (FWI, "Fire Weather
Index"), une image par jour, d'aujourd'hui (UTC) au dernier jour que sert le serveur (dix au plus), datée de
12 h UTC.

- Rouge et vert : l'indice sur deux octets (fort puis faible), par pas de 0,01 depuis 0 :
  `indice = (rouge * 256 + vert) * 0,01`.
- Bleu : 0, pas de valeur (la mer, un lac, un chott, le désert ou la glace, que le modèle ne calcule pas) ; 255, pleine
  terre du modèle (les quatre mailles du serveur autour du point ont une valeur) ; 128, une valeur de bord
  ou prolongée vers la mer. L'application ne lit une valeur que si le bleu vaut au moins 128.

Les données viennent du serveur cartographique du GWIS (Global Wildfire Information System) du service de
gestion des urgences de Copernicus : couche `ecmwf.fwi`, calculée par ECMWF, lue en image TIFF du monde
(5120 x 2560 mailles de 0,07°, 52 Mo par jour, une requête par jour). Dans ce TIFF, la valeur 0 veut dire
"pas de valeur". Chaque point des tuiles (1/12 de degré) est interpolé entre les quatre mailles qui
l'entourent, en ne comptant que celles qui ont une valeur, puis les valeurs sont prolongées de deux points
(environ 18 km) vers la mer, et vers la mer seulement : un point vide n'est rempli que s'il est à deux points
au plus d'un point de mer du modèle océanique de `mer.py` (`masque-mer.png`). L'application efface ce qui
dépasse sur l'eau avec la côte de son fond de carte. Les points vides de l'intérieur des terres (désert,
chott, lac, glace) restent sans valeur. Le masque ne couvre ni les grands lacs (lac Victoria) ni la mer
Caspienne : leurs bords ne sont pas prolongés. Quatre niveaux, comme la mer ; environ 330 tuiles et 3 Mo par
jour. `index.json` décrit la famille sous `layers.fire`.

`masque-mer.png` (4320 x 2161 points au 1/12 de degré, nord en haut ; blanc : mer) est fabriqué une fois par
`python masque_mer.py`, à partir d'une heure du modèle `meteofrance_sea_surface_temperature` : les côtes ne
changent pas. S'il manque, le feu est publié sans prolongement.

Le serveur renouvelle ses données une fois par jour, alors que le site est refait à chaque calcul d'ICON :
la tâche planifiée garde `f/` et son entrée d'index dans un cache (une clé par jour UTC), et `feu.py` les
reprend tant que le serveur n'a pas publié de jour de plus. Si le serveur du feu ne répond pas, le vent et
le reste sont publiés quand même, sans la famille `f/` (l'application retombe sur les images du serveur).

Essai, un jour : `python feu.py sortie --days 1`. Essais sans réseau : `python -m unittest test_feu`.

## Essayer sur son ordinateur

```
pip install -r requirements.txt
python prepare.py sortie --limit 2
```

## Sources et licence des données

Données météo : [Open-Meteo](https://open-meteo.com) (CC BY 4.0), modèle ICON du Deutscher Wetterdienst.
Open-Meteo ne garantit ni l'exactitude ni la continuité de ces fichiers.

Danger de feu : "Contient des informations modifiées du service Copernicus de gestion des urgences (2026).
Indice forêt-météo d'ECMWF." L'année est celle des données ; `index.json` porte la même mention dans
`layers.fire.source`. Les conditions du service demandent "Contains modified Copernicus Emergency Management
Service information [Year]" ou une mention proche. Réutilisation sous licence CC BY 4.0
([licence des données d'EFFIS et du GWIS](https://forest-fire.emergency.copernicus.eu/about-effis/data-license),
[conditions du CEMS](https://ewds.climate.copernicus.eu/licences/terms-of-use-cems)). Les valeurs sont
interpolées et prolongées vers la mer par l'atelier. Elles sont fournies à titre d'information : ce ne sont
pas des alertes, que seuls les services nationaux peuvent émettre.
