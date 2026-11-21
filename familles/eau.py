"""Famille de la température de l'eau : modèle océanique de Météo-France, tuiles m/ (la logique est dans mer.py)."""
import mer

NAME = "eau"
LAYER = "sea"  # sa clé dans index["layers"]


def prepare(ctx):
    """Rend l'entrée d'index, ou None : la famille est laissée, l'app garde alors les cartes de Copernicus."""
    return mer.prepare_family(ctx.out, ctx.work, ctx.tools, "sea", keep_files=ctx.keep_files, limit=abs(ctx.limit), now=ctx.now)
