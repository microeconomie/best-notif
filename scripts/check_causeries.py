#!/usr/bin/env python3
"""Veille des causeries parisiennes (causeriesdecomptoir.fr).

Le site est une application JavaScript dont la page est vide pour qui la
télécharge, mais ses données viennent d'une base Supabase à l'interface
publique. C'est le plus riche des quatre : on y lit le nombre de places
restantes, et surtout la date de mise en ligne enregistrée par l'organisateur,
qui donne la vraie parution et non notre date de détection.

Deux alertes : nouvelle causerie et dernières places. Les remises en vente
sont enregistrées dans le journal mais ne déclenchent pas de mail, ce site
n'étant pas le pilote retenu pour cette alerte.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veille as v

SITE = "https://causeriesdecomptoir.fr/"
API = "https://hphoixtruwbvoobubbtl.supabase.co/rest/v1/events"
ETAT = "data/causeries_seen.json"
ORGANISATEUR = "Causeries"
VILLE = "paris"
SEUIL_PLACES = 3

CLE = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
       "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhwaG9peHRydXdidm9vYnViYnRsIiwicm9sZSI6"
       "ImFub24iLCJpYXQiOjE3NzUxNjEwNzYsImV4cCI6MjA5MDczNzA3Nn0."
       "MADD6c59OLy2LgUgrJUs7bc-tThyCH-KbXq0R34wUbo")
CHAMPS = ("id,title,subtitle,event_date,event_time,city,venue,total_spots,"
          "spots_remaining,speaker_name,speaker_title,created_at")

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def aujourd_hui():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).date()
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(hours=1)).date()


def cle_de_secours():
    """Retrouve la clé publique dans le code du site si elle a changé."""
    page = v.fetch(SITE, essais=2)
    bundle = re.search(r'src="(/assets/index-[^"]+\.js)"', page)
    if not bundle:
        return ""
    code = v.fetch(SITE.rstrip("/") + bundle.group(1), essais=2, delai=60)
    jeton = re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", code)
    return jeton.group(0) if jeton else ""


def url_agenda():
    return (f"{API}?select={CHAMPS}&deleted_at=is.null&is_active=eq.true&is_test=eq.false"
            f"&booking_status=neq.cancelled&event_date=gte.{aujourd_hui().isoformat()}"
            "&order=event_date.asc")


def interroger(url):
    for cle in (CLE, None):
        if cle is None:
            cle = cle_de_secours()
            if not cle or cle == CLE:
                raise RuntimeError("clé Supabase introuvable")
            print("Clé renouvelée : pense à la mettre à jour dans le script.", file=sys.stderr)
        try:
            return json.loads(v.fetch(url, essais=2, entetes={
                "apikey": cle, "Authorization": f"Bearer {cle}", "Accept": "application/json"}))
        except urllib.error.HTTPError as erreur:
            if erreur.code not in (401, 403):
                raise
    return []


def jolie_date(brut):
    date, heure = brut.get("event_date", ""), (brut.get("event_time") or "")[:5]
    try:
        j = datetime.strptime(date, "%Y-%m-%d").date()
        texte = f"{JOURS[j.weekday()]} {j.day} {MOIS[j.month - 1]} {j.year}"
    except ValueError:
        texte = date
    return f"{texte} à {heure.replace(':', 'h')}" if heure else texte


def recuperer(donnees):
    causeries = {}
    for brut in donnees:
        if (brut.get("city") or "").strip().lower() != VILLE:
            continue
        identifiant, titre = brut.get("id"), (brut.get("title") or "").strip()
        if not identifiant or not titre:
            continue
        restantes, total = brut.get("spots_remaining"), brut.get("total_spots")
        causeries[identifiant] = {
            "titre": titre,
            "date": jolie_date(brut),
            "date_iso": brut.get("event_date"),
            "lieu": (brut.get("venue") or "").strip(),
            "url": f"{SITE}reservation?eventId={identifiant}",
            "intervenant": (brut.get("speaker_name") or "").strip() or None,
            "specialite": (brut.get("speaker_title") or "").strip() or None,
            "publie_le": (brut.get("created_at") or "")[:16] or None,
            "capacite": total if isinstance(total, int) else None,
            "places": restantes if isinstance(restantes, int) else None,
            "inscrits": (total - restantes) if isinstance(total, int) and isinstance(restantes, int) else None,
            "complet": (not isinstance(restantes, int)) or restantes <= 0,
        }
    return len(donnees), causeries


def completer_parutions(etat):
    """Retrouve la date de mise en ligne des causeries déjà passées.

    L'agenda ne renvoie que les causeries à venir, si bien que celles
    enregistrées avant que le script ne lise created_at n'ont jamais eu leur
    vraie date de publication. On les redemande par identifiant, sans filtre
    de date, ce qui transforme une parution approximative en parution exacte.
    """
    manquantes = [c for c, f in etat.items() if not f.get("publie_le")][:100]
    if not manquantes:
        return 0
    try:
        lignes = interroger(f"{API}?select=id,created_at&id=in.({','.join(manquantes)})")
    except Exception as erreur:
        print(f"Parutions passées non récupérées ({type(erreur).__name__}).", file=sys.stderr)
        return 0
    retrouvees = 0
    for brut in lignes:
        fiche = etat.get(brut.get("id"))
        publie = (brut.get("created_at") or "")[:16]
        if fiche and publie and not fiche.get("publie_le"):
            fiche["publie_le"] = publie
            retrouvees += 1
    return retrouvees


def main():
    total, trouves = recuperer(interroger(url_agenda()))
    if total == 0:
        print("L'API ne renvoie aucune causerie : panne ou changement ?", file=sys.stderr)
        sys.exit(1)

    etat = v.charger(ETAT)
    premier = etat is None
    etat = etat or {}
    nouveaux, _rouverts, derniers = v.fusionner(
        etat, trouves, seuil_places=SEUIL_PLACES, premier_passage=premier)
    retrouvees = completer_parutions(etat)
    v.sauver(ETAT, etat)
    if retrouvees:
        print(f"{retrouvees} dates de mise en ligne retrouvées pour des causeries passées.")

    if premier:
        print(f"Initialisation : {len(trouves)} causeries parisiennes enregistrées, aucune alerte.")
        return
    v.alerter(ORGANISATEUR, SITE + "#prochaines-causeries", [
        ("LAST CHANCE", derniers, f"{len(derniers)} causeries à {SEUIL_PLACES} places ou moins", "hourglass"),
        ("JUST DROPPED", nouveaux, v.accord(len(nouveaux), "nouvelle causerie", "nouvelles causeries"), "speech_balloon"),
    ])
    print(v.resumer(ORGANISATEUR, etat, trouves, total))


if __name__ == "__main__":
    main()
