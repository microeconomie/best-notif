#!/usr/bin/env python3
"""Veille des sessions d'Amio (amioclub.com).

Amio a refondu son site : le marqueur « liste-attente » a disparu des liens,
et le badge COMPLET est désormais ajouté par JavaScript, donc invisible pour
qui télécharge la page. L'ancienne version de ce script a cessé de voir les
complets sans jamais échouer.

En revanche le site interroge une fonction publique qui renvoie le nombre
d'inscrits par session. On s'en sert ici, ce qui donne à Amio le même niveau
de détail qu'aux Causeries : courbe de remplissage et non plus simple binaire.

Le catalogue vient des liens de réservation, qui portent déjà le titre, la
date et le lieu en paramètres. La capacité est lue dans le code du site, et
mémorisée une fois trouvée ; sans elle, le complet reste indéterminé.
"""
import datetime
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veille as v

SITE = "https://amioclub.com"
PAGE = SITE + "/upcoming-sessions"
DISPOS = "https://iwodklhrgkftorthbqxl.supabase.co/functions/v1/get-event-availability"
ETAT = "data/amio_seen.json"
ORGANISATEUR = "Amio"

LIEN = re.compile(r'href="(/checkout\?[^"]+)"', re.IGNORECASE)
BUNDLE = re.compile(r'/assets/(index-[A-Za-z0-9_-]+\.js)')
SESSIONS = re.compile(r'/assets/(sessions-[A-Za-z0-9_-]+\.js)|"(sessions-[A-Za-z0-9_-]+\.js)"')
CLE = re.compile(r"sb_[A-Za-z0-9_-]{20,}")
CAPACITE = re.compile(r'slug:"([a-z0-9-]+)"[^}]{0,400}?capacity:(\d+)|capacity:(\d+)[^}]{0,400}?slug:"([a-z0-9-]+)"')


def catalogue(page):
    """Les sessions annoncées, lues dans les liens de réservation."""
    sessions = {}
    for href in LIEN.findall(page):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(href.replace("&amp;", "&")).query)
        prendre = lambda k: v.propre(params.get(k, [""])[0])
        cle, titre = prendre("event"), prendre("title")
        if not cle or not titre or cle in sessions:
            continue
        sessions[cle] = {
            "titre": titre,
            "date": prendre("date").split("·")[0].strip(),
            "etiquette_date": prendre("date"),
            "lieu": prendre("venue").split(",")[0][:60],
            "url": SITE + href.replace("&amp;", "&"),
            "intervenant": " ".join(m.capitalize() for m in cle.split("-")[-2:]) or None,
        }
    return sessions


def secrets(page):
    """Clé d'accès et capacités, extraites du code du site."""
    cle, capacites = None, {}
    noms = BUNDLE.findall(page)
    for nom in noms[:3]:
        try:
            code = v.fetch(f"{SITE}/assets/{nom}", essais=2)
        except Exception:
            continue
        trouve = CLE.search(code)
        if trouve and not cle:
            cle = trouve.group(0)
        for s in SESSIONS.findall(code):
            fichier = s[0] or s[1]
            try:
                sess = v.fetch(f"{SITE}/assets/{fichier}", essais=2)
            except Exception:
                continue
            for a, b, c, d in CAPACITE.findall(sess):
                if a:
                    capacites[a] = int(b)
                elif d:
                    capacites[d] = int(c)
            break
    return cle, capacites


def inscrits(cle, etiquettes):
    if not cle or not etiquettes:
        return {}
    try:
        rep = json.loads(v.fetch(DISPOS, essais=2, donnees={"dates": etiquettes},
                                 entetes={"Authorization": f"Bearer {cle}"}))
    except Exception as erreur:
        print(f"Compteur d'inscrits indisponible ({type(erreur).__name__}).", file=sys.stderr)
        return {}
    return rep.get("counts", {}) if rep.get("success") else {}


MOIS = {m: i + 1 for i, m in enumerate(
    "janvier fevrier mars avril mai juin juillet aout septembre octobre "
    "novembre decembre".split())}


