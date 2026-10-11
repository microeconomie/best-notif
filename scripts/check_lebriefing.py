#!/usr/bin/env python3
"""Veille des nouveaux événements parisiens publiés sur lebriefing.co.

Chaque événement est identifié par son lien de billetterie (l'identifiant
Eventbrite), plus stable qu'un titre qui peut être reformulé. Le titre, la date
et le lieu sont lus juste avant ce lien.

Filtre ville : seuls les événements parisiens sont notifiés. Le site n'indique
pas « Bruxelles » sur les cartes, donc un événement n'est écarté que s'il y a
une preuve qu'il est belge (mot-clé, lieu connu, billetterie .be). Dans le
doute, on notifie : mieux vaut une alerte de trop qu'un talk manqué.

Notifications :
  - création d'une issue GitHub (toujours, via le GITHUB_TOKEN du workflow)
  - notification push ntfy.sh (optionnel, si le secret NTFY_TOPIC existe)
"""
import html
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

PAGE_URL = "https://www.lebriefing.co/"
STATE_FILE = Path("data/lebriefing_seen.json")

# Indices qu'un événement n'est pas parisien. Liste à compléter si Le Briefing
# ouvre d'autres villes (Bordeaux, Lyon, Lille...).
HORS_PARIS = [
    "bruxelles", "brussels", "belgique", "belgium", "ixelles", "saint-gilles",
    "le petit bailli", "bordeaux", "marseille", "lyon", "lille", "toulouse",
]

JOURS = "lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
MOIS = ("janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout"
        "|septembre|octobre|novembre|décembre|decembre")
DATE_LINE = re.compile(
    rf"(?:{JOURS})\s*(?:&nbsp;|\s)*\d{{1,2}}(?:er)?\s*(?:&nbsp;|\s)*(?:{MOIS})\s*(?:&nbsp;|\s)*\d{{4}}",
    re.IGNORECASE,
)
HEADING = re.compile(r"<h[1-4][^>]*>(.*?)</h[1-4]>", re.S | re.I)
TICKET_LINK = re.compile(
    r'href="(https?://(?:www\.)?(?:eventbrite\.[a-z.]+|shotgun\.live|billetweb\.fr)/[^"]+)"',
    re.IGNORECASE,
)


def clean(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (veille-lebriefing)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def event_key(url):
    """Identifiant stable : le numéro Eventbrite, sinon l'adresse sans paramètres."""
    numero = re.search(r"/e/(?:[^/?#]*-)?(\d{6,})", url)
    return numero.group(1) if numero else url.split("?")[0]


def is_paris(card_text, url):
    haystack = f"{card_text} {url}".lower()
    if re.search(r"eventbrite\.be|billetweb\.fr/[a-z-]*bruxelles", haystack):
        return False
    return not any(indice in haystack for indice in HORS_PARIS)


def extract_events(page):
    """Une carte = tout ce qui précède un lien de billetterie, depuis le lien d'avant."""
    events = {}
    previous_end = 0
    for match in TICKET_LINK.finditer(page):
        url = match.group(1)
        window = page[previous_end:match.start()]
        previous_end = match.end()

        dates = list(DATE_LINE.finditer(window))
        if not dates:
            continue  # lien de billetterie hors programmation (menu, bannière...)
        date_match = dates[-1]
        headings = [h for h in HEADING.finditer(window) if h.start() >= date_match.end()]
        if not headings:
            continue
        heading = headings[0]

        title = clean(heading.group(1))
        if not title:
            continue
        venue = clean(window[date_match.end():heading.start()]).lstrip("@ ").strip()
        card_text = clean(window[date_match.start():])

        if not is_paris(card_text, url):
            continue

        events[event_key(url)] = {
            "title": title,
            "date": clean(date_match.group(0)),
            "venue": venue[:80],
            "url": url,
        }
    return events


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
    details = " ".join(p for p in (event.get("date"), event.get("venue")) if p)
    return f"{event['title']} ({details})" if details else event["title"]


def notify_github(new):
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("Pas de contexte GitHub, aucune issue créée.")
        return
    title = (
        "Le Briefing : nouvel événement"
        if len(new) == 1
        else f"Le Briefing : {len(new)} nouveaux événements"
    )
    lines = [f"- **{describe(e)}** : {e['url']}" for e in new.values()]
    payload = {
        "title": title,
        "body": f"Détecté sur {PAGE_URL}#programmation\n\n" + "\n".join(lines),
    }
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    if owner:
        payload["assignees"] = [owner]
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        post_json(f"https://api.github.com/repos/{repo}/issues", payload, headers)
    except Exception:
        payload.pop("assignees", None)
        post_json(f"https://api.github.com/repos/{repo}/issues", payload, headers)
    print("Issue GitHub créée.")


def notify_ntfy(new):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    for event in new.values():
        post_json("https://ntfy.sh", {
            "topic": topic,
            "title": "Nouveau Briefing",
            "message": describe(event),
            "click": event["url"],
            "tags": ["microphone"],
        })
    print("Notification ntfy envoyée.")


def main():
    events = extract_events(fetch(PAGE_URL))
    if not events:
        print("Aucun événement trouvé : site en panne ou structure modifiée ?", file=sys.stderr)
        sys.exit(1)

    seen = load_seen()
    if seen is None:
        save_seen(events)
        print(f"Initialisation : {len(events)} événements parisiens enregistrés, aucune alerte.")
        return

    new = {key: e for key, e in events.items() if key not in seen}
    if not new:
        print(f"Rien de nouveau ({len(events)} événements parisiens en ligne).")
        return

    for event in new.values():
        print("Nouveau :", describe(event), event["url"])
    notify_github(new)
    notify_ntfy(new)
    seen.update(new)
    save_seen(seen)


if __name__ == "__main__":
    main()
  
