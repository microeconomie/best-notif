#!/usr/bin/env python3
"""Veille des nouvelles causeries parisiennes de causeriesdecomptoir.fr.

Le site est une application JavaScript : sa page est vide pour qui la
télécharge. En revanche, il lit ses données dans une base Supabase dont
l'interface REST est publique, et c'est elle qu'on interroge ici. On obtient
un JSON propre (ville, date, lieu, places restantes), bien plus fiable que la
lecture d'un HTML.

La disponibilité se lit dans « spots_remaining » : le champ « sales_status »
vaut toujours « auto », le site calculant donc le statut à partir du nombre de
places restantes.

Identité d'une causerie : son identifiant unique en base, parfaitement stable.

Deux alertes :
  - une nouvelle causerie parisienne apparaît ;
  - une causerie parisienne tombe à SEUIL_PLACES places ou moins, signalée une
    seule fois, puis réarmée si le stock remonte au-dessus du seuil.

Notifications :
  - création d'une issue GitHub (toujours, via le GITHUB_TOKEN du workflow)
  - notification push ntfy.sh (optionnel, si le secret NTFY_TOPIC existe)
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SITE_URL = "https://causeriesdecomptoir.fr/"
API_BASE = "https://hphoixtruwbvoobubbtl.supabase.co/rest/v1/events"
STATE_FILE = Path("data/causeries_seen.json")
VILLE = "paris"

# Clé publique du site, embarquée dans son code JavaScript et livrée à chaque
# visiteur : elle n'ouvre que la lecture de ce qui est déjà public. Si elle est
# un jour changée, le script la retrouve tout seul (voir cle_de_secours).
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImhwaG9peHRydXdidm9vYnViYnRsIiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3NzUxNjEwNzYsImV4cCI6MjA5MDczNzA3Nn0."
    "MADD6c59OLy2LgUgrJUs7bc-tThyCH-KbXq0R34wUbo"
)
CHAMPS = "id,title,subtitle,event_date,event_time,city,venue,total_spots,spots_remaining"

# Nombre de places à partir duquel on prévient qu'il ne reste presque rien.
SEUIL_PLACES = 3

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
        "août", "septembre", "octobre", "novembre", "décembre"]


def aujourd_hui():
    """Date du jour à Paris, pour ne pas écarter une causerie du soir même."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Paris")).date()
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(hours=1)).date()


