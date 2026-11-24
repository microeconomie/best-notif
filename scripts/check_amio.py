#!/usr/bin/env python3
"""Veille des sessions d'Amio (amioclub.com).

Le site est rendu par JavaScript : la page téléchargée contient bien les
liens de réservation de toutes les sessions, mais ni le badge COMPLET ni le
nombre de places restantes, que la page calcule elle-même après avoir
interrogé une fonction publique. On refait ici le même calcul.

Cette fonction renvoie, par date, le nombre de billets individuels (counts)
et le nombre de billets vendus en pack (packs). Leur somme donne le nombre
d'inscrits, qui est sûr et que l'on enregistre.

En revanche on n'en déduit aucun complet. Il y faudrait la capacité de la
soirée, qui varie d'une salle à l'autre (une session affichée complète à 64
inscrits quand une autre reste ouverte à 92) et qui n'est lisible ni dans la
page, ni dans l'API, ni dans le code du site. Le seul indice accessible sans
navigateur est le lien de liste d'attente, que le site pose sur les sessions
pleines : il est en retard de quelques heures sur la réalité, mais il ne se
trompe pas dans l'autre sens.
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
            # le site renvoie vers la liste d'attente quand la soirée est pleine
            "complet": "liste-attente" in lien,
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

    etat = v.charger(ETAT)
    premier = etat is None
    etat = etat or {}
    nouveaux, _rouverts, _derniers = v.fusionner(etat, sessions, premier_passage=premier)
    v.sauver(ETAT, etat)

    if premier:
        print(f"Initialisation : {len(sessions)} sessions enregistrées, aucune alerte.")
        return
    v.alerter(ORGANISATEUR, PAGE, [
        ("JUST DROPPED", nouveaux, v.accord(len(nouveaux), "nouvelle session", "nouvelles sessions"), "beer"),
    ])
    connus = sum(1 for s in sessions.values() if s["inscrits"] is not None)
    print(v.resumer(ORGANISATEUR, etat, sessions) + f", {connus} avec compteur d'inscrits")
    for f in nouveaux.values():
        print("  nouveau :", v.decrire(f))


if __name__ == "__main__":
    main()
