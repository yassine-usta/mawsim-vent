# Atelier du vent de Mawsim

Prépare les valeurs du monde (vent, air, ciel, mer, feu) en petites tuiles que l'application Mawsim télécharge pour
sa carte.

## Ce que fait l'atelier

Toutes les heures, une tâche planifiée regarde si Open-Meteo a publié un nouveau calcul du modèle ICON
(quatre par jour). Si oui, deux temps, séparés :

1. **Préparer** (`prepare.py`) : télécharge, heure par heure, le fichier mondial de la prévision (un seul
   téléchargement par heure, partagé par les familles qui le lisent), écrit les tuiles PNG de 181 x 181 points
   à trois finesses (1°, 0,5° et 0,25°) puis `index.json`, qui décrit le calcul, les heures et les tuiles. Le site
   est écrit dans un dossier.
2. **Publier** (`publier.py`) : vérifie ce dossier et le publie sur GitHub Pages (voir "Publication").

Un calcul pèse environ 130 Mo. L'application ne télécharge que les tuiles de la partie de la carte qu'elle
montre : quelques dizaines de Ko par image.

## Les familles

Chaque grandeur est une **famille**, un module du dossier `familles/` qui écrit ses tuiles et rend son entrée
d'index. La liste des familles est `FAMILIES`, dans `prepare.py`, et nulle part ailleurs.

| Famille    | Module                 | Source                              | Tuiles | Place dans l'index |
|------------|------------------------|-------------------------------------|--------|--------------------|
| `vent`     | `familles/vent.py`     | ICON : vent à 10 m, rafales         | `t/`   | racine             |
| `air`      | `familles/air.py`      | ICON : température, nébulosité      | `a/`   | `layers.air`       |
| `ciel`     | `familles/ciel.py`     | ICON : pression, pluie              | `s/`   | `layers.sky`       |
| `eau`      | `familles/eau.py`      | Météo-France : température de l'eau | `m/`   | `layers.sea`       |
| `courants` | `familles/courants.py` | Météo-France : courants de surface  | `c/`   | `layers.current`   |
| `feu`      | `familles/feu.py`      | GWIS : danger de feu                | `f/`   | `layers.fire`      |

Les familles `vent`, `air` et `ciel` lisent le même fichier horaire d'ICON : `source_icon.py` le télécharge une
fois par heure et le prête à chacune. `eau`, `courants` et `feu` ont chacune leur source ; leur logique est dans
`mer.py` et `feu.py`, les modules de `familles/` en sont l'entrée.

