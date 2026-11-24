#!/usr/bin/env python3
"""Veille des talks d'Apéro Talk (aperotalk.com).

Le site marque ses cartes avec la classe « event-card », et celles qui sont
complètes avec « is-soldout ». L'ancienne version de ce script ne repérait un
talk que s'il avait un lien Shotgun : un talk rempli perdait son lien et
disparaissait purement et simplement du suivi, sans jamais être enregistré
comme complet. On lit donc désormais toutes les cartes.

Shotgun ne publie aucun nombre de places, seulement un état binaire.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veille as v

PAGE = "https://aperotalk.com/"
ETAT = "data/aperotalk_seen.json"
ORGANISATEUR = "Apéro Talk"

CARTE = re.compile(r'class="([^"]*\bevent-card\b[^"]*)"', re.IGNORECASE)
TITRE = re.compile(r"<h[23][^>]*>(.*?)</h[23]>", re.S | re.IGNORECASE)
AFFICHE = re.compile(r'alt="Affiche\s*[:—-]\s*([^"]{3,200})"', re.IGNORECASE)
LIGNE_LI = re.compile(r"<li[^>]*>(.*?)</li>", re.S | re.IGNORECASE)
SHOTGUN = re.compile(r'href="(https://shotgun\.live/[a-z]{2}/events/([a-z0-9-]+))"', re.IGNORECASE)
JOURS = "lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
DATE = re.compile(rf"(?:{JOURS})\s+\d{{1,2}}(?:er)?\s+[a-zéû]+(?:\s+\d{{4}})?", re.IGNORECASE)


def recuperer(page):
    """Renvoie (nombre de cartes, événements). Une carte = un talk."""
    reperes = [m.start() for m in CARTE.finditer(page)]
    evenements = {}
    for i, debut in enumerate(reperes):
        fin = reperes[i + 1] if i + 1 < len(reperes) else len(page)
        bloc = page[debut:fin]
        classes = CARTE.match(page[debut:]).group(1) if CARTE.match(page[debut:]) else ""

        titre = ""
        t = TITRE.search(bloc)
        if t:
            titre = v.propre(t.group(1))
        if not titre:
            a = AFFICHE.search(bloc)
            if a:
                titre = v.propre(a.group(1))
        if not titre:
            continue

        lien = SHOTGUN.search(bloc)
        d = DATE.search(v.propre(bloc))
        lieux = [v.propre(x) for x in LIGNE_LI.findall(bloc)]
        lieu = next((x for x in lieux if x and not DATE.match(x)), "")

        cle = lien.group(2).lower() if lien else v.slug(titre)
        evenements[cle] = {
            "titre": titre,
            "date": d.group(0).capitalize() if d else "",
            "lieu": lieu[:60],
            "url": lien.group(1) if lien else PAGE + "#evenements",
            "complet": "is-soldout" in classes.lower(),
            "intervenant": None,
        }
    return len(reperes), evenements


LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
AVEC = re.compile(r"[Aa]vec ([A-ZÉÈÊÀÂÎÔÛÇ][^,\n.;]{2,40})")
NAVIGATEUR = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/141.0.0.0 Safari/537.36"),
              "Accept-Language": "fr-FR,fr;q=0.9"}
SIGLES = {"ia": "IA", "ai": "AI", "adn": "ADN", "ovni": "OVNI", "pib": "PIB"}


def phrase(titre):
    """Shotgun Capitalise Chaque Mot : on revient a une phrase lisible.

    Le decoupage se fait sur les suites de lettres et non sur les mots, pour
    que « L\u2019Ia » et « Peut-On » soient traites comme deux elements.
    """
    rang = [0]

    def remplacer(trouve):
        mot = trouve.group(0)
        bas = mot.lower()
        premier = rang[0] == 0
        rang[0] += 1
        if bas in SIGLES:
            return SIGLES[bas]
        if premier:
            return mot[:1].upper() + bas[1:]
        return bas

    return re.sub(r"[A-Za-z\u00c0-\u024f]+", remplacer, titre)


def fiche_shotgun(url):
    """Titre, date, lieu et intervenant, lus dans les données structurées.

    La page de billetterie expose un bloc schema.org complet, et le garde
    après la soirée. C'est la seule trace qui reste des talks enregistrés par
    la première version de ce script, qui ne retenait que leur lien.
    """
    for brut in LD.findall(v.fetch(url, essais=2, entetes=NAVIGATEUR)):
        try:
            donnees = json.loads(brut)
        except ValueError:
            continue
        if "Event" not in str(donnees.get("@type", "")):
            continue
        intervenant = AVEC.search(donnees.get("description") or "")
        return {
            "titre": phrase(v.propre(donnees.get("name") or "")),
            "date_iso": (donnees.get("startDate") or "")[:10],
            "lieu": v.propre((donnees.get("location") or {}).get("name") or "")[:60],
            "intervenant": v.propre(intervenant.group(1)) if intervenant else None,
        }
    return None


def completer_fiches(etat, limite=10):
    """Comble depuis Shotgun ce que la page d'Apéro Talk ne dit pas.

    Ses cartes n'annoncent ni l'intervenant ni le millésime. La billetterie,
    elle, publie un bloc structuré complet et le garde après la soirée : on y
    puise ce qui manque, sans jamais écraser ce qu'on a déjà.
    """
    nues = [c for c, f in etat.items()
            if f.get("url") and (not (f.get("date") or f.get("date_iso"))
                                 or not f.get("intervenant"))][:limite]
    rhabillees = 0
    for cle in nues:
        fiche = etat[cle]
        try:
            trouve = fiche_shotgun(fiche["url"])
        except Exception as erreur:
            print(f"Shotgun illisible pour {cle} "
                  f"({type(erreur).__name__} {getattr(erreur, 'code', '')}).", file=sys.stderr)
            continue
        if not trouve:
            continue
        comble = False
        for champ, valeur in trouve.items():
            if valeur and not fiche.get(champ):
                fiche[champ] = valeur
                comble = True
        rhabillees += comble
    return rhabillees


def main():
    total, trouves = recuperer(v.fetch(PAGE))
    if not trouves:
        print("Aucune carte trouvée : site en panne ou structure modifiée ?", file=sys.stderr)
        sys.exit(1)

    etat = v.charger(ETAT)
    premier = etat is None
    etat = etat or {}
    nouveaux, rouverts, _ = v.fusionner(etat, trouves, premier_passage=premier)
    rhabillees = completer_fiches(etat)
    v.sauver(ETAT, etat)
    if rhabillees:
        print(f"{rhabillees} fiches complétées depuis Shotgun.")

    if premier:
        print(f"Initialisation : {len(trouves)} talks enregistrés, aucune alerte.")
        return
    v.alerter(ORGANISATEUR, PAGE + "#evenements", [
        ("JUST DROPPED", nouveaux, v.accord(len(nouveaux), "nouveau talk", "nouveaux talks"), "beers"),
    ])
    print(v.resumer(ORGANISATEUR, etat, trouves, total))
    for f in nouveaux.values():
        print("  nouveau :", v.decrire(f))
    for f in rouverts.values():
        print("  remise en vente (non notifiée) :", v.decrire(f))


if __name__ == "__main__":
    main()
