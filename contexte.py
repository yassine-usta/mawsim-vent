"""Le contexte d'un passage de l'atelier : ce que prepare.py donne à chaque famille."""
import dataclasses
import datetime as dt
from typing import Any, Optional

import outils


@dataclasses.dataclass
class Contexte:
    """Tout ce dont une famille a besoin pour préparer ses tuiles.

    out         dossier de sortie, le site à publier (une famille y écrit son dossier de tuiles : t/, a/, s/...)
    work        dossier de travail, où se posent les fichiers téléchargés
    model       modèle principal (ICON du DWD par défaut) : celui de l'index et du vent
    latest      latest.json du modèle principal (heures, état du calcul) ; vide dans un essai sans réseau
    run         heure du calcul du modèle principal, en UTC
    received    heure à laquelle l'atelier a vu ce calcul complet chez Open-Meteo
    limit       nombre d'heures à préparer (essais) ; 0 : toutes ; négatif : voir source_icon.plan
    keep_files  garder les fichiers téléchargés (option --cache)
    fire_cache  dossier où le feu garde ses tuiles du jour d'une publication à l'autre
    now         l'heure d'aujourd'hui, pour les essais ; None : l'heure réelle, lue au moment voulu
    tools       les outils communs (outils.py) ; les anciens modules (mer.py, feu.py) les reçoivent en paramètre
    shared      mémoire d'un passage, pour ce que plusieurs familles partagent (le plan des heures d'ICON)
    """
    out: str
    work: str
    model: str = "dwd_icon"
    latest: dict = dataclasses.field(default_factory=dict)
    run: Optional[dt.datetime] = None
    received: Optional[dt.datetime] = None
    limit: int = 0
    keep_files: bool = False
    fire_cache: Optional[str] = None
    now: Optional[dt.datetime] = None
    tools: Any = outils
    shared: dict = dataclasses.field(default_factory=dict)

    def provenance(self, run=None, received=None, latest=None, model=None):
        """Les champs d'index qui datent un calcul : [run] (heure du calcul), [received] (vu complet par l'atelier) et,
        quand le serveur la donne dans [latest] (son latest.json), [source_modified] (dernière modification de ce
        fichier chez Open-Meteo, soit le moment où le calcul s'est terminé). Sans argument : ceux du modèle principal."""
        latest = self.latest if latest is None else latest
        fields = {"run": outils.format_time(run or self.run), "received": outils.format_time(received or self.received)}
        if latest.get("last_modified_time"):
            fields["source_modified"] = outils.format_time(outils.parse_time(latest["last_modified_time"]))
        if model:
            fields = {"model": model, **fields}
        return fields
