#!/usr/bin/env python3
"""Veille des sessions d'Amio (amioclub.com).

Amio a refondu son site : le marqueur « liste-attente » a disparu des liens,
et le badge COMPLET est désormais ajouté par JavaScript, donc invisible pour
qui télécharge la page. L'ancienne version de ce script a cessé de voir les
complets sans jamais échouer.

En revanche le site interroge une fonction publique qui renvoie le nombre
d'inscrits par session. On s'en sert ici, ce qui donne à Amio le même niveau
de détail qu'aux Causeries : courbe de remplissage et non plus simple binaire.

Le catalogue vient des liens de réservation, qui portent déjà le titre, la
date et le lieu en paramètres. La capacité est lue dans le code du site, et
mémorisée une fois trouvée ; sans elle, le complet reste indéterminé.
"""
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
SESSIONS = re.compile(r'/assets/(sessions-[A-Za-z0-9_-]+\.js)|"(sessions-[A-Za-z0-9_-]+\.js)"')
CLE = re.compile(r"sb_[A-Za-z0-9_-]{20,}")
CAPACITE = re.compile(r'slug:"([a-z0-9-]+)"[^}]{0,400}?capacity:(\d+)|capacity:(\d+)[^}]{0,400}?slug:"([a-z0-9-]+)"')


def catalogue(page):
    """Les sessions annoncées, lues dans les liens de réservation."""
    sessions = {}
    for href in LIEN.findall(page):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(href.replace("&amp;", "&")).query)
        prendre = lambda k: v.propre(params.get(k, [""])[0])
        cle, titre = prendre("event"), prendre("title")
        if not cle or not titre or cle in sessions:
            continue
        sessions[cle] = {
            "titre": titre,
            "date": prendre("date").split("·")[0].strip(),
            "etiquette_date": prendre("date"),
            "lieu": prendre("venue").split(",")[0][:60],
            "url": SITE + href.replace("&amp;", "&"),
            "intervenant": " ".join(m.capitalize() for m in cle.split("-")[-2:]) or None,
        }
    return sessions


def secrets(page):
    """Clé d'accès et capacités, extraites du code du site."""
    cle, capacites = None, {}
    noms = BUNDLE.findall(page)
    for nom in noms[:3]:
        try:
            code = v.fetch(f"{SITE}/assets/{nom}", essais=2)
        except Exception:
            continue
        trouve = CLE.search(code)
        if trouve and not cle:
            cle = trouve.group(0)
        for s in SESSIONS.findall(code):
            fichier = s[0] or s[1]
            try:
                sess = v.fetch(f"{SITE}/assets/{fichier}", essais=2)
            except Exception:
                continue
            for a, b, c, d in CAPACITE.findall(sess):
                if a:
                    capacites[a] = int(b)
                elif d:
                    capacites[d] = int(c)
            break
    return cle, capacites


def inscrits(cle, etiquettes):
    if not cle or not etiquettes:
        return {}
    try:
        rep = json.loads(v.fetch(DISPOS, essais=2, donnees={"dates": etiquettes},
                                 entetes={"Authorization": f"Bearer {cle}"}))
    except Exception as erreur:
        print(f"Compteur d'inscrits indisponible ({type(erreur).__name__}).", file=sys.stderr)
        return {}
    return rep.get("counts", {}) if rep.get("success") else {}


def main():
    page = v.fetch(PAGE)
    sessions = catalogue(page)
    if not sessions:
        print("Aucune session trouvée : site en panne ou structure modifiée ?", file=sys.stderr)
        sys.exit(1)

    cle, capacites = secrets(page)
    compteur = inscrits(cle, [s["etiquette_date"] for s in sessions.values()])
    if not compteur:
        print("Pas de nombre d'inscrits ce passage : suivi limité au catalogue.", file=sys.stderr)

    for identifiant, s in sessions.items():
        n = compteur.get(s.pop("etiquette_date"))
        cap = capacites.get(identifiant)
        s["inscrits"] = n if isinstance(n, int) else None
        s["capacite"] = cap
        s["places"] = (cap - n) if (cap and isinstance(n, int)) else None
        s["complet"] = (s["places"] <= 0) if s["places"] is not None else None

    etat = v.charger(ETAT)
    premier = etat is None
    etat = etat or {}
    nouveaux, rouverts, _ = v.fusionner(etat, sessions, premier_passage=premier)
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
