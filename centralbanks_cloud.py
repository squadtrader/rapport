# -*- coding: utf-8 -*-
"""
Script d'extraction d'articles - investingLive Central Banks
------------------------------------------------------------------------------
Version SANS Firebase : le depot Git lui-meme sert de base de donnees.

- Pas de boucle infinie : un seul passage (single-pass), c'est GitHub
  Actions (cron) qui se charge de relancer le script periodiquement.
- Les articles sont ecrits en texte brut lisible dans des fichiers
  data/CB-AAAA-MM-JJ.txt (voir bucket_utils.py pour la regle exacte de
  nommage : uniquement des jours PAIRS, 02/04/06/.../30, avec le jour
  orphelin de fin de mois qui bascule sur le "02" du mois suivant).
- Dedup : LOCALE via dedup_cache.py (cache/centralbanks.json), aucune
  base de donnees externe.
- A la fin, le script commit et pousse lui-meme (git) les dossiers
  data/ et cache/ vers le depot distant.
"""

import os
import re
import time
import hashlib
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from bucket_utils import nom_fichier
from dedup_cache import charger_cache, sauvegarder_cache, marquer_traite
from dedup_fichier import deja_ecrit
from git_utils import git_commit_et_push

# ---------- CONFIGURATION ----------
URL_LISTE = "https://investinglive.com/CentralBanks/"
# NB : il n'y a plus de fenetre de 24h ici. Ce script traite TOUS les
# liens de la page 1 (les plus recents) et les range dans le bon fichier
# .txt selon leur date reelle. La dedup se charge d'eviter les
# doublons, ce qui rend ce script compatible avec backfill_centralbanks.py
# (qui, lui, remonte plus loin dans le temps via la pagination).

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

PREFIXE = "CB"
NOM_SOURCE = "centralbanks"  # identifiant unique de ce script (nom du fichier cache)
DOSSIER_DATA = "data"
DOSSIER_CACHE = "cache"

# Seuil au-dessus duquel un bloc est considere comme "surtout des liens"
# (nav, listes d'articles lies, bloc Tags, CTA...) et donc ignore.
SEUIL_DENSITE_LIEN = 0.6

# Marqueurs de titre/texte qui signalent qu'on est sorti du contenu
# editorial (tout ce qui suit est ignore).
MARQUEURS_ARRET = [
    "must read", "featured videos", "best in", "related articles",
    "high risk warning", "advisory warning", "disclaimer",
    "subscribe to our", "follow us", "manage cookies",
]

# Libelles courts a ignorer tels quels s'ils apparaissent seuls.
LIBELLES_A_IGNORER = {
    "tags", "share", "print", "advertisement - continue reading below",
}

# Lignes de separation pures (ex: "---", "***", "___").
MOTIF_SEPARATEUR = re.compile(r"^[\-–—_*=~\s]+$")

MOTIFS_ERREUR = [
    "error 500", "server error", "that's an error",
    "error 404", "page not found", "404 not found", "access denied",
    "forbidden", "too many requests", "rate limit",
]


