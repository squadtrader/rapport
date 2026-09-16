# -*- coding: utf-8 -*-
"""
Backfill (rattrapage) CentralBanks
------------------------------------------------------------------------------
A LANCER UNE SEULE FOIS (en local ou via "workflow_dispatch" manuel), pour
recuperer les articles plus anciens que la fenetre de 24h du script normal
(centralbanks_cloud.py) et les ranger dans les bons fichiers data/CB-*.txt.

Principe : la page /CentralBanks/ a de la pagination (/CentralBanks/page/2/,
page/3/, ...). On la parcourt page par page, du plus recent au plus ancien,
jusqu'a ce qu'on soit tombe sur des articles anterieurs a DATE_DEBUT.

Reutilise les fonctions d'extraction et le cache de dedup de
centralbanks_cloud.py (aucun risque de doublon avec les prochains cycles
normaux : meme cache, meme regle de nommage de fichier).

Usage :
    python backfill_centralbanks.py                     # depuis le 1er septembre 2026 jusqu'a maintenant
    python backfill_centralbanks.py --depuis 2026-09-01 --jusqua 2026-09-16
"""

import argparse
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from centralbanks_cloud import (
    URL_LISTE, HEADERS, NOM_SOURCE, DOSSIER_DATA, DOSSIER_CACHE,
    hash_url, page_erreur, extraire_article, ecrire_article,
)
from dedup_cache import charger_cache, sauvegarder_cache, marquer_traite
from git_utils import git_commit_et_push


def url_page(numero_page):
    if numero_page <= 1:
        return URL_LISTE
    return URL_LISTE.rstrip("/") + f"/page/{numero_page}/"


def recuperer_liens_page(numero_page):
    """Comme recuperer_liens_articles() de centralbanks_cloud.py, mais sur
    une page de pagination precise. Renvoie une liste (ordonnee, sans
    doublon) au lieu d'un set, pour garder l'ordre de la page (plus
    recent -> plus ancien)."""
    reponse = requests.get(url_page(numero_page), headers=HEADERS, timeout=15)
    reponse.raise_for_status()
    soup = BeautifulSoup(reponse.text, "html.parser")

    liens = []
    vus = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/central-banks/" in href.lower() and href.rstrip("/").lower() != "https://investinglive.com/central-banks":
            if href.startswith("/"):
                href = "https://investinglive.com" + href
            if href.startswith("https://investinglive.com/central-banks/"):
                href = href.split("?")[0]
                if href not in vus:
                    vus.add(href)
                    liens.append(href)

    return liens


def backfill(date_debut, date_fin, page_max=50, pause=1.0):
    cache = charger_cache(NOM_SOURCE)
    articles_ecrits = 0
    numero_page = 1
    liens_deja_vus_backfill = set()  # evite de re-traiter un lien qui apparait sur 2 pages a la fois

    while numero_page <= page_max:
        print(f"\n--- Page {numero_page} ({url_page(numero_page)}) ---")
        try:
            liens = recuperer_liens_page(numero_page)
        except Exception as e:
            print(f"Erreur recuperation page {numero_page} : {e}")
            break

        if not liens:
            print("Plus aucun lien sur cette page, arret.")
            break

        # au_moins_un_fetch_frais : vrai si on a reellement telecharge au
        # moins un article de cette page (donc qu'on connait sa date).
        # au_moins_un_dans_la_fenetre : vrai si au moins un de ces articles
        # frais est encore >= date_debut.
        # On ne s'arrete QUE si on a la preuve (un article frais, donc une
        # vraie date) qu'on est passe sous date_debut. Si la page est
        # entierement deja en cache (ex: page 1, deja traitee par le run
        # normal), on n'a aucune preuve : on continue sur la page suivante
        # au lieu de s'arreter a tort.
        au_moins_un_fetch_frais = False
        au_moins_un_dans_la_fenetre = False

        for url in liens:
            if url in liens_deja_vus_backfill:
                continue
            liens_deja_vus_backfill.add(url)

            doc_id = hash_url(url)
            if doc_id in cache:
                # Deja traite par un run normal ou un backfill precedent.
                # On ne sait pas quelle etait sa date : on ne compte pas
                # ca comme une preuve pour decider d'arreter la pagination.
                continue

            try:
                titre, date_pub, contenu, tags = extraire_article(url)
                au_moins_un_fetch_frais = True

                if page_erreur(titre, contenu):
                    print(f"Page d'erreur, ignore : {url}")
                    marquer_traite(cache, doc_id)
                    continue

                if date_pub is None:
                    print(f"Date introuvable, ignore : {titre}")
                    marquer_traite(cache, doc_id)
                    continue

                if date_pub < date_debut:
                    # Trop ancien : hors de la periode demandee.
                    marquer_traite(cache, doc_id)
                    continue

                au_moins_un_dans_la_fenetre = True

                if date_pub > date_fin:
                    # Trop recent (ne devrait pas arriver ici, le run normal
                    # s'en charge deja) : on marque quand meme pour ne pas
                    # le retraiter en double.
                    marquer_traite(cache, doc_id)
                    continue

                chemin_fichier = ecrire_article(titre, url, date_pub, tags, contenu)
                marquer_traite(cache, doc_id)
                if chemin_fichier:
                    articles_ecrits += 1
                    print(f"Ecrit dans {chemin_fichier} : {titre}")
                else:
                    print(f"Deja present dans le fichier cible, ignore : {titre}")

            except Exception as e:
                print(f"Erreur sur {url} : {e}")

            time.sleep(pause)

        if au_moins_un_fetch_frais and not au_moins_un_dans_la_fenetre:
            # On a reellement regarde au moins un article de cette page, et
            # aucun n'est dans la periode demandee : on est passe sous
            # date_debut, plus la peine de continuer (les pages suivantes
            # seront encore plus anciennes).
            print(f"Page {numero_page} entierement hors periode (trop ancienne), arret.")
            break

        if not au_moins_un_fetch_frais:
            print(f"Page {numero_page} entierement deja en cache, on continue quand meme sur la page suivante.")

        numero_page += 1

    sauvegarder_cache(NOM_SOURCE, cache)
    print(f"\nBackfill termine. {articles_ecrits} article(s) ecrit(s).")

    if articles_ecrits > 0:
        git_commit_et_push(
            f"CentralBanks : backfill +{articles_ecrits} article(s) ancien(s)",
            [DOSSIER_DATA, DOSSIER_CACHE],
        )
    else:
        git_commit_et_push("CentralBanks : backfill (cache mis a jour, rien de nouveau)", [DOSSIER_CACHE])


def parser_date(texte):
    return datetime.strptime(texte, "%Y-%m-%d").replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    parseur = argparse.ArgumentParser(description="Backfill CentralBanks")
    parseur.add_argument("--depuis", default="2026-09-01", help="Date de debut (AAAA-MM-JJ), incluse")
    parseur.add_argument("--jusqua", default=None, help="Date de fin (AAAA-MM-JJ), incluse. Par defaut : maintenant.")
    parseur.add_argument("--pages-max", type=int, default=50, help="Nombre max de pages de pagination a parcourir (securite)")
    args = parseur.parse_args()

    date_debut = parser_date(args.depuis)
    date_fin = parser_date(args.jusqua) if args.jusqua else datetime.now(timezone.utc)

    print(f"Backfill CentralBanks du {date_debut.date()} au {date_fin.date()}")
    backfill(date_debut, date_fin, page_max=args.pages_max)
