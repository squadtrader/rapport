# -*- coding: utf-8 -*-
"""
Protection anti-doublon "de secours", basee sur le contenu REEL des
fichiers .txt (et non sur le cache JSON, qui n'est qu'une optimisation
de performance pour eviter de re-telecharger une page deja vue).

Pourquoi ce filet de secours ? Le cache JSON (dedup_cache.py) marque une
URL comme "traitee" des qu'on a decide de ne PAS l'ecrire (page d'erreur,
date illisible...). Si un jour le cache est reinitialise, corrompu, ou
si un bug marque par erreur une URL comme traitee sans l'avoir
reellement ecrite (c'est ce qui est arrive avec l'ancien filtre "hors
fenetre de 24h"), ce filet de secours garantit qu'on ne perd jamais
d'article pour de bon : tant qu'une URL n'apparait pas reellement dans
le fichier .txt cible, elle peut toujours etre (re)ecrite.
"""

import os


def deja_ecrit(url, chemin_fichier):
    """Vrai si cette URL apparait deja dans le fichier .txt cible
    (recherche de la ligne exacte 'URL: <url>')."""
    if not os.path.exists(chemin_fichier):
        return False
    marqueur = f"URL: {url}\n"
    with open(chemin_fichier, "r", encoding="utf-8") as f:
        return marqueur in f.read()
