#!/usr/bin/env python3
"""Construit la base consolidée des talks à partir des quatre fichiers d'état.

Produit data/talks.csv, qui s'ouvre directement dans Excel, et data/talks.json
pour le tableau de bord. Une ligne par talk, avec sa thématique, sa date de
parution, sa date de remplissage et le délai entre les deux.

Ce script ne va sur aucun site : il ne fait que relire ce que les veilles ont
mémorisé.
"""
import csv
import json
import os
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import veille as v

SOURCES = {
    "Apéro Talk": "data/aperotalk_seen.json",
    "Le Briefing": "data/lebriefing_seen.json",
    "Amio": "data/amio_seen.json",
    "Causeries de Comptoir": "data/causeries_seen.json",
}

# Classement par mots-clés, du plus spécifique au plus général.
THEMES = [
    ("Crime & justice", "crime|criminal|prison|carcér|justice|police|commissariat|meurtr|cold case|gang|légiste|peine|avocat|marabout|passage à l'acte|fich|epstein"),
    ("Géopolitique & défense", "géopolit|guerre|moyen-orient|conflictualité|nucléaire|dgse|espion|otan|monde en 2030|chaos|gign|bataclan|raid"),
    ("IA & technologie", r"\bia\b|intelligence artificielle|technolog|écrans|tiktok|jeux vidéo|algorithm|numérique|internet|médium|doublage"),
    ("Sciences & espace", "univers|trou noir|espace|nasa|mars|astronom|océan|abysse|fonds marins|éruption|solaire|extraterrestre|pyramide|gizeh|physique|neuroscience"),
    ("Santé & corps", "santé|addiction|respirer|longtemps en bonne santé|sommeil|dormir|neurolog|anxiété|cerveau|médecin|corps|sexolog"),
    ("Amour & intime", "amour|amoureux|couple|intime|désir|dating|fantasme|astres|étoiles|astrologie"),
    ("Psychologie & soi", "psycholog|mental|émotion|doute|volonté|manipul|rêve|limites|parole en public|s'affirmer|juge-t-on|compétents|rituel|destin|seul|stress"),
    ("Société & politique", "politique|opinion publique|présidentiel|démocrat|société|masculinist|population|éducation|école|enfants|inacceptable|moque"),
    ("Argent & travail", "argent|épargne|investir|banque|finance|travail|startup|carrière|entreprise|capitalisme|écono|jobs|mode"),
    ("Environnement", "effondrement|manquer d'eau|climat|environnement|ressource|bovine"),
    ("Culture & histoire", "histoire|moyen âge|camus|philosoph|netflix|cinéma|musique|classique|pokémon|exposition|paris|koh-lanta|série|photograph|courir|secte|gourou|religion"),
]

MOIS = {m: i + 1 for i, m in enumerate(
    "janvier février mars avril mai juin juillet août septembre octobre novembre décembre".split())}


def theme(texte):
    t = unicodedata.normalize("NFC", (texte or "").lower())
    for nom, motifs in THEMES:
        if re.search(motifs, t):
            return nom
    return "Autre"


def date_iso(fiche):
    if fiche.get("date_iso"):
        return fiche["date_iso"]
    m = re.search(r"(\d{1,2})\s+([a-zéû]+)\s+(\d{4})", (fiche.get("date") or "").lower())
    if not m or m.group(2) not in MOIS:
        return ""
    return f"{m.group(3)}-{MOIS[m.group(2)]:02d}-{int(m.group(1)):02d}"


def delai(depart, arrivee):
    if not depart or not arrivee:
        return ""
    try:
        d1, d2 = datetime.fromisoformat(depart[:16]), datetime.fromisoformat(arrivee[:16])
    except ValueError:
        return ""
    return round((d2 - d1).total_seconds() / 86400, 1)


def main():
    lignes = []
    for organisateur, chemin in SOURCES.items():
        etat = v.charger(chemin) or {}
        for cle, f in etat.items():
            parution = f.get("publie_le") or f.get("vu_le") or ""
            capacite = f.get("capacite")
            restantes = f.get("places")
            if restantes is None and capacite and isinstance(f.get("inscrits"), int):
                restantes = capacite - f["inscrits"]
            lignes.append({
                "id": f"{v.slug(organisateur)}--{cle}"[:80],
                "organisateur": organisateur,
                "titre": f.get("titre") or f.get("title") or cle,
                "intervenant": f.get("intervenant") or "",
                "theme": theme(f"{f.get('titre') or f.get('title') or cle} {f.get('specialite') or ''} {cle.replace('-', ' ')}"),
                "date_event": date_iso(f),
                "lieu": f.get("lieu") or f.get("venue") or "",
                "parution": parution[:16],
                "parution_exacte": "oui" if f.get("publie_le") else "non",
                "complet_le": (f.get("complet_le") or "")[:16],
                "jours_avant_complet": delai(parution, f.get("complet_le")),
                "rouvert_le": (f.get("rouvert_le") or "")[:16],
                "capacite": capacite if capacite is not None else "",
                "inscrits": f.get("inscrits") if f.get("inscrits") is not None else "",
                "places_restantes": restantes if restantes is not None else "",
                "remplissage_pct": (round(100 * (capacite - restantes) / capacite)
                                    if capacite and restantes is not None else ""),
                "complet": {True: "oui", False: "non"}.get(f.get("complet"), "inconnu"),
                "url": f.get("url", ""),
                "points_de_mesure": len(f.get("courbe") or []),
            })

    lignes.sort(key=lambda l: (l["organisateur"], l["date_event"] or "9999"))
    colonnes = list(lignes[0].keys()) if lignes else []
    Path("data").mkdir(exist_ok=True)
    with open("data/talks.csv", "w", newline="", encoding="utf-8-sig") as fh:
        ecrivain = csv.DictWriter(fh, fieldnames=colonnes, delimiter=";")
        ecrivain.writeheader()
        ecrivain.writerows(lignes)
    Path("data/talks.json").write_text(
        json.dumps(lignes, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")

    mesures = [l["jours_avant_complet"] for l in lignes if l["jours_avant_complet"] != ""]
    print(f"Base reconstruite : {len(lignes)} talks, {len(mesures)} délais mesurés.")
    if mesures:
        mesures.sort()
        milieu = mesures[len(mesures) // 2]
        print(f"  délai médian : {milieu} jours (de {mesures[0]} à {mesures[-1]})")


if __name__ == "__main__":
    main()