def hash_url(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


# ---------- SCRAPING ----------
def recuperer_liens_articles():
    reponse = requests.get(URL_LISTE, headers=HEADERS, timeout=15)
    reponse.raise_for_status()
    soup = BeautifulSoup(reponse.text, "html.parser")

    liens = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/central-banks/" in href.lower() and href.rstrip("/").lower() != "https://investinglive.com/central-banks":
            if href.startswith("/"):
                href = "https://investinglive.com" + href
            if href.startswith("https://investinglive.com/central-banks/"):
                liens.add(href.split("?")[0])

    return sorted(liens)


def densite_lien(tag):
    """Proportion du texte d'un element qui provient de liens <a>."""
    texte_total = tag.get_text(strip=True)
    if not texte_total:
        return 1.0
    texte_liens = "".join(a.get_text(strip=True) for a in tag.find_all("a"))
    return len(texte_liens) / max(len(texte_total), 1)


def est_marqueur_arret(texte):
    texte_lower = texte.strip().lower()
    return any(motif in texte_lower for motif in MARQUEURS_ARRET)


def extraire_contenu_complet(titre_tag):
    """Parcourt le DOM dans l'ordre de lecture a partir du <h1> du titre,
    et collecte le texte de chaque paragraphe/puce reel de l'article."""
    if titre_tag is None:
        return ""

    morceaux = []
    for element in titre_tag.find_all_next(["h1", "h2", "h3", "h4", "p", "li"]):
        texte = element.get_text(strip=True)
        if not texte:
            continue

        if est_marqueur_arret(texte):
            break

        if texte.lower() in LIBELLES_A_IGNORER:
            continue

        if MOTIF_SEPARATEUR.match(texte):
            continue

        if element.name in ("h1", "h2", "h3", "h4"):
            morceaux.append(texte)
            continue

        if densite_lien(element) > SEUIL_DENSITE_LIEN:
            continue

        if texte.endswith(":") and len(texte) < 80:
            continue

        morceaux.append(texte)

    return "\n\n".join(morceaux)


def extraire_tags(soup):
    """Recupere les tags/themes de l'article (liens /Tag/nom/), sans
    doublon, dans l'ordre d'apparition."""
    tags = []
    vus = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/tag/" in href.lower():
            texte = a.get_text(strip=True)
            if texte and texte.lower() not in vus:
                vus.add(texte.lower())
                tags.append(texte)
    return tags


def extraire_date_publication(soup):
    balise = soup.find("meta", {"property": "article:published_time"})
    if balise and balise.get("content"):
        try:
            texte_date = balise["content"].replace("Z", "+00:00")
            return datetime.fromisoformat(texte_date)
        except ValueError:
            return None
    return None


def page_erreur(titre, contenu):
    texte = f"{titre or ''} {contenu or ''}".lower()
    return any(motif in texte for motif in MOTIFS_ERREUR)


def extraire_article(url):
    reponse = requests.get(url, headers=HEADERS, timeout=15)
    reponse.raise_for_status()
    soup = BeautifulSoup(reponse.text, "html.parser")

    titre_tag = soup.find("h1")
    titre = titre_tag.get_text(strip=True) if titre_tag else "Sans titre"

    date_pub = extraire_date_publication(soup)
    contenu = extraire_contenu_complet(titre_tag)
    tags = extraire_tags(soup)

    return titre, date_pub, contenu, tags


# ---------- ECRITURE EN TEXTE BRUT ----------
def formater_entree(titre, url, date_pub, tags, contenu):
    lignes = []
    lignes.append("=" * 80)
    horodatage = date_pub.strftime("%Y-%m-%d %H:%M UTC") if date_pub else "date inconnue"
    lignes.append(f"[{horodatage}] {titre}")
    lignes.append(f"URL: {url}")
    if tags:
        lignes.append(f"Tags: {', '.join(tags)}")
    lignes.append("-" * 80)
    lignes.append(contenu if contenu else "(Contenu non trouve)")
    lignes.append("=" * 80)
    lignes.append("")
    lignes.append("")
    return "\n".join(lignes)


def ecrire_article(titre, url, date_pub, tags, contenu):
    """Ecrit l'article dans le fichier .txt correspondant a sa date.
    Renvoie le chemin du fichier si ecrit, ou None si l'URL y figurait
    deja (protection anti-doublon de secours, voir dedup_fichier.py)."""
    os.makedirs(DOSSIER_DATA, exist_ok=True)
    chemin_fichier = os.path.join(DOSSIER_DATA, nom_fichier(PREFIXE, date_pub.date()))

    if deja_ecrit(url, chemin_fichier):
        return None

    with open(chemin_fichier, "a", encoding="utf-8") as f:
        f.write(formater_entree(titre, url, date_pub, tags, contenu))
    return chemin_fichier


# ---------- PROGRAMME PRINCIPAL (single-pass) ----------
def cycle():
    try:
        liens = recuperer_liens_articles()
    except Exception as e:
        print(f"Erreur recuperation des liens : {e}")
        return

    print(f"{len(liens)} lien(s) trouve(s) sur la page liste.")

    cache = charger_cache(NOM_SOURCE)
    articles_ecrits = 0

    for url in liens:
        doc_id = hash_url(url)

        # Dedup rapide : verification LOCALE (cache/centralbanks.json).
        # Le cache n'est marque plus bas QUE sur des cas definitifs
        # (ecrit avec succes, page d'erreur, date illisible) : jamais
        # juste parce qu'un article est "trop vieux", pour ne pas
        # bloquer un futur backfill sur cet article.
        if doc_id in cache:
            continue

        try:
            titre, date_pub, contenu, tags = extraire_article(url)

            if page_erreur(titre, contenu):
                print(f"Page d'erreur detectee, ignore : {url}")
                marquer_traite(cache, doc_id)
                continue

            if date_pub is None:
                print(f"Date introuvable, ignore : {titre}")
                marquer_traite(cache, doc_id)
                continue

            chemin_fichier = ecrire_article(titre, url, date_pub, tags, contenu)
            marquer_traite(cache, doc_id)

            if chemin_fichier:
                articles_ecrits += 1
                print(f"Ecrit dans {chemin_fichier} : {titre} (tags: {tags})")
            else:
                print(f"Deja present dans le fichier cible, ignore : {titre}")

        except Exception as e:
            # On ne marque PAS le cache ici : erreur reseau ponctuelle,
            # on retentera au prochain cycle.
            print(f"Erreur sur {url} : {e}")

        time.sleep(1)

    sauvegarder_cache(NOM_SOURCE, cache)
    print(f"\nTermine. {articles_ecrits} nouvel(aux) article(s) ecrit(s).")

    if articles_ecrits > 0:
        git_commit_et_push(
            f"CentralBanks : +{articles_ecrits} article(s)",
            [DOSSIER_DATA, DOSSIER_CACHE],
        )
    else:
        # Meme sans nouvel article, le cache a pu changer (pages
        # d'erreur / dates illisibles marquees comme traitees) : on le
        # commit quand meme pour ne pas les retraiter au prochain cycle.
        git_commit_et_push("CentralBanks : mise a jour du cache (rien de neuf)", [DOSSIER_CACHE])


if __name__ == "__main__":
    cycle()
