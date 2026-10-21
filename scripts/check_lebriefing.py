#!/usr/bin/env python3
"""Veille des événements parisiens publiés sur lebriefing.co.

Deux types d'alerte :
  - un nouveau talk apparaît dans la programmation ;
  - un talk complet redevient réservable, souvent l'occasion la plus utile
    puisque ces soirées se remplissent vite.

Le site déclare lui-même ce dont on a besoin, on s'appuie donc dessus plutôt
que sur des approximations :
  - chaque ville a son bloc, repéré par data-city-panel="paris" ;
  - chaque talk est un <article class="event-card">, affublé de la classe
    event-card--sold-out quand il est complet ;
  - un talk réservable porte un lien <a class="event-btn--reserve">.

Identité d'un talk : son titre normalisé. Le numéro Eventbrite serait plus
stable, mais il disparaît de la page dès que le talk est complet ; le titre,
lui, reste, ce qui permet de suivre un même talk à travers ses changements de
statut.

Avant d'alerter, le statut de vente est relevé sur Eventbrite, car le site et
sa billetterie se désynchronisent parfois : un talk peut être présenté comme
réservable alors que les ventes y sont closes. Ce relevé est purement
informatif, il n'empêche jamais l'envoi d'une alerte, et son échec éventuel
est signalé plutôt que silencieux.

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
VILLE = "paris"

CITY_PANEL = re.compile(r'data-city-panel\s*=\s*"([^"]+)"', re.IGNORECASE)
ARTICLE = re.compile(
    r'<article[^>]*\bclass="([^"]*\bevent-card\b[^"]*)"[^>]*>(.*?)</article>',
    re.S | re.IGNORECASE,
)
TITLE = re.compile(r'class="[^"]*event-card__title[^"]*"[^>]*>(.*?)</h[1-6]>', re.S | re.I)
META = re.compile(r'class="[^"]*event-card__meta[^"]*"[^>]*>(.*?)</p>', re.S | re.I)
TICKET_LINK = re.compile(
    r'href="(https?://(?:www\.)?(?:eventbrite\.[a-z.]+|shotgun\.live|billetweb\.fr)/[^"]+)"',
    re.IGNORECASE,
)
# repli si le site est refondu et que les blocs par ville disparaissent
HORS_PARIS = ("bruxelles", "brussels", "belgique", "ixelles", "le petit bailli")


def clean(fragment):
    fragment = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(fragment).replace(" ", " ")).strip()


def slugify(value):
    value = unicodedata.normalize("NFKD", (value or "").lower())
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value)).strip("-")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (veille-lebriefing)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def ticket_id(url):
    numero = re.search(r"/e/(?:[^/?#]*-)?(\d{6,})", url or "")
    return numero.group(1) if numero else ""


def city_section(page):
    """Portion de page consacrée à la ville suivie, ou None si non étiquetée."""
    panels = list(CITY_PANEL.finditer(page))
    for index, panel in enumerate(panels):
        if panel.group(1).strip().lower() != VILLE:
            continue
        end = panels[index + 1].start() if index + 1 < len(panels) else len(page)
        return page[panel.end():end]
    return None


def read_card(classes, body):
    title = TITLE.search(body)
    if not title:
        return None
    title = clean(title.group(1))
    if not title:
        return None

    meta = clean(META.search(body).group(1)) if META.search(body) else ""
    date, _, venue = meta.partition("@")
    link = TICKET_LINK.search(body)
    url = link.group(1) if link else ""
    complet = "event-card--sold-out" in classes or "soldout-badge" in body

    return {
        "title": title,
        "date": date.strip(),
        "venue": venue.strip()[:80],
        "url": url or PROG_URL,
        "ticket_id": ticket_id(url),
        "complet": bool(complet) or not url,
    }


def extract_events(page):
    """Renvoie (nombre total de cartes sur la page, cartes de la ville suivie)."""
    total = len(ARTICLE.findall(page))
    section = city_section(page)
    events = {}

    if section is None:
        # le site ne déclare plus ses villes : on retombe sur un filtrage par
        # mots-clés, moins sûr mais préférable à une veille aveugle
        print("Blocs par ville introuvables, filtrage de repli par mots-clés.", file=sys.stderr)
        section = page

    for classes, body in ARTICLE.findall(section):
        card = read_card(classes, body)
        if not card:
            continue
        repere = f"{card['venue']} {card['url']}".lower()
        if any(indice in repere for indice in HORS_PARIS):
            continue
        events[slugify(card["title"])] = card
    return total, events


EVENTBRITE_STATUTS = {
    "sales_ended": "ventes fermées",
    "sales_not_started": "ventes pas encore ouvertes",
    "on_sale": "en vente",
    "sold_out": "complet",
    "unavailable": "indisponible",
}
SALES_STATUS = re.compile(r'"salesStatus"\s*:\s*"([a-z_]+)"', re.IGNORECASE)


def statut_eventbrite(url):
    """Statut de vente lu sur la billetterie, ou une mention d'échec.

    Volontairement tolérant : si Eventbrite est injoignable ou bloque nos
    serveurs, on le dit dans l'alerte au lieu de la retenir.
    """
    if not url or "eventbrite." not in url:
        return ""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=20) as resp:
            page = resp.read().decode("utf-8", errors="replace")
    except Exception as erreur:
        print(f"Statut Eventbrite non vérifié ({type(erreur).__name__}) : {url}", file=sys.stderr)
        return "non vérifié"
    trouve = SALES_STATUS.search(page)
    if not trouve:
        return "non vérifié"
    brut = trouve.group(1).lower()
    return EVENTBRITE_STATUTS.get(brut, brut.replace("_", " "))


def was_complet(entry):
    """Statut précédent, y compris pour les fiches d'avant l'ajout de ce champ.

    Les anciennes versions n'enregistraient pas le statut mais stockaient
    l'adresse de la billetterie quand elle existait : une fiche pointant vers
    la page du site correspondait donc à un talk complet.
    """
    if "complet" in entry:
        return bool(entry["complet"])
    return not TICKET_LINK.search(f'href="{entry.get("url", "")}"')


def migrate(seen):
    """Reclasse un état enregistré sous d'anciennes clés (numéros Eventbrite)."""
    migrated = {}
    for key, entry in seen.items():
        slug = entry.pop("slug", "") or slugify(entry.get("title", "")) or key
        entry.setdefault("ticket_id", ticket_id(entry.get("url", "")))
        entry.setdefault("complet", was_complet(entry))
        migrated[slug] = entry
    return migrated


