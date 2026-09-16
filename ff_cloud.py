# -*- coding: utf-8 -*-
"""
Script d'extraction de news - ForexFactory News
------------------------------------------------------------------------------
Version SANS Firebase : le depot Git lui-meme sert de base de donnees.

- Pas de boucle infinie : un seul passage (single-pass), c'est GitHub
  Actions (cron) qui se charge de relancer le script periodiquement.
- Les news sont ecrites en texte brut lisible dans des fichiers
  data/FF-AAAA-MM-JJ.txt (voir bucket_utils.py pour la regle exacte de
  nommage : uniquement des jours PAIRS, 02/04/06/.../30, avec le jour
  orphelin de fin de mois qui bascule sur le "02" du mois suivant).
- Dedup : LOCALE via dedup_cache.py (cache/forexfactory.json), aucune
  base de donnees externe.
- A la fin, le script commit et pousse lui-meme (git) les dossiers
  data/ et cache/ vers le depot distant.

⚠️ Les selecteurs CSS (.news-block__item, etc.) doivent etre verifies
contre le vrai HTML en direct. Si les logs affichent "Aucun bloc de
news trouve" au premier run, ouvrez https://www.forexfactory.com/news,
inspectez un titre, et ajustez recuperer_liens_articles() si besoin.
"""

import os
import re
import hashlib
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from bucket_utils import nom_fichier
from dedup_cache import charger_cache, sauvegarder_cache, marquer_traite
from git_utils import git_commit_et_push

# ---------- CONFIGURATION ----------
URL_LISTE = "https://www.forexfactory.com/news"
FENETRE_HEURES = 24

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
}

PREFIXE = "FF"
NOM_SOURCE = "forexfactory"  # identifiant unique de ce script (nom du fichier cache)
DOSSIER_DATA = "data"
DOSSIER_CACHE = "cache"

# Niveaux d'impact retenus. Les news sans icone d'impact du tout sont
# ignorees.
NIVEAUX_IMPACT_GARDES = {"high", "medium", "low"}

MOTIFS_ERREUR = [
    "error 500", "server error", "that's an error",
    "error 404", "page not found", "404 not found", "access denied",
    "forbidden", "too many requests", "rate limit",
]


