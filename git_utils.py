# -*- coding: utf-8 -*-
"""
Commit et push automatique des fichiers modifies (data/ et cache/), pour
que le script se passe entierement de Firebase : le depot Git EST la
base de donnees.

Suppose que le job GitHub Actions a fait un `actions/checkout` classique
(avec les identifiants persistes, comportement par defaut) et que le
workflow a la permission `contents: write`.
"""

import subprocess


def git_commit_et_push(message, chemins):
    """Ajoute les chemins donnes, commit (si des changements existent)
    et pousse vers le depot distant. Ne leve pas d'exception si rien
    n'a change."""
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(
        ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
        check=True,
    )

    subprocess.run(["git", "add", *chemins], check=True)

    # git diff --cached --quiet renvoie 0 s'il n'y a AUCUN changement en
    # attente de commit.
    resultat = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if resultat.returncode == 0:
        print("Rien a committer (aucun changement).")
        return

    subprocess.run(["git", "commit", "-m", message], check=True)

    branche = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    # Rebase sur la version distante avant de pousser, au cas ou l'autre
    # script (ou un cycle precedent encore en cours) aurait pousse entre
    # temps. On ne fait pas `check=True` ici : si le rebase echoue pour
    # une raison quelconque, on tente quand meme le push (qui echouera
    # proprement et remontera dans les logs Actions si un vrai conflit
    # existe).
    subprocess.run(["git", "pull", "--rebase", "origin", branche], check=False)
    subprocess.run(["git", "push", "origin", branche], check=True)
    print("Changements pousses sur le depot.")