Options de `prepare.py` : `--limit N` (nombre d'heures, pour les essais), `--cache DOSSIER` (garde les fichiers
téléchargés), `--no-sea`, `--no-fire`, `--published URL` (rien n'est refait si ce calcul est déjà publié),
`--fire-cache DOSSIER`, et pour les essais `--familles vent,air` (seulement ces familles : un site sans le vent
n'est pas publiable, `publier.py` le refuse) et `--received AAAA-MM-JJThh:mmZ` (voir "L'index").

### Ajouter une famille

1. Créer `familles/<nom>.py`. Le nom du fichier est le nom de la famille (en français, sans accent).
2. Y écrire `NAME` (le nom du fichier) et `LAYER` (sa clé dans `index["layers"]`, par exemple `"wave"`), puis l'un
   des deux contenus :
   - **famille autonome** (sa propre source, comme la mer ou le feu) : une fonction `prepare(ctx)` qui écrit ses
     tuiles dans `ctx.out` (par exemple `ctx.out/w/<heure>/<niveau>/<ligne>-<colonne>.png`, avec
     `outils.write_tiles`) et rend son entrée d'index, ou `None` si rien n'a pu être préparé ;
   - **famille horaire d'ICON** (elle lit le fichier horaire du modèle, partagé avec le vent) : `FOLDER`,
     `write_hour(ctx, frame)` (écrit les tuiles de l'heure, lit ses grandeurs par `frame.raw("nom_omfiles")`,
     rend `(nombre de tuiles, poids)` ou `None` pour laisser l'heure), `entry(ctx, heures)` (son entrée d'index) et
     `prepare = lambda ctx: source_icon.prepare_alone(sys.modules[__name__], ctx)`. Voir `familles/air.py`.
3. Ajouter son nom à `FAMILIES` dans `prepare.py`, à la place voulue : c'est la seule ligne à ajouter.
4. L'entrée d'index rendue porte le gabarit des chemins (`"w/{time}/{level}/{row}-{col}.png"`), les heures
   (`"times"`, au format `2026-11-21T06:00Z`), les niveaux si ce ne sont pas ceux du vent (`"levels"`), la façon de
   lire les octets, et les champs de date : `**ctx.provenance(model="...")` donne `run`, `received` et, quand le
   serveur le dit, `source_modified` (pour une source qui a son propre `latest.json`, passer `latest=` et son
   `run`). Les champs existants de l'index ne changent jamais de nom, de sens ni de format.
5. Une famille qui échoue ne retient pas les autres : une exception ou `None` suffit, le passage la laisse de côté
   et publie le reste.
6. Écrire ses essais sans réseau en suivant `test_familles.py` (famille de démonstration, faux fichier ICON de
   `conftest.py`), puis `python -m pytest`.

`ctx` est un `Contexte` (`contexte.py`) : `out` (dossier du site), `work` (téléchargements), `model`, `run` (calcul
du modèle principal), `received`, `limit`, `keep_files`, `fire_cache`, `now`, `tools` (les outils communs de
`outils.py`).

## L'index

`index.json` est écrit en dernier. Champs de la racine (l'application installée les lit : on en ajoute, on n'en
change jamais le nom, le sens ni le format, ni les chemins des tuiles) :

- `version`, `model`, `source`, `encoding`, `tile`, `levels`, `path`, `times` : le vent et son codage ;
- `completed_from` : le calcul précédent qui complète la fin de la semaine, ou `null` ;
- `layers` : une entrée par famille autre que le vent (`air`, `sky`, `sea`, `current`, `fire`) ;
- trois dates, au format `2026-11-21T06:00Z`, pour l'ensemble **et pour chaque famille** :
  - `run` : l'heure du calcul du modèle ;
  - `received` (nouveau) : l'heure à laquelle l'atelier a vu ce calcul complet chez Open-Meteo. C'est l'heure du
    passage de l'atelier : quand celui qui le lance connaît mieux l'instant, il le donne par `--received` (la tâche
    planifiée y met l'heure de sa vérification) ;
  - `source_modified` (nouveau, quand le serveur le donne) : la dernière modification de `latest.json` chez
    Open-Meteo, soit le moment où le calcul s'est terminé ; l'écart avec `received` est le retard de l'atelier ;
- `created` : l'heure de la publication (racine seulement).

Pour le feu, `received` est l'heure où les images du jour ont été reçues du GWIS (elle reste la même tant que le
cache du jour est repris).

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

Essai, un jour : `python feu.py sortie --days 1`. Essais sans réseau : `python -m pytest` (ou `python -m unittest test_feu`).

## Publication

`prepare.py` ne publie rien : il écrit le site dans un dossier. `publier.py` le vérifie (index lisible, champs de la
racine, tuiles des premières et dernières heures de chaque famille présentes) puis l'envoie vers une cible. Un
site abîmé ou sans le vent n'est jamais publié.

```
python publier.py site --cible github-pages
python publier.py site --verifier
```

Dans `.github/workflows/vent.yml`, deux jobs :

- `preparer` : vérifie s'il y a un nouveau calcul, prépare le site (`prepare.py site ...`) et le dépose en
  artefact `site` ;
- `publier-pages` : reprend l'artefact, lance `publier.py`, puis publie sur GitHub Pages (`upload-pages-artifact`
  et `deploy-pages`). Le site publié est celui de la préparation, octet pour octet.

Le déclenchement ne change pas (toutes les heures, et `workflow_dispatch`). Un autre hébergement (Cloudflare,
fichiers statiques des Workers) s'ajoute sans toucher à la préparation : une cible `cloudflare` dans `publier.py`
(regroupement des tuiles par famille et par heure, envoi par `wrangler`), une ligne dans `CIBLES`, et un job
`publier-cloudflare` qui reprend l'artefact `site` (modèle en commentaire au bout de `vent.yml`).

## Essayer sur son ordinateur

```
pip install -r requirements.txt pytest
python prepare.py sortie --limit 2
python prepare.py sortie --limit 2 --familles vent,air --no-fire
python -m pytest
```

`python comparer.py ANCIEN NOUVEAU` compare deux sites préparés : mêmes tuiles octet pour octet, mêmes champs
d'index (hors `created`), et liste les champs que le nouveau ajoute. C'est la preuve qu'un changement de
l'atelier ne modifie pas ce que lit l'application.

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
