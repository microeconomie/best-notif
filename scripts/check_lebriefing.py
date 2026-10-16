#!/usr/bin/env python3
"""Veille des nouveaux événements parisiens publiés sur lebriefing.co.

Lecture de toutes les cartes de la section « Programmation », y compris celles
affichées « Sold out » : quand tous les talks parisiens sont complets, la page
n'affiche plus aucun lien de billetterie, et se fier aux liens reviendrait à
croire que le site est cassé.

Identité d'un événement : le numéro Eventbrite quand un lien existe, sinon le
titre normalisé. Les deux sont comparés à l'état connu, pour qu'un talk qui
perd son lien (passage en complet) ou qui en gagne un (remise en vente) ne soit
pas pris pour un nouveau.

Filtre ville : seuls les événements parisiens sont notifiés. Le site n'écrit
pas « Bruxelles » sur les cartes, donc un événement n'est écarté que s'il y a
une preuve qu'il n'est pas parisien (lieu connu, mot-clé, billetterie .be).
Dans le doute, on notifie.

Notifications :
  - création d'une issue GitHub (toujours, via le GITHUB_TOKEN du workflow)
  - notification push ntfy.sh (optionnel, si le secret NTFY_TOPIC existe)
"""
import html
import json
import os
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

PAGE_URL = "https://www.lebriefing.co/"
PROG_URL = PAGE_URL + "#programmation"
STATE_FILE = Path("data/lebriefing_seen.json")

# Indices qu'un événement n'est pas parisien. À compléter si Le Briefing ouvre
# d'autres villes (Bordeaux, Lyon, Lille...), lieux compris.
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


def slugify(value):
    value = unicodedata.normalize("NFKD", value.lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value)).strip("-")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (veille-lebriefing)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def ticket_id(url):
    """Numéro Eventbrite, seul identifiant vraiment stable quand il est là."""
    if not url:
        return ""
    numero = re.search(r"/e/(?:[^/?#]*-)?(\d{6,})", url)
    return numero.group(1) if numero else url.split("?")[0]


def is_paris(card_text, url):
    haystack = f"{card_text} {url}".lower()
    if re.search(r"eventbrite\.be|billetweb\.fr/[a-z-]*bruxelles", haystack):
        return False
    return not any(indice in haystack for indice in HORS_PARIS)


def extract_events(page):
    """Une carte = une ligne de date, puis le premier titre qui suit.

    Renvoie (toutes_les_cartes, cartes_parisiennes). Le premier compte sert de
    garde-fou : si la page ne donne plus aucune carte, la structure a changé.
    """
    total = 0
    paris = {}
    starts = [m.start() for m in DATE_LINE.finditer(page)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(page)
        window = page[start:end]

        date_match = DATE_LINE.search(window)
        headings = [h for h in HEADING.finditer(window) if h.start() >= date_match.end()]
        if not headings:
            continue  # date isolée (pied de page, mention légale...), pas une carte
        heading = headings[0]
        title = clean(heading.group(1))
        if not title:
            continue

        total += 1
        link = TICKET_LINK.search(window)
        url = link.group(1) if link else ""
        venue = clean(window[date_match.end():heading.start()]).lstrip("@ ").strip()
        card_text = clean(window[:heading.end()])

        if not is_paris(card_text, url):
            continue

        slug = slugify(title)
        paris[ticket_id(url) or slug] = {
            "title": title,
            "date": clean(date_match.group(0)),
            "venue": venue[:80],
            "url": url or PROG_URL,
            "slug": slug,
            "ticket_id": ticket_id(url),
        }
    return total, paris


def known_keys(seen):
    """Tout ce qui permet de reconnaître un événement déjà vu.

    Les anciens états ne contenaient ni « slug » ni « ticket_id » : on les
    reconstitue depuis le titre et la clé pour rester compatible.
    """
    ids, slugs = set(), set()
    for key, entry in seen.items():
        ids.add(key)
        if entry.get("ticket_id"):
            ids.add(entry["ticket_id"])
        if entry.get("slug"):
            slugs.add(entry["slug"])
        if entry.get("title"):
            slugs.add(slugify(entry["title"]))
    return ids, slugs


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
    payload = {"title": title, "body": f"Détecté sur {PROG_URL}\n\n" + "\n".join(lines)}
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
    total, events = extract_events(fetch(PAGE_URL))
    if total == 0:
        print("Aucune carte trouvée : site en panne ou structure modifiée ?", file=sys.stderr)
        sys.exit(1)

    seen = load_seen()
    if seen is None:
        save_seen(events)
        print(f"Initialisation : {len(events)} événements parisiens enregistrés "
              f"({total} cartes au total), aucune alerte.")
        return

    ids, slugs = known_keys(seen)
    new = {
        key: event
        for key, event in events.items()
        if key not in ids and event["slug"] not in slugs
        and not (event["ticket_id"] and event["ticket_id"] in ids)
    }

    # les événements déjà connus voient leur fiche rafraîchie (lien apparu ou
    # disparu, lieu précisé) sans déclencher d'alerte
    seen.update({k: v for k, v in events.items() if k in seen})

    if not new:
        print(f"Rien de nouveau ({len(events)} événements parisiens, {total} cartes au total).")
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
