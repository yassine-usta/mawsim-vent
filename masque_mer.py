"""Fabrique masque-mer.png : les points de mer du modèle océanique, dont feu.py se sert pour ne prolonger le danger
de feu que vers la mer (jamais vers le désert, un chott, un lac ou la glace).

Mode d'emploi (une seule fois : les côtes ne changent pas ; le fichier produit est commis dans le dépôt) :

    pip install -r requirements.txt
    python masque_mer.py                     # télécharge une heure du modèle (2 Mo) et écrit masque-mer.png
    python masque_mer.py --om FICHIER.om     # part d'un fichier du modèle déjà téléchargé

Source : le modèle "meteofrance_sea_surface_temperature" publié par Open-Meteo (data_spatial, CC BY 4.0), la même
grille au douzième de degré que mer.py. La température de l'eau n'existe qu'en mer : une valeur veut dire la mer,
une absence la terre.

Le masque est sur les points des tuiles au douzième de degré : 4320 colonnes depuis 180° ouest, 2161 lignes, le
nord en haut de l'image (ligne 0 à 90° nord, dernière ligne à 90° sud). Blanc : point de mer, c'est-à-dire qu'au
moins une des quatre mailles du modèle autour du point est en mer (mer.lattice y donne une valeur). Noir : terre.
"""
import argparse
import os
import sys
import tempfile

import numpy as np
from PIL import Image

import mer
import prepare

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "masque-mer.png")


def mask_from(path):
    """Points de mer (ligne 0 au pôle Sud) d'après un fichier du modèle de température de l'eau."""
    return ~np.isnan(mer.lattice(mer.read(path, "sea_surface_temperature")))


def main():
    parser = argparse.ArgumentParser(description="Fabrique masque-mer.png pour feu.py.")
    parser.add_argument("--om", help="fichier du modèle déjà téléchargé")
    parser.add_argument("--out", default=OUT, help="fichier à écrire (par défaut masque-mer.png à côté du script)")
    args = parser.parse_args()

    path = args.om
    if not path:
        latest = prepare.fetch_json(f"{prepare.SOURCE}/{mer.TEMPERATURE_MODEL}/latest.json")
        run = prepare.parse_time(latest["reference_time"])
        valid = prepare.parse_time(latest["valid_times"][0])
        path = os.path.join(tempfile.mkdtemp(prefix="masque-"), "sst.om")
        print(f"Modèle {mer.TEMPERATURE_MODEL}, calcul du {run:%d/%m/%Y %H h}, heure du {valid:%d/%m %H h}")
        prepare.download(prepare.source_url(mer.TEMPERATURE_MODEL, run, valid), path)
    sea = mask_from(path)
    Image.fromarray(sea[::-1]).save(args.out, optimize=True)  # nord en haut de l'image
    print(f"{args.out} : {sea.shape[1]} x {sea.shape[0]} points, {sea.mean() * 100:.1f} % de mer, "
          f"{os.path.getsize(args.out) / 1024:.0f} Ko")
    return 0


if __name__ == "__main__":
    sys.exit(main())
