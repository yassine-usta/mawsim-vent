"""Famille du danger de feu : indice forêt-météo du GWIS, tuiles f/ (la logique est dans feu.py)."""
import feu

NAME = "feu"
LAYER = "fire"  # sa clé dans index["layers"]


def prepare(ctx):
    """Rend l'entrée d'index, ou None : la famille est laissée, l'app garde alors les images du serveur."""
    layers = feu.prepare(ctx.out, ctx.work, ctx.tools, cache=ctx.fire_cache, days=abs(ctx.limit))
    return layers.get("fire")
