#!/usr/bin/env python3
"""Rattrapage ponctuel : l'intervenant des talks enregistrés avant son suivi.

Les quatre veilles ne lisent le nom de l'intervenant que depuis peu. Les
fiches plus anciennes l'ont donc vide, et rien ne viendra le combler : leur
soirée est passée, elles ont disparu des sites et aucune veille ne les
rencontre plus. Ce script les remplit une fois pour toutes.

Il ne remplit que les champs vides. Il n'écrase jamais rien, et ne change
donc rien au remplissage automatique : pour les soirées encore en ligne, la
veille garde le dernier mot comme avant.

Les noms viennent de trois sources, toutes relevées à la main le 24/11/2026 :

- Amio : le fichier « assets/sessions-*.js » du site, qui porte pour chaque
  soirée, passée comme à venir, « speaker: {fr: "Avec Untel, sa qualité"} ».
  Les mêmes noms figurent sur amioclub.com/past-events.
- Le Briefing : la description de chaque page Eventbrite. Six des treize sont
  confirmés une seconde fois par les « syntheses » de lebriefing.co/api/content,
  qui donnent le même nom.
- Apéro Talk : la page Shotgun de la soirée.
- Causeries de Comptoir : interrogées ici en direct. Leur base publie
  « speaker_name », mais notre script ne demandait ce champ qu'à partir du
  16/11 : les fiches d'octobre ne l'ont jamais lu, alors qu'il existe.
"""
import json
import os
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(RACINE, "scripts"))
os.chdir(RACINE)

import check_causeries as causeries
import veille as v   # pour écrire les états dans leur format habituel

# Relevés à la main, par clé de fiche.
NOMS = {
    "data/amio_seen.json": {
        "cold-cases-antoine-sauvestre-vinci": "Antoine Sauvestre-Vinci",
        "criminalite-jerome-barlatier": "Jérôme Barlatier",
        "criminologie-elodie-eliot": "Elodie Eliot",
        "espion-olivier-mas": "Olivier Mas",
        "geopolitique-bruno-tertrais": "Bruno Tertrais",
        "gizeh-patrice-pouillard": "Patrice Pouillard",
        "jeux-video-didier-mennecier": "Didier Mennecier",
        "koh-lanta-sophia-mansouri": "Sophia Mansouri",
        "presidentielle-richard-flurin": "Richard Flurin",
        "prison-yannick-deslandes": "Yannick Deslandes",
        "sexologue-catherine-blanc": "Catherine Blanc",
    },
    "data/lebriefing_seen.json": {
        "abysses-que-renferme-le-fond-des-oceans": "Françoise Gaill",
        "comment-repondre-quand-on-se-moque-de-nous": "Emmanuelle Piquet",
        "comment-s-affirmer-au-travail": "Émilie Berthet",
        "dating-how-to-get-away-with-dating-in-paris": "Christina Moeller",
        "expositions-universelles-a-quoi-paris-aurait-il-pu-ressembler": "Sophie Gacheny",
        "paris-comment-s-est-elle-batie-comment-va-t-elle-evoluer": "Simon Texier",
        "peut-on-apprendre-la-philosophie-en-regardant-netflix": "Gilles Vervisch",
        "peut-on-choisir-de-qui-l-on-tombe-amoureux": "Aurore Malet-Karas",
        "peut-on-ficher-toute-une-population": "Nicolas Belorgey",
        "photographe-de-l-extreme-jusqu-ou-aller-pour-une-image": "Maxime Mergalet",
        "pourquoi-tout-le-monde-se-met-a-courir": "Rafael Janosevic",
        "quel-est-le-futur-de-l-education": "François Taddei",
        "sommes-nous-seuls-dans-l-univers": "Jacques Arnould",
    },
    "data/aperotalk_seen.json": {
        "la-france-est-elle-prete-pour-la-guerre": "Robin Gastaldi",
    },
}

ETAT_CAUSERIES = "data/causeries_seen.json"


def charger(chemin):
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def remplir(etat, noms):
    """Pose les noms sur les fiches vides. Renvoie (remplies, deja, inconnues)."""
    remplies, deja, inconnues = [], [], []
    for cle, nom in sorted(noms.items()):
        fiche = etat.get(cle)
        if fiche is None:
            inconnues.append(cle)
        elif fiche.get("intervenant"):
            deja.append((cle, fiche["intervenant"]))
        else:
            fiche["intervenant"] = nom
            remplies.append((cle, nom))
    return remplies, deja, inconnues


def noms_des_causeries(etat):
    """Demande à la base des Causeries le nom des fiches restées vides."""
    vides = [c for c, f in etat.items() if not f.get("intervenant")]
    if not vides:
        return {}
    url = (f"{causeries.API}?select=id,speaker_name,speaker_title"
           f"&id=in.({','.join(vides)})")
    trouves = {}
    for ligne in causeries.interroger(url):
        nom = (ligne.get("speaker_name") or "").strip()
        if nom:
            trouves[ligne["id"]] = nom
    return trouves


def main():
    total = 0
    for chemin, noms in NOMS.items():
        etat = charger(chemin)
        remplies, deja, inconnues = remplir(etat, noms)
        if remplies:
            v.sauver(chemin, etat)
        total += len(remplies)
        print(f"\n{chemin} : {len(remplies)} remplies")
        for cle, nom in remplies:
            print(f"  + {nom}  ({cle})")
        for cle, nom in deja:
            print(f"  = deja renseigne, laisse tel quel : {nom}  ({cle})")
        for cle in inconnues:
            print(f"  ? clé absente de l'état : {cle}", file=sys.stderr)

    etat = charger(ETAT_CAUSERIES)
    avant = [c for c, f in etat.items() if not f.get("intervenant")]
    try:
        trouves = noms_des_causeries(etat)
    except Exception as erreur:
        # hors de la forge, cette base n'est pas joignable : on le dit, et le
        # reste du rattrapage tient quand même
        print(f"Base des Causeries injoignable ({type(erreur).__name__}) : "
              f"{len(avant)} fiches laissées indéterminées.", file=sys.stderr)
        trouves = {}
    remplies, _, _ = remplir(etat, trouves)
    if remplies:
        v.sauver(ETAT_CAUSERIES, etat)
    total += len(remplies)
    print(f"\n{ETAT_CAUSERIES} : {len(remplies)} remplies sur {len(avant)} vides")
    for cle, nom in remplies:
        print(f"  + {nom}  ({cle})")
    for cle in avant:
        if not etat[cle].get("intervenant"):
            print(f"  - toujours indéterminé : {etat[cle].get('titre')}")

    print(f"\nTotal : {total} intervenants rattrapés.")


if __name__ == "__main__":
    main()
