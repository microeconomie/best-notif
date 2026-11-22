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
    """La date de l'événement, au format ISO.

    Apéro Talk écrit ses dates sans millésime (« Lundi 23 novembre »). Un
    talk se tenant toujours après sa mise en ligne, l'année se déduit de la
    date où la veille l'a repéré : la même, ou la suivante si le jour et le
    mois sont déjà passés à ce moment-là.
    """
    if fiche.get("date_iso"):
        return fiche["date_iso"]
    texte = (fiche.get("date") or "").lower()
    m = re.search(r"(\d{1,2})(?:er)?\s+([a-zéû]+)\s+(\d{4})", texte)
    if m and m.group(2) in MOIS:
        return f"{m.group(3)}-{MOIS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{1,2})(?:er)?\s+([a-zéû]+)", texte)
    if not m or m.group(2) not in MOIS:
        return ""
    jour, mois = int(m.group(1)), MOIS[m.group(2)]
    repere = (fiche.get("vu_le") or fiche.get("derniere_maj") or "")[:10]
    if not repere:
        return ""
    an = int(repere[:4])
    if (mois, jour) < (int(repere[5:7]), int(repere[8:10])):
        an += 1
    return f"{an}-{mois:02d}-{jour:02d}"


MARGE_MAX_H = 48      # au-delà, la date de complet est tenue pour indéterminée


def statut(date_ev, fiche):
    """L'etat du talk, en tenant compte du fait qu'il ait eu lieu ou non.

    Un talk complet une fois l'est definitivement ici. Les places qui se
    liberent ensuite, apres une annulation, declenchent bien une alerte par
    mail, mais elles ne doivent pas effacer le fait que la soiree a fait le
    plein : c'est cela que le barometre mesure.

    Une fois la date passee, « disponible » n'a plus de sens : ce qui compte
    alors est que la soiree ait fait le plein ou non.
    """
    if fiche.get("complet") or fiche.get("complet_le"):
        return "complet"
    if fiche.get("complet") is None:
        return "indéterminé"
    passe = bool(date_ev) and date_ev < datetime.now().date().isoformat()
    return "jamais complet" if passe else "disponible"


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
            # La date qui fait foi est celle à partir de laquelle la réservation
            # est ouverte, c'est-à-dire le moment où le talk apparaît publiquement.
            # Aucun organisateur ne la publie : celle que les Causeries exposent
            # (created_at) est la création du brouillon dans leur base, de 4 à 49
            # jours avant la mise en vente. Seule notre propre première détection
            # témoigne de l'ouverture des réservations, à quelques heures près.
            parution = f.get("vu_le") or ""
            # un délai ne vaut que si ses deux bouts tiennent à 48 h près
            marge = f.get("complet_marge_h")
            complet_sur = bool(f.get("complet_le")) and marge is not None and marge <= MARGE_MAX_H
            marge_p = f.get("parution_marge_h")
            parution_sure = bool(parution) and marge_p is not None and marge_p <= MARGE_MAX_H
            fiable = complet_sur and parution_sure
            date_ev = date_iso(f)
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
                "date_event": date_ev,
                "lieu": f.get("lieu") or f.get("venue") or "",
                "parution": parution[:16],
                "creation_en_base": (f.get("publie_le") or "")[:16],
                "parution_marge_h": marge_p if marge_p is not None else "",
                "parution_date_sure": "oui" if parution_sure else "non",
                "complet_le": (f.get("complet_le") or "")[:16],
                "complet_marge_h": marge if marge is not None else "",
                "complet_date_sure": "oui" if complet_sur else ("non" if f.get("complet_le") else ""),
                "jours_avant_complet": delai(parution, f.get("complet_le")) if fiable else "",
                "rouvert_le": (f.get("rouvert_le") or "")[:16],
                "capacite": capacite if capacite is not None else "",
                "inscrits": f.get("inscrits") if f.get("inscrits") is not None else "",
                "places_restantes": restantes if restantes is not None else "",
                "remplissage_pct": (round(100 * (capacite - restantes) / capacite)
                                    if capacite and restantes is not None else ""),
                "complet": {True: "oui", False: "non"}.get(f.get("complet"), "inconnu"),
                "statut": statut(date_ev, f),
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
    flous = sum(1 for l in lignes if l["complet_date_sure"] == "non")
    print(f"Base reconstruite : {len(lignes)} talks, {len(mesures)} délais mesurés"
          + (f", {flous} complets de date indéterminée." if flous else "."))
    if mesures:
        mesures.sort()
        milieu = mesures[len(mesures) // 2]
        print(f"  délai médian : {milieu} jours (de {mesures[0]} à {mesures[-1]})")


if __name__ == "__main__":
    main()
