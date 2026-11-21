"""Famille des courants marins : modèle océanique de Météo-France, tuiles c/ (la logique est dans mer.py)."""
import mer

NAME = "courants"
LAYER = "current"  # sa clé dans index["layers"]


def prepare(ctx):
    """Rend l'entrée d'index, ou None : la famille est laissée, l'app garde alors les cartes de Copernicus."""
    return mer.prepare_family(ctx.out, ctx.work, ctx.tools, "current", keep_files=ctx.keep_files, limit=abs(ctx.limit), now=ctx.now)
