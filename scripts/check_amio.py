#!/usr/bin/env python3
"""Veille des sessions d'Amio (amioclub.com).

Le site est rendu par JavaScript : la page téléchargée contient bien les
liens de réservation de toutes les sessions, mais ni le badge COMPLET ni le
nombre de places restantes, que la page calcule elle-même après avoir
interrogé une fonction publique. On refait ici le même calcul.

Cette fonction renvoie, par date, le nombre de billets individuels (counts)
et le nombre de billets vendus en pack (packs). Leur somme est le nombre de
places occupées, à comparer à la capacité d'une soirée. La capacité n'est
écrite nulle part dans le code du site : elle a été retrouvée en comparant
l'affichage du site à ces chiffres, sur l'ensemble des sessions annoncées.
Si Amio changeait de salle, c'est la constante ci-dessous qu'il faudrait
revoir, et l'écart se verrait aussitôt dans le baromètre.
"""
import html
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veille as v

SITE = "https://amioclub.com"
PAGE = SITE + "/upcoming-sessions"
DISPOS = "https://iwodklhrgkftorthbqxl.supabase.co/functions/v1/get-event-availability"
ETAT = "data/amio_seen.json"
ORGANISATEUR = "Amio"

CAPACITE = 79          # places par soirée, déduite de l'affichage du site
SEUIL_PLACES = 3       # en dessous, on alerte

LIEN = re.compile(r'href="(/checkout\?[^"]+)"', re.IGNORECASE)
BUNDLE = re.compile(r'/assets/(index-[A-Za-z0-9_-]+\.js)')
CLE = re.compile(r"sb_[A-Za-z0-9_-]{20,}")


def catalogue(page):
    """Les sessions annoncées, lues dans les liens de réservation.

    Les apostrophes des titres sont encodées « &#x27; » dans le HTML. Sans
    décodage préalable, le dièse ouvre un fragment d'URL et tout ce qui suit,
    dont l'identifiant de la session, est perdu : les sessions dont le titre
    contient une apostrophe disparaissaient purement et simplement.
    """
    sessions = {}
    for href in LIEN.findall(page):
        lien = html.unescape(href)
        params = urllib.parse.parse_qs(urllib.parse.urlparse(lien).query)
        prendre = lambda k: v.propre(params.get(k, [""])[0])
        cle, titre = prendre("event"), prendre("title")
        if not cle or not titre or cle in sessions:
            continue
        sessions[cle] = {
            "titre": titre,
            "date": prendre("date").split("·")[0].strip(),
            "etiquette_date": prendre("date"),
            "lieu": prendre("venue").split(",")[0][:60],
            "url": SITE + lien,
            "intervenant": " ".join(m.capitalize() for m in cle.split("-")[-2:]) or None,
        }
    return sessions


def cle_api(page):
    """La clé publique d'accès, extraite du code du site."""
    for nom in BUNDLE.findall(page)[:3]:
        try:
            trouve = CLE.search(v.fetch(f"{SITE}/assets/{nom}", essais=2))
        except Exception:
            continue
        if trouve:
            return trouve.group(0)
    return None


def occupation(cle, etiquettes):
    """Places occupées par session : billets individuels et packs réunis."""
    if not cle or not etiquettes:
        return {}
    try:
        rep = json.loads(v.fetch(DISPOS, essais=2, donnees={"dates": etiquettes},
                                 entetes={"Authorization": f"Bearer {cle}"}))
    except Exception as erreur:
        print(f"Compteur d'inscrits indisponible ({type(erreur).__name__}).", file=sys.stderr)
        return {}
    if not rep.get("success"):
        return {}
    comptes, packs = rep.get("counts") or {}, rep.get("packs") or {}
    return {e: comptes[e] + packs.get(e, 0) for e in comptes if isinstance(comptes[e], int)}


def main():
    page = v.fetch(PAGE)
    sessions = catalogue(page)
    if not sessions:
        print("Aucune session trouvée : site en panne ou structure modifiée ?", file=sys.stderr)
        sys.exit(1)

    occupees = occupation(cle_api(page), [s["etiquette_date"] for s in sessions.values()])
    if not occupees:
        print("Pas de nombre d'inscrits ce passage : suivi limité au catalogue.", file=sys.stderr)

    for s in sessions.values():
        prises = occupees.get(s.pop("etiquette_date"))
        s["inscrits"] = prises if isinstance(prises, int) else None
        s["capacite"] = CAPACITE if s["inscrits"] is not None else None
        s["places"] = max(CAPACITE - s["inscrits"], 0) if s["inscrits"] is not None else None
        s["complet"] = (s["places"] <= 0) if s["places"] is not None else None

    etat = v.charger(ETAT)
    premier = etat is None
    etat = etat or {}
    nouveaux, _rouverts, derniers = v.fusionner(
        etat, sessions, seuil_places=SEUIL_PLACES, premier_passage=premier)
    v.sauver(ETAT, etat)

    if premier:
        print(f"Initialisation : {len(sessions)} sessions enregistrées, aucune alerte.")
        return
    v.alerter(ORGANISATEUR, PAGE, [
        ("LAST CHANCE", derniers, f"{len(derniers)} sessions à {SEUIL_PLACES} places ou moins", "hourglass"),
        ("JUST DROPPED", nouveaux, v.accord(len(nouveaux), "nouvelle session", "nouvelles sessions"), "beer"),
    ])
    connus = sum(1 for s in sessions.values() if s["inscrits"] is not None)
    print(v.resumer(ORGANISATEUR, etat, sessions) + f", {connus} avec compteur d'inscrits")
    for f in nouveaux.values():
        print("  nouveau :", v.decrire(f))


if __name__ == "__main__":
    main()
