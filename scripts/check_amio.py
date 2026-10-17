#!/usr/bin/env python3
"""Veille des nouvelles sessions publiées sur amioclub.com/upcoming-sessions.

Cas particulièrement simple : chaque bouton de réservation pointe vers une
adresse /checkout qui porte déjà toutes les informations en paramètres
(event, title, date, venue). On lit donc les liens plutôt que le HTML, ce qui
rend la veille insensible à une refonte graphique du site.

L'identifiant est le paramètre « event », stable et unique. Les sessions
complètes gardent un lien (vers la liste d'attente) et sont donc suivies
comme les autres.

Notifications :
  - création d'une issue GitHub (toujours, via le GITHUB_TOKEN du workflow)
  - notification push ntfy.sh (optionnel, si le secret NTFY_TOPIC existe)
"""
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

PAGE_URL = "https://amioclub.com/upcoming-sessions"
BASE_URL = "https://amioclub.com"
STATE_FILE = Path("data/amio_seen.json")
CHECKOUT_LINK = re.compile(
    r'href="((?:https?://(?:www\.)?amioclub\.com)?/checkout\?[^"]+)"', re.IGNORECASE
)


def tidy(value):
    return re.sub(r"\s+", " ", value or "").strip()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (veille-amio)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_events(page):
    """Chaque lien /checkout porte ses propres données en paramètres."""
    events = {}
    for match in CHECKOUT_LINK.finditer(page):
        href = html.unescape(match.group(1))
        parsed = urllib.parse.urlparse(href)
        params = urllib.parse.parse_qs(parsed.query)

        slug = tidy(params.get("event", [""])[0])
        title = tidy(params.get("title", [""])[0])
        if not slug or not title:
            continue  # lien de paiement générique, sans session rattachée
        if slug in events:
            continue

        # la date porte parfois un suffixe de désambiguïsation : « ... · Criminalité »
        date = tidy(params.get("date", [""])[0]).split("·")[0].strip()
        venue = tidy(params.get("venue", [""])[0])

        events[slug] = {
            "title": title,
            "date": date,
            "venue": venue,
            "url": href if href.startswith("http") else BASE_URL + href,
            "complet": "liste-attente" in href.lower(),
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
    details = ", ".join(p for p in (event.get("date"), event.get("venue")) if p)
    suffixe = " [complet]" if event.get("complet") else ""
    return f"{event['title']} ({details}){suffixe}" if details else event["title"] + suffixe


def notify_github(new):
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("Pas de contexte GitHub, aucune issue créée.")
        return
    title = (
        "Amio : nouvelle session"
        if len(new) == 1
        else f"Amio : {len(new)} nouvelles sessions"
    )
    lines = [f"- **{describe(e)}** : {e['url']}" for e in new.values()]
    payload = {"title": title, "body": f"Détecté sur {PAGE_URL}\n\n" + "\n".join(lines)}
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
            "title": "Nouvelle session Amio",
            "message": describe(event),
            "click": event["url"],
            "tags": ["beer"],
        })
    print("Notification ntfy envoyée.")


def main():
    events = extract_events(fetch(PAGE_URL))
    if not events:
        print("Aucune session trouvée : site en panne ou structure modifiée ?", file=sys.stderr)
        sys.exit(1)

    seen = load_seen()
    if seen is None:
        save_seen(events)
        print(f"Initialisation : {len(events)} sessions enregistrées, aucune alerte.")
        return

    new = {slug: e for slug, e in events.items() if slug not in seen}

    # les sessions déjà connues voient leur fiche rafraîchie (passage en
    # complet, lieu précisé) sans déclencher d'alerte
    seen.update({k: v for k, v in events.items() if k in seen})

    if not new:
        print(f"Rien de nouveau ({len(events)} sessions en ligne).")
        save_seen(seen)
        return

    for event in new.values():
        print("Nouveau :", describe(event), event["url"])
    notify_github(new)
    notify_ntfy(new)
    seen.update(new)
    save_seen(seen)


if __name__ == "__main__":
    main()
