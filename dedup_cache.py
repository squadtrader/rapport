# -*- coding: utf-8 -*-
"""
Cache de deduplication 100% local, sans aucune base de donnees externe.

Le fichier cache/<nom_source>.json contient un objet :
    { "<hash_url>": "2026-09-16T10:00:00+00:00", ... }

Ce fichier est commite dans le depot Git (comme les fichiers .txt de
data/) pour que la dedup survive entre deux executions de GitHub
Actions, dont l'environnement est jete a chaque run.
"""

import json
import os
from datetime import datetime, timezone

DOSSIER_CACHE = "cache"


def _chemin_cache(nom_source):
    return os.path.join(DOSSIER_CACHE, f"{nom_source}.json")


def charger_cache(nom_source):
    chemin = _chemin_cache(nom_source)
    if not os.path.exists(chemin):
        return {}
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def sauvegarder_cache(nom_source, cache):
    os.makedirs(DOSSIER_CACHE, exist_ok=True)
    chemin = _chemin_cache(nom_source)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def marquer_traite(cache, doc_id):
    cache[doc_id] = datetime.now(timezone.utc).isoformat()
