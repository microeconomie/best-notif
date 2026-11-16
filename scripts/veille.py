#!/usr/bin/env python3
"""Socle commun aux quatre veilles.

Chaque veille fournit un dictionnaire d'événements ; ce module se charge du
reste : mémoriser ce qu'on a déjà vu, dater les changements, et prévenir.

Ce qu'une fiche mémorise, au-delà du descriptif de l'événement :
  vu_le        première fois que la veille l'a vu  (la « parution »)
  complet_le   première fois qu'il est apparu complet
  rouvert_le   dernière remise en vente constatée
  journal      liste horodatée des changements d'état
  courbe       relevés quotidiens du nombre d'inscrits, là où le site le publie

Étiquettes des alertes :
  JUST DROPPED  un événement apparaît
  BACK ON SALE  un événement complet redevient réservable
  LAST CHANCE   il ne reste que quelques places
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0 Safari/537.36")


# ---------------------------------------------------------------- réseau

def fetch(url, essais=3, entetes=None, donnees=None, delai=30):
    """Télécharge une page ou appelle une API, avec plusieurs tentatives.

    Une coupure passagère ne doit pas faire échouer la veille. En revanche une
    réponse d'erreur du serveur remonte tout de suite : la réessayer ne
    servirait à rien.
    """
    attente = 10
    for tentative in range(1, essais + 1):
        tetes = {"User-Agent": NAVIGATEUR, "Accept-Language": "fr-FR,fr;q=0.9"}
        tetes.update(entetes or {})
        corps = json.dumps(donnees).encode() if donnees is not None else None
        if corps is not None:
            tetes.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=corps, headers=tetes)
        try:
            with urllib.request.urlopen(req, timeout=delai) as rep:
                return rep.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError:
            raise
        except Exception as erreur:
            if tentative == essais:
                raise
            print(f"Tentative {tentative}/{essais} échouée ({type(erreur).__name__}), "
                  f"nouvel essai dans {attente}s.", file=sys.stderr)
            time.sleep(attente)
            attente *= 3


def fetch_json(url, **kw):
    return json.loads(fetch(url, **kw))


# ---------------------------------------------------------------- texte

def propre(fragment):
    fragment = re.sub(r"<[^>]+>", " ", fragment or "")
    fragment = unicodedata.normalize("NFC", fragment)
    from html import unescape
    return re.sub(r"\s+", " ", unescape(fragment).replace(" ", " ")).strip()


def slug(valeur):
    valeur = unicodedata.normalize("NFKD", (valeur or "").lower())
    valeur = "".join(c for c in valeur if not unicodedata.combining(c))
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", valeur)).strip("-")


def maintenant():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")


# ---------------------------------------------------------------- état

def charger(chemin):
    p = Path(chemin)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def sauver(chemin, etat):
    p = Path(chemin)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(etat, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")


def _noter(fiche, quand, quoi):
    fiche.setdefault("journal", []).append([quand, quoi])


def marge_h(dispo_le, complet_le):
    """Heures d'incertitude sur la date de complet, ou None si indéterminable.

    On ne sait pas quand un talk est tombé : on sait seulement qu'il était
    encore ouvert à la dernière observation, et complet à celle-ci. L'écart
    entre les deux est la marge d'erreur ; sans observation « disponible »
    antérieure, le talk était déjà complet quand la veille l'a découvert et
    la date reste indéterminée.
    """
    if not dispo_le or not complet_le:
        return None
    try:
        d1 = datetime.fromisoformat(dispo_le[:16])
        d2 = datetime.fromisoformat(complet_le[:16])
    except ValueError:
        return None
    return round((d2 - d1).total_seconds() / 3600, 1)


def fusionner(etat, trouves, seuil_places=None, premier_passage=False):
    """Met l'état à jour et renvoie ce qui mérite une alerte.

    Renvoie (nouveaux, rouverts, derniers) : trois dictionnaires de fiches.
    Au premier passage, tout est enregistré sans qu'aucune alerte ne parte.
    """
    quand = maintenant()
    jour = quand[:10]
    nouveaux, rouverts, derniers = {}, {}, {}

    for cle, vu in trouves.items():
        fiche = etat.get(cle)
        complet_vu = vu.get("complet")
        if fiche is None:
            fiche = dict(vu)
            fiche["vu_le"] = quand
            fiche["journal"] = [[quand, "drop"]]
            fiche["complet_le"] = quand if complet_vu else None
            fiche["dispo_le"] = None
            fiche["complet_marge_h"] = None     # déjà complet à la découverte
            if complet_vu:
                _noter(fiche, quand, "complet")
            etat[cle] = fiche
            if not premier_passage:
                nouveaux[cle] = fiche
        else:
            if not fiche.get("vu_le"):          # fiche d'avant l'horodatage
                fiche["vu_le"] = quand
                _noter(fiche, quand, "reprise")
            etait_complet = bool(fiche.get("complet"))
            etait_sous_seuil = bool(fiche.get("alerte_seuil"))
            # la capacité, une fois connue, ne se perd pas
            capacite = vu.get("capacite") or fiche.get("capacite")
            fiche.update({k: v for k, v in vu.items() if v is not None or k not in fiche})
            fiche["capacite"] = capacite
            if complet_vu and not etait_complet:
                fiche["complet_le"] = fiche.get("complet_le") or quand
                if fiche.get("complet_marge_h") is None:
                    fiche["complet_marge_h"] = marge_h(fiche.get("dispo_le"), quand)
                _noter(fiche, quand, "complet")
            elif etait_complet and complet_vu is False:
                fiche["rouvert_le"] = quand
                _noter(fiche, quand, "revente")
                if not premier_passage:
                    rouverts[cle] = fiche

        # courbe de remplissage, un point par jour au plus
        inscrits = fiche.get("inscrits")
        if inscrits is None and fiche.get("places") is not None and fiche.get("capacite"):
            inscrits = fiche["capacite"] - fiche["places"]
        if isinstance(inscrits, int):
            courbe = fiche.setdefault("courbe", [])
            if not courbe or courbe[-1][0] != jour:
                courbe.append([jour, inscrits])
            else:
                courbe[-1][1] = inscrits

        # dernières places
        if seuil_places is not None:
            restantes = fiche.get("places")
            if restantes is None and fiche.get("capacite") and isinstance(inscrits, int):
                restantes = fiche["capacite"] - inscrits
            sous_seuil = isinstance(restantes, int) and 0 < restantes <= seuil_places
            if sous_seuil and not fiche.get("alerte_seuil"):
                _noter(fiche, quand, f"dernieres-places:{restantes}")
                if not premier_passage:
                    derniers[cle] = fiche
            fiche["alerte_seuil"] = sous_seuil

        # dernière observation où le talk était encore ouvert
        if complet_vu is False:
            fiche["dispo_le"] = quand
        fiche["derniere_maj"] = quand

    return nouveaux, rouverts, derniers


# ---------------------------------------------------------------- alertes

def accord(n, singulier, pluriel):
    return f"{n} {singulier if n == 1 else pluriel}"


def decrire(fiche):
    detail = ", ".join(p for p in (fiche.get("date"), fiche.get("lieu")) if p)
    etat = ""
    places = fiche.get("places")
    if places is None and fiche.get("capacite") and isinstance(fiche.get("inscrits"), int):
        places = fiche["capacite"] - fiche["inscrits"]
    if fiche.get("complet"):
        etat = " [complet]"
    elif isinstance(places, int):
        etat = f" [{accord(places, 'place restante', 'places restantes')}]"
    qui = f" · {fiche['intervenant']}" if fiche.get("intervenant") else ""
    return f"{fiche['titre']}{qui} ({detail}){etat}" if detail else f"{fiche['titre']}{qui}{etat}"


def _issue(titre, corps):
    jeton, depot = os.environ.get("GH_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not jeton or not depot:
        print(f"Pas de contexte GitHub, issue non créée : {titre}")
        return
    charge = {"title": titre, "body": corps}
    proprio = os.environ.get("GITHUB_REPOSITORY_OWNER")
    if proprio:
        charge["assignees"] = [proprio]
    tetes = {"Authorization": f"Bearer {jeton}", "Accept": "application/vnd.github+json"}
    try:
        fetch(f"https://api.github.com/repos/{depot}/issues", essais=1, entetes=tetes, donnees=charge)
    except Exception:
        charge.pop("assignees", None)
        fetch(f"https://api.github.com/repos/{depot}/issues", essais=1, entetes=tetes, donnees=charge)
    print(f"Issue GitHub créée : {titre}")


def _ntfy(titre, fiches, tag):
    sujet = os.environ.get("NTFY_TOPIC")
    if not sujet:
        return
    for fiche in fiches.values():
        try:
            fetch("https://ntfy.sh", essais=1, donnees={
                "topic": sujet, "title": titre, "message": decrire(fiche),
                "click": fiche.get("url", ""), "tags": [tag],
            })
        except Exception as erreur:
            print(f"Notification ntfy échouée ({type(erreur).__name__}).", file=sys.stderr)


def alerter(organisateur, source, lots):
    """Un mail par catégorie, l'étiquette toujours en tête de l'objet.

    `lots` est une liste de (etiquette, fiches, resume_pluriel, tag_ntfy).
    """
    for etiquette, fiches, resume, tag in lots:
        if not fiches:
            continue
        if len(fiches) == 1:
            fiche = next(iter(fiches.values()))
            detail = fiche["titre"]
            if len(detail) > 70:
                detail = detail[:69].rstrip() + "…"
            if etiquette == "LAST CHANCE":
                places = fiche.get("places")
                if places is None and fiche.get("capacite") and isinstance(fiche.get("inscrits"), int):
                    places = fiche["capacite"] - fiche["inscrits"]
                if isinstance(places, int):
                    detail += f" ({accord(places, 'place', 'places')})"
            elif fiche.get("billetterie"):
                detail += f" [{fiche['billetterie']}]"
        else:
            detail = resume
        lignes = []
        for fiche in fiches.values():
            marque = f" [{fiche['billetterie']}]" if fiche.get("billetterie") else ""
            lignes.append(f"- **{decrire(fiche)}**{marque} : {fiche.get('url', '')}")
        _issue(f"{etiquette} {organisateur} : {detail}",
               f"Détecté sur {source}\n\n" + "\n".join(lignes))
        _ntfy(f"{etiquette} {organisateur}", fiches, tag)


def resumer(organisateur, etat, trouves, total=None):
    complets = sum(1 for f in trouves.values() if f.get("complet"))
    bouts = [f"{len(trouves)} suivis", f"{complets} complets"]
    if total is not None:
        bouts.append(f"{total} au total sur la page")
    return f"{organisateur} : " + ", ".join(bouts)