def hash_url(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def page_erreur(titre, contenu):
    texte = f"{titre or ''} {contenu or ''}".lower()
    return any(motif in texte for motif in MOTIFS_ERREUR)


# ---------- SCRAPING ----------
def parser_date_relative(texte, maintenant):
    if not texte:
        return None
    texte = texte.strip().lower()

    m = re.fullmatch(r"(\d+)\s*hr\s*(\d+)\s*min\s*ago", texte)
    if m:
        heures, minutes = int(m.group(1)), int(m.group(2))
        return maintenant - timedelta(hours=heures, minutes=minutes)

    m = re.fullmatch(r"(\d+)\s*hr\s*ago", texte)
    if m:
        return maintenant - timedelta(hours=int(m.group(1)))

    m = re.fullmatch(r"(\d+)\s*min\s*ago", texte)
    if m:
        return maintenant - timedelta(minutes=int(m.group(1)))

    m = re.fullmatch(r"(\d+)\s*d(ay|ays)?\s*ago", texte)
    if m:
        return maintenant - timedelta(days=int(m.group(1)))

    return None


def extraire_date_publication(element, maintenant):
    details = element.select_one(".news-block__details")
    if not details:
        return None
    date_span = details.select_one("span.nowrap")
    if date_span:
        return parser_date_relative(date_span.get_text(strip=True), maintenant)
    return None


def extraire_impact(element):
    """Determine le niveau d'impact d'une news a partir de l'icone
    presente dans .news-block__details (URL contenant /impact/ff/high|
    medium|low.svg). Retourne None si aucune icone."""
    details = element.select_one(".news-block__details")
    if not details:
        return None
    icone = details.select_one("img[src*='/impact/ff/']")
    if not icone or not icone.get("src"):
        return None
    src = icone["src"].lower()
    for niveau in ("high", "medium", "low"):
        if f"/impact/ff/{niveau}" in src:
            return niveau
    return None


def recuperer_liens_articles(maintenant):
    reponse = requests.get(URL_LISTE, headers=HEADERS, timeout=15)
    reponse.raise_for_status()
    soup = BeautifulSoup(reponse.text, "html.parser")

    candidats = soup.select(".news-block__item")
    total_brut = len(candidats)
    resultats = []

    for element in candidats:
        if "news-block__item--comment" in element.get("class", []):
            continue

        titre_tag = element.select_one(".news-block__title a")
        if not titre_tag:
            continue
        titre = titre_tag.get_text(strip=True)
        if not titre:
            continue

        href = titre_tag.get("href")
        if not href:
            continue
        if href.startswith("/"):
            href = "https://www.forexfactory.com" + href

        details = element.select_one(".news-block__details")
        source_tag = details.select_one("a") if details else None
        source = source_tag.get_text(strip=True) if source_tag else "Inconnue"
        source = re.sub(r"^from\s+", "", source, flags=re.IGNORECASE)

        preview_tag = element.select_one(".news-block__preview")
        extrait = preview_tag.get_text(strip=True) if preview_tag else ""

        date_pub = extraire_date_publication(element, maintenant)
        impact = extraire_impact(element)

        if impact not in NIVEAUX_IMPACT_GARDES:
            continue

        resultats.append({
            "titre": titre,
            "url": href,
            "source": source,
            "extrait": extrait,
            "date_pub": date_pub,
            "impact": impact,
        })

    return resultats, total_brut


# ---------- ECRITURE EN TEXTE BRUT ----------
def formater_entree(titre, url, source, impact, date_pub, extrait):
    lignes = []
    lignes.append("=" * 80)
    horodatage = date_pub.strftime("%Y-%m-%d %H:%M UTC") if date_pub else "date inconnue"
    lignes.append(f"[{horodatage}] {titre}  (impact: {impact})")
    lignes.append(f"URL: {url}")
    lignes.append(f"Source: {source}")
    lignes.append("-" * 80)
    lignes.append(extrait if extrait else "(Pas d'extrait disponible)")
    lignes.append("=" * 80)
    lignes.append("")
    lignes.append("")
    return "\n".join(lignes)


def ecrire_news(titre, url, source, impact, date_pub, extrait):
    os.makedirs(DOSSIER_DATA, exist_ok=True)
    chemin_fichier = os.path.join(DOSSIER_DATA, nom_fichier(PREFIXE, date_pub.date()))
    with open(chemin_fichier, "a", encoding="utf-8") as f:
        f.write(formater_entree(titre, url, source, impact, date_pub, extrait))
    return chemin_fichier


# ---------- PROGRAMME PRINCIPAL (single-pass) ----------
def cycle():
    maintenant = datetime.now(timezone.utc)
    debut_fenetre = maintenant - timedelta(hours=FENETRE_HEURES)

    try:
        toutes_les_news, total_brut = recuperer_liens_articles(maintenant)
    except requests.exceptions.RequestException as e:
        print(f"Erreur lors de la recuperation de la page news : {e}")
        return

    if total_brut == 0:
        print(
            "Aucun bloc de news trouve du tout. Les selecteurs CSS doivent "
            "probablement etre ajustes, ou le contenu est charge en JavaScript."
        )
        return

    print(f"{total_brut} bloc(s) de news au total sur la page.")
    print(f"{len(toutes_les_news)} apres filtre d'impact (high/medium/low).")

    if not toutes_les_news:
        print("Aucune news avec impact retenu sur la page pour le moment.")
        return

    cache = charger_cache(NOM_SOURCE)
    news_ecrites = 0

    for news in toutes_les_news:
        doc_id = hash_url(news["url"])

        # Dedup : verification LOCALE (cache/forexfactory.json).
        if doc_id in cache:
            continue

        try:
            if page_erreur(news["titre"], news["extrait"]):
                print(f"Page d'erreur detectee, ignore : {news['url']}")
                marquer_traite(cache, doc_id)
                continue

            if news["date_pub"] is None:
                # Une meme URL peut apparaitre plusieurs fois sur la page
                # (ex: "Hot Stories" dupliquees) avec des structures HTML
                # differentes. Si CETTE occurrence n'a pas de date
                # exploitable, on ne bloque pas l'URL (pas de
                # marquer_traite) au cas ou une autre occurrence, plus
                # loin dans la liste ou a un prochain cycle, ait une date
                # correcte.
                print(f"Date introuvable sur cette occurrence, ignoree (non bloquee) : {news['titre']}")
                continue

            if news["date_pub"] < debut_fenetre:
                marquer_traite(cache, doc_id)
                continue

            chemin_fichier = ecrire_news(
                news["titre"], news["url"], news["source"],
                news["impact"], news["date_pub"], news["extrait"],
            )
            marquer_traite(cache, doc_id)
            news_ecrites += 1
            print(f"Ecrit dans {chemin_fichier} : {news['titre']}")

        except Exception as e:
            print(f"Erreur sur {news['url']} : {e}")

    sauvegarder_cache(NOM_SOURCE, cache)
    print(f"\nTermine. {news_ecrites} nouvelle(s) news ecrite(s).")

    if news_ecrites > 0:
        git_commit_et_push(f"ForexFactory : +{news_ecrites} news", [DOSSIER_DATA, DOSSIER_CACHE])
    else:
        git_commit_et_push("ForexFactory : mise a jour du cache (rien de neuf)", [DOSSIER_CACHE])


if __name__ == "__main__":
    cycle()
