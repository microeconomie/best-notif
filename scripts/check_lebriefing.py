#!/usr/bin/env python3
"""Veille des talks du Briefing (lebriefing.co), via l'API du site.

La page d'accueil ne sert à rien ici : son HTML présente toutes les cartes
comme ouvertes, et c'est un script de la page qui vient ensuite poser la
classe « event-card--sold-out » sur les soirées pleines. Un simple
téléchargement n'en voyait donc jamais aucune, et la veille a vécu deux mois
en annonçant deux complets sur trente et un.

Ce script lit donc la même source que la page : /api/content, qui donne pour
chaque soirée son titre, son intervenant, sa salle, sa date et son statut.
Eventbrite n'arbitre rien : son statut de vente vaut pour toutes les
catégories de billets confondues, et annonce donc « en vente » tant qu'une
seule d'entre elles reste ouverte, fût-elle réservée aux membres. Une soirée
dont les billets généraux sont épuisés y apparaît disponible. Le statut du
site fait foi ; celui de la billetterie n'est conservé qu'à titre indicatif
sur les talks nouvellement repérés.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veille as v

SITE = "https://lebriefing.co"
API = SITE + "/api/content"
SOURCE = SITE + "/#evenements"
ETAT = "data/lebriefing_seen.json"
ORGANISATEUR = "Le Briefing"
VILLE = "paris"

STATUT_EB = re.compile(r'"salesStatus"\s*:\s*"([a-z_]+)"')
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
    return STATUTS.get(trouve.group(1), trouve.group(1))


def recuperer(brut):
    """Renvoie (nombre de soirées annoncées, talks parisiens).

    Les clés restent le slug du titre, comme du temps de la lecture du HTML,
    pour ne pas rompre l'historique des fiches déjà suivies.
    """
    donnees = json.loads(brut)
    annonces = (donnees.get("eventsPublic") or []) + (donnees.get("eventsExclusive") or [])
    talks = {}
    for e in annonces:
        titre = v.propre(e.get("label") or "")
        if not titre or v.propre(e.get("city") or "").lower() != VILLE:
            continue
        cle = v.slug(titre)
        if cle in talks:
            continue
        talks[cle] = {
            "titre": titre,
            "intervenant": v.propre(e.get("speaker") or "") or None,
            "specialite": v.propre(e.get("role") or "") or None,
            "date": v.propre(e.get("when") or ""),
            "date_iso": (e.get("startsAt") or "")[:10],
            "lieu": v.propre(e.get("where") or "").split(",")[0][:60],
            "url": e.get("reserveUrl") or "",
            "complet": e.get("status") == "sold_out",
            "exclusif": bool(e.get("exclusive")),
        }
    return len(annonces), talks


def main():
    total, trouves = recuperer(v.fetch(API))
    if not trouves:
        print("Aucun talk parisien renvoyé par l'API : panne ou changement ?", file=sys.stderr)
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
        ("BACK ON SALE", rouverts,
         v.accord(len(rouverts), "talk à nouveau disponible", "talks à nouveau disponibles"), "tada"),
        ("JUST DROPPED", nouveaux,
         v.accord(len(nouveaux), "nouveau talk", "nouveaux talks"), "microphone"),
    ])
    print(v.resumer(ORGANISATEUR, etat, trouves, total))
    for fiche in nouveaux.values():
        print("  nouveau :", v.decrire(fiche))


if __name__ == "__main__":
    main()