def load_seen():
    if STATE_FILE.exists():
        return migrate(json.loads(STATE_FILE.read_text(encoding="utf-8")))
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


def accord(nombre, singulier, pluriel):
    return f"{nombre} {singulier if nombre == 1 else pluriel}"


def ligne(event):
    etiquette = f" [Eventbrite : {event['eventbrite']}]" if event.get("eventbrite") else ""
    return f"- **{describe(event)}**{etiquette} : {event['url']}"


def objet(prefixe, lot, resume_pluriel):
    """Objet du mail : l'étiquette d'abord, toujours au même endroit."""
    if len(lot) == 1:
        event = next(iter(lot.values()))
        detail = event["title"]
        if len(detail) > 60:
            detail = detail[:59].rstrip() + "…"
        if event.get("eventbrite"):
            detail += f" [Eventbrite : {event['eventbrite']}]"
    else:
        detail = resume_pluriel
    return f"{prefixe} Le Briefing : {detail}"


def creer_issue(titre, lot):
    token, repo = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    corps = f"Détecté sur {PROG_URL}\n\n" + "\n".join(ligne(e) for e in lot.values())
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


def notify_github(nouveaux, rouverts):
    """Un mail par catégorie, pour que l'étiquette reste lisible d'un coup d'oeil."""
    if rouverts:
        creer_issue(
            objet("BACK ON SALE", rouverts,
                  accord(len(rouverts), "talk à nouveau disponible",
                         "talks à nouveau disponibles")),
            rouverts)
    if nouveaux:
        creer_issue(
            objet("JUST DROPPED", nouveaux,
                  accord(len(nouveaux), "nouveau talk", "nouveaux talks")),
            nouveaux)


def notify_ntfy(nouveaux, rouverts):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    for titre, lot, tag in (("BACK ON SALE Le Briefing", rouverts, "tada"),
                            ("JUST DROPPED Le Briefing", nouveaux, "microphone")):
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
    total, events = extract_events(fetch(PAGE_URL))
    if not events:
        print("Aucune carte trouvée : site en panne ou structure modifiée ?", file=sys.stderr)
        sys.exit(1)

    seen = load_seen()
    if seen is None:
        save_seen(events)
        print(f"Initialisation : {len(events)} talks parisiens enregistrés "
              f"({total} cartes au total), aucune alerte.")
        return

    nouveaux = {k: e for k, e in events.items() if k not in seen}
    rouverts = {
        k: e for k, e in events.items()
        if k in seen and was_complet(seen[k]) and not e["complet"]
    }

    seen.update(events)  # fiches rafraîchies : statut, lien, lieu
    save_seen(seen)

    complets = sum(1 for e in events.values() if e["complet"])
    etat = (f"{len(events)} talks parisiens dont {complets} complet"
            f"{'s' if complets > 1 else ''}, {total} cartes au total")

    if not nouveaux and not rouverts:
        print(f"Rien de nouveau ({etat}).")
        return

    for event in list(rouverts.values()) + list(nouveaux.values()):
        event["eventbrite"] = statut_eventbrite(event.get("url", ""))

    for event in rouverts.values():
        print("Remise en vente :", ligne(event))
    for event in nouveaux.values():
        print("Nouveau :", ligne(event))
    notify_github(nouveaux, rouverts)
    notify_ntfy(nouveaux, rouverts)


if __name__ == "__main__":
    main()