def date_du(texte):
    """« Mercredi 2 décembre 2026 » -> date, ou None si illisible."""
    if not texte:
        return None
    sans_accent = texte.lower()
    for a, b in zip("éèêëàâäûüôöç", "eeeeaaauuooc"):
        sans_accent = sans_accent.replace(a, b)
    m = re.search(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", sans_accent)
    if not m or m.group(2) not in MOIS:
        return None
    try:
        return datetime.date(int(m.group(3)), MOIS[m.group(2)], int(m.group(1)))
    except ValueError:
        return None


# La déduction du complet par disparition est suspendue : le catalogue lu ici
# ne voit que 7 des 11 sessions réellement annoncées, si bien que des sessions
# parfaitement réservables passeraient pour pleines. À rétablir une fois que le
# catalogue sera complet. Les absences continuent d'être comptées, sans effet.
DEDUIRE_COMPLET = False


def disparues(etat, sessions, quand):
    """Les complets d'Amio, déduits de la disparition du lien de réservation.

    Le badge COMPLET est posé par JavaScript, donc illisible ici, mais la
    session perd en même temps son lien de réservation et quitte le catalogue.
    Une session dont la date est encore à venir et qui manque à l'appel deux
    passages de suite est donc comptée comme complète. Deux passages, pour
    qu'un hoquet du site ne suffise pas ; et rien du tout si le catalogue est
    trop maigre, car ce serait alors le script qui aurait cessé de voir.
    """
    if len(sessions) < 3:
        return {}
    aujourdhui = datetime.date.fromisoformat(quand[:10])
    complets = {}
    for cle, fiche in etat.items():
        if cle in sessions:
            fiche["absences"] = 0
            continue
        jour = date_du(fiche.get("date"))
        # le jour même, une session disparaît parce qu'elle a lieu, pas parce qu'elle est pleine
        if fiche.get("complet") or not fiche.get("vu_le") or not jour or jour <= aujourdhui:
            continue
        fiche["absences"] = fiche.get("absences", 0) + 1
        if DEDUIRE_COMPLET and fiche["absences"] >= 2:
            fiche["complet"] = True
            fiche["complet_le"] = fiche.get("complet_le") or quand
            fiche["derniere_maj"] = quand
            v._noter(fiche, quand, "complet")
            complets[cle] = fiche
    return complets


def main():
    page = v.fetch(PAGE)
    sessions = catalogue(page)
    if not sessions:
        print("Aucune session trouvée : site en panne ou structure modifiée ?", file=sys.stderr)
        sys.exit(1)

    cle, capacites = secrets(page)
    compteur = inscrits(cle, [s["etiquette_date"] for s in sessions.values()])
    if not compteur:
        print("Pas de nombre d'inscrits ce passage : suivi limité au catalogue.", file=sys.stderr)

    for identifiant, s in sessions.items():
        n = compteur.get(s.pop("etiquette_date"))
        cap = capacites.get(identifiant)
        s["inscrits"] = n if isinstance(n, int) else None
        s["capacite"] = cap
        s["places"] = (cap - n) if (cap and isinstance(n, int)) else None
        # figurer au catalogue, c'est avoir un lien de réservation ouvert
        s["complet"] = (s["places"] <= 0) if s["places"] is not None else False

    etat = v.charger(ETAT)
    premier = etat is None
    etat = etat or {}
    nouveaux, rouverts, _ = v.fusionner(etat, sessions, premier_passage=premier)
    complets = {} if premier else disparues(etat, sessions, v.maintenant())
    v.sauver(ETAT, etat)

    if premier:
        print(f"Initialisation : {len(sessions)} sessions enregistrées, aucune alerte.")
        return
    v.alerter(ORGANISATEUR, PAGE, [
        ("JUST DROPPED", nouveaux, v.accord(len(nouveaux), "nouvelle session", "nouvelles sessions"), "beer"),
    ])
    connus = sum(1 for s in sessions.values() if s["inscrits"] is not None)
    print(v.resumer(ORGANISATEUR, etat, sessions)
          + f", {connus} avec compteur d'inscrits"
          + (f", {len(complets)} passés complets" if complets else ""))
    for f in complets.values():
        print("  complet :", v.decrire(f))
    for f in nouveaux.values():
        print("  nouveau :", v.decrire(f))


if __name__ == "__main__":
    main()
