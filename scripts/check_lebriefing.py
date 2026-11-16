#!/usr/bin/env python3
"""Veille des talks parisiens du Briefing (lebriefing.co).

Le site déclare lui-même ce dont on a besoin : chaque ville a son bloc,
repéré par data-city-panel, chaque talk est une carte « event-card », et une
carte complète porte « event-card--sold-out ». On s'appuie dessus plutôt que
sur des approximations. Une troisième ville, Bordeaux, est apparue en
novembre : le filtrage par bloc l'écarte sans rien changer.

Avant d'alerter, le statut de vente est relevé sur Eventbrite, car le site et
sa billetterie se désynchronisent parfois. Ce relevé est informatif et
n'empêche jamais un envoi.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veille as v

PAGE = "https://www.lebriefing.co/"
SOURCE = PAGE + "#programmation"
ETAT = "data/lebriefing_seen.json"
ORGANISATEUR = "Le Briefing"
VILLE = "paris"

BLOC_VILLE = re.compile(r'data-city-panel\s*=\s*"([^"]+)"', re.IGNORECASE)
CARTE = re.compile(r'<article[^>]*\bclass="([^"]*\bevent-card\b[^"]*)"[^>]*>(.*?)</article>', re.S | re.I)
TITRE = re.compile(r'class="[^"]*event-card__title[^"]*"[^>]*>(.*?)</h[1-6]>', re.S | re.I)
META = re.compile(r'class="[^"]*event-card__meta[^"]*"[^>]*>(.*?)</p>', re.S | re.I)
DESC = re.compile(r'class="[^"]*event-card__desc[^"]*"[^>]*>(.*?)</p>', re.S | re.I)
BILLET = re.compile(r'href="(https?://(?:www\.)?(?:eventbrite\.[a-z.]+|shotgun\.live|billetweb\.fr)/[^"]+)"', re.I)
STATUT_EB = re.compile(r'"salesStatus"\s*:\s*"([a-z_]+)"', re.IGNORECASE)
HORS_PARIS = ("bruxelles", "belgique", "ixelles", "le petit bailli", "bordeaux")

STATUTS = {
    "sales_ended": "ventes fermées", "sales_not_started": "ventes pas encore ouvertes",
    "on_sale": "en vente", "sold_out": "complet", "unavailable": "indisponible",
}


def statut_billetterie(url):
    """Lu sur Eventbrite ; en cas d'échec on le dit plutôt que de le taire."""
    if not url or "eventbrite." not in url:
        return ""
    try:
        page = v.fetch(url, essais=2, delai=20)
    except Exception as erreur:
        print(f"Statut Eventbrite non vérifié ({type(erreur).__name__}).", file=sys.stderr)
        return "non vérifié"
    trouve = STATUT_EB.search(page)
    if not trouve:
        return "non vérifié"
    brut = trouve.group(1).lower()
    return STATUTS.get(brut, brut.replace("_", " "))


def section_ville(page):
    blocs = list(BLOC_VILLE.finditer(page))
    for i, bloc in enumerate(blocs):
        if bloc.group(1).strip().lower() != VILLE:
            continue
        fin = blocs[i + 1].start() if i + 1 < len(blocs) else len(page)
        return page[bloc.end():fin]
    return None


def recuperer(page):
    total = len(CARTE.findall(page))
    section = section_ville(page)
    if section is None:
        print("Blocs par ville introuvables, filtrage de repli par mots-clés.", file=sys.stderr)
        section = page
    talks = {}
    for classes, corps in CARTE.findall(section):
        t = TITRE.search(corps)
        if not t:
            continue
        titre = v.propre(t.group(1))
        if not titre:
            continue
        meta = v.propre(META.search(corps).group(1)) if META.search(corps) else ""
        date, _, lieu = meta.partition("@")
        lien = BILLET.search(corps)
        url = lien.group(1) if lien else ""
        if any(x in f"{lieu} {url}".lower() for x in HORS_PARIS):
            continue
        desc = v.propre(DESC.search(corps).group(1)) if DESC.search(corps) else ""
        orateur = desc.split(",")[0].strip() if desc and len(desc.split(",")[0]) < 40 else None
        talks[v.slug(titre)] = {
            "titre": titre,
            "date": date.strip(),
            "lieu": lieu.strip()[:60],
            "url": url or SOURCE,
            "intervenant": orateur,
            "complet": "event-card--sold-out" in classes or "soldout-badge" in corps or not url,
        }
    return total, talks


def main():
    total, trouves = recuperer(v.fetch(PAGE))
    if not trouves:
        print("Aucune carte trouvée : site en panne ou structure modifiée ?", file=sys.stderr)
        sys.exit(1)

    etat = v.charger(ETAT)
    premier = etat is None
    etat = etat or {}
    nouveaux, rouverts, _ = v.fusionner(etat, trouves, premier_passage=premier)

    for fiche in list(nouveaux.values()) + list(rouverts.values()):
        fiche["billetterie"] = statut_billetterie(fiche.get("url", ""))
    v.sauver(ETAT, etat)

    if premier:
        print(f"Initialisation : {len(trouves)} talks parisiens enregistrés, aucune alerte.")
        return
    v.alerter(ORGANISATEUR, SOURCE, [
        ("BACK ON SALE", rouverts, v.accord(len(rouverts), "talk à nouveau disponible", "talks à nouveau disponibles"), "tada"),
        ("JUST DROPPED", nouveaux, v.accord(len(nouveaux), "nouveau talk", "nouveaux talks"), "microphone"),
    ])
    print(v.resumer(ORGANISATEUR, etat, trouves, total))


if __name__ == "__main__":
    main()