def lire(url, cle):
    req = urllib.request.Request(url, headers={
        "apikey": cle,
        "Authorization": f"Bearer {cle}",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (veille-causeries)",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cle_de_secours():
    """Retrouve la clé publique dans le code du site, si celle-ci a changé."""
    page = urllib.request.urlopen(urllib.request.Request(
        SITE_URL, headers={"User-Agent": "Mozilla/5.0 (veille-causeries)"}
    ), timeout=30).read().decode("utf-8", errors="replace")
    bundle = re.search(r'src="(/assets/index-[^"]+\.js)"', page)
    if not bundle:
        return ""
    code = urllib.request.urlopen(urllib.request.Request(
        SITE_URL.rstrip("/") + bundle.group(1),
        headers={"User-Agent": "Mozilla/5.0 (veille-causeries)"}
    ), timeout=60).read().decode("utf-8", errors="replace")
    jeton = re.search(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", code)
    return jeton.group(0) if jeton else ""


def fetch_events():
    url = (f"{API_BASE}?select={CHAMPS}"
           "&deleted_at=is.null&is_active=eq.true&is_test=eq.false"
           "&booking_status=neq.cancelled"
           f"&event_date=gte.{aujourd_hui().isoformat()}&order=event_date.asc")
    try:
        return lire(url, ANON_KEY)
    except urllib.error.HTTPError as erreur:
        if erreur.code not in (401, 403):
            raise
        print("Clé refusée, recherche de la clé à jour sur le site.", file=sys.stderr)
        nouvelle = cle_de_secours()
        if not nouvelle or nouvelle == ANON_KEY:
            raise
        donnees = lire(url, nouvelle)
        print("Clé renouvelée : pense à la mettre à jour dans le script.", file=sys.stderr)
        return donnees


def jolie_date(event):
    date, heure = event.get("event_date", ""), (event.get("event_time") or "")[:5]
    try:
        jour = datetime.strptime(date, "%Y-%m-%d").date()
        texte = f"{JOURS[jour.weekday()]} {jour.day} {MOIS[jour.month - 1]} {jour.year}"
    except ValueError:
        texte = date
    return f"{texte} à {heure.replace(':', 'h')}" if heure else texte


def extract_events(donnees):
    """Renvoie (nombre total de causeries, causeries de la ville suivie)."""
    events = {}
    for brut in donnees:
        if (brut.get("city") or "").strip().lower() != VILLE:
            continue
        identifiant = brut.get("id")
        titre = (brut.get("title") or "").strip()
        if not identifiant or not titre:
            continue
        restantes = brut.get("spots_remaining")
        events[identifiant] = {
            "title": titre,
            "date": jolie_date(brut),
            "venue": (brut.get("venue") or "").strip(),
            "url": f"{SITE_URL}reservation?eventId={identifiant}",
            "places": restantes,
            "complet": not isinstance(restantes, int) or restantes <= 0,
        }
    return len(donnees), events


def derniers_billets(event):
    """Vrai quand il reste peu de places, mais qu'il en reste."""
    places = event.get("places")
    return isinstance(places, int) and 0 < places <= SEUIL_PLACES


def load_seen():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return None


def save_seen(seen):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(seen, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def post_json(url, payload, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status


def describe(event):
    details = ", ".join(p for p in (event.get("date"), event.get("venue")) if p)
    if event.get("complet"):
        etat = " [complet]"
    elif event.get("places") == 1:
        etat = " [1 place restante]"
    elif isinstance(event.get("places"), int):
        etat = f" [{event['places']} places restantes]"
    else:
        etat = ""
    return f"{event['title']} ({details}){etat}" if details else event["title"] + etat


def accord(nombre, singulier, pluriel):
    return f"{nombre} {singulier if nombre == 1 else pluriel}"


def objet(prefixe, lot, resume_pluriel):
    """Objet du mail : le titre de la causerie quand il n'y en a qu'une."""
    if len(lot) == 1:
        event = next(iter(lot.values()))
        detail = event["title"]
        if len(detail) > 80:
            detail = detail[:79].rstrip() + "…"
        if prefixe == "LAST CHANCE" and isinstance(event.get("places"), int):
            detail += f" ({accord(event['places'], 'place', 'places')})"
    else:
        detail = resume_pluriel
    return f"{prefixe} Causeries : {detail}"


def creer_issue(titre, lot):
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    lignes = [f"- **{describe(e)}** : {e['url']}" for e in lot.values()]
    corps = f"Détecté sur {SITE_URL}#prochaines-causeries\n\n" + "\n".join(lignes)
    if not token or not repo:
        print(f"Pas de contexte GitHub, issue non créée : {titre}")
        return
    payload = {"title": titre, "body": corps}
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    if owner:
        payload["assignees"] = [owner]
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        post_json(f"https://api.github.com/repos/{repo}/issues", payload, headers)
    except Exception:
        payload.pop("assignees", None)
        post_json(f"https://api.github.com/repos/{repo}/issues", payload, headers)
    print(f"Issue GitHub créée : {titre}")


def notify_github(nouveaux, seuil):
    """Un mail pour les fins de stock, un autre pour les parutions."""
    if seuil:
        creer_issue(
            objet("LAST CHANCE", seuil,
                  f"{len(seuil)} causeries à {SEUIL_PLACES} places ou moins"),
            seuil)
    if nouveaux:
        creer_issue(
            objet("JUST DROPPED", nouveaux,
                  accord(len(nouveaux), "nouvelle causerie", "nouvelles causeries")),
            nouveaux)


def notify_ntfy(nouveaux, seuil):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    for titre, lot, tag in (("LAST CHANCE Causeries", seuil, "hourglass"),
                            ("JUST DROPPED Causeries", nouveaux, "speech_balloon")):
        for event in lot.values():
            post_json("https://ntfy.sh", {
                "topic": topic,
                "title": titre,
                "message": describe(event),
                "click": event["url"],
                "tags": [tag],
            })
    print("Notification ntfy envoyée.")


def main():
    total, events = extract_events(fetch_events())
    if total == 0:
        print("L'API ne renvoie aucune causerie : panne ou changement ?", file=sys.stderr)
        sys.exit(1)

    seen = load_seen()
    if seen is None:
        for event in events.values():
            event["alerte_seuil"] = derniers_billets(event)
        save_seen(events)
        print(f"Initialisation : {len(events)} causeries parisiennes enregistrées "
              f"({total} toutes villes), aucune alerte.")
        return

    nouveaux = {k: e for k, e in events.items() if k not in seen}
    seuil = {
        k: e for k, e in events.items()
        if k not in nouveaux and derniers_billets(e)
        and not seen[k].get("alerte_seuil")
    }

    # mémorise l'état du seuil : une causerie déjà signalée ne le sera plus,
    # et repasser au-dessus du seuil réarme l'alerte
    for event in events.values():
        event["alerte_seuil"] = derniers_billets(event)

    seen.update(events)  # fiches rafraîchies : places restantes, lieu, date
    save_seen(seen)

    complets = sum(1 for e in events.values() if e["complet"])
    etat = (f"{len(events)} causeries parisiennes dont {complets} complète"
            f"{'s' if complets > 1 else ''}, {total} toutes villes")

    if not nouveaux and not seuil:
        print(f"Rien de nouveau ({etat}).")
        return

    for event in seuil.values():
        print("Dernières places :", describe(event), event["url"])
    for event in nouveaux.values():
        print("Nouveau :", describe(event), event["url"])
    notify_github(nouveaux, seuil)
    notify_ntfy(nouveaux, seuil)


if __name__ == "__main__":
    main()
