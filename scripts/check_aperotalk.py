#!/usr/bin/env python3
"""Veille des nouveaux événements publiés sur aperotalk.com.

Repère chaque événement grâce à son lien Shotgun, compare avec ceux déjà vus
(data/aperotalk_seen.json) et notifie en cas de nouveauté :
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

PAGE_URL = "https://aperotalk.com/"
STATE_FILE = Path("data/aperotalk_seen.json")
EVENT_LINK = re.compile(
    r'href="(https://shotgun\.live/[a-z]{2}/events/([a-z0-9-]+))"', re.IGNORECASE
)


def clean(fragment):
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (veille-aperotalk)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def extract_events(page):
    events = {}
    for match in EVENT_LINK.finditer(page):
        url, slug = match.group(1), match.group(2).lower()
        if slug in events:
            continue
        after = page[match.end(): match.end() + 4000]
        title_match = re.search(r"<h3[^>]*>(.*?)</h3>", after, re.S | re.I)
        title = clean(title_match.group(1)) if title_match else slug.replace("-", " ").capitalize()
        date = ""
        if title_match:
            li = re.search(r"<li[^>]*>(.*?)</li>", after[title_match.end():], re.S | re.I)
            date = clean(li.group(1)) if li else ""
        events[slug] = {"title": title, "date": date, "url": url}
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
    return f"{event['title']} ({event['date']})" if event.get("date") else event["title"]


def notify_github(new):
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("Pas de contexte GitHub, aucune issue créée.")
        return
    title = (
        "Apéro Talk : nouvel événement"
        if len(new) == 1
        else f"Apéro Talk : {len(new)} nouveaux événements"
    )
    lines = [f"- **{describe(e)}** : {e['url']}" for e in new.values()]
    payload = {"title": title, "body": f"Détecté sur {PAGE_URL}#evenements\n\n" + "\n".join(lines)}
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
            "title": "Nouvel Apéro Talk",
            "message": describe(event),
            "click": event["url"],
            "tags": ["beers"],
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
        print(f"Initialisation : {len(events)} événements enregistrés, aucune alerte.")
        return

    new = {slug: e for slug, e in events.items() if slug not in seen}
    if not new:
        print(f"Rien de nouveau ({len(events)} événements en ligne).")
        return

    for event in new.values():
        print("Nouveau :", describe(event), event["url"])
    notify_github(new)
    notify_ntfy(new)
    seen.update(new)
    save_seen(seen)


if __name__ == "__main__":
    main()
