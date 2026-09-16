# -*- coding: utf-8 -*-
"""
Calcule, pour une date donnee, le nom du fichier .txt dans lequel les
articles/news de cette date doivent etre ecrits.

Regle demandee par l'utilisateur :
- Les fichiers ne sont nommes QUE sur des jours PAIRS du mois :
  02, 04, 06, 08, ..., 30.
- Un fichier "JJ" (pair) couvre normalement 2 jours : JJ-1 et JJ.
  Ex : CB-2026-09-04.txt couvre le 3 et le 4 septembre.
- Si un mois se termine sur un jour IMPAIR (ex : 29 en fevrier
  bissextile, ou 31 pour janvier/mars/mai/juillet/aout/octobre/decembre),
  ce dernier jour est "orphelin" : il n'a pas de jour pair pour le
  cloturer dans le meme mois. Il est alors rattache au fichier "02" du
  MOIS SUIVANT, qui couvre alors 3 jours au lieu de 2.

  Exemples concrets :
  - Aout (31 jours) -> le 31 aout est orphelin -> range dans
    CB-2026-09-02.txt, qui couvre alors 31/08, 01/09 et 02/09.
  - Fevrier 2028 (bissextile, 29 jours) -> le 29 fevrier est orphelin ->
    range dans CB-2028-03-02.txt (29/02, 01/03, 02/03).
  - Septembre (30 jours, pair) -> pas de jour orphelin -> le fichier du
    30 septembre couvre bien 29/09 et 30/09, et octobre repart a zero
    sur des paires propres (01-02, 03-04, ...).
"""

import calendar
from datetime import timedelta


def nom_fichier(prefixe, une_date):
    """Retourne le nom de fichier (sans dossier) correspondant a une_date.

    Exemples :
        nom_fichier("CB", date(2026, 9, 1))  -> "CB-2026-09-02.txt"
        nom_fichier("CB", date(2026, 9, 2))  -> "CB-2026-09-02.txt"
        nom_fichier("CB", date(2026, 8, 31)) -> "CB-2026-09-02.txt"
        nom_fichier("FF", date(2026, 10, 1)) -> "FF-2026-10-02.txt"
    """
    jour = une_date.day
    _, jours_dans_mois = calendar.monthrange(une_date.year, une_date.month)

    if jour % 2 == 1 and jour == jours_dans_mois:
        # Dernier jour du mois ET impair -> jour orphelin, rattache au
        # fichier "02" du mois suivant.
        premier_jour_mois_suivant = une_date.replace(day=1) + timedelta(days=jours_dans_mois)
        annee_cible = premier_jour_mois_suivant.year
        mois_cible = premier_jour_mois_suivant.month
        jour_cible = 2
    else:
        annee_cible = une_date.year
        mois_cible = une_date.month
        jour_cible = jour + 1 if jour % 2 == 1 else jour

    return f"{prefixe}-{annee_cible}-{mois_cible:02d}-{jour_cible:02d}.txt"
