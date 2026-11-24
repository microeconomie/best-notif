# best-notif

Veille horaire de quatre organisateurs de talks parisiens (Apéro Talk, Le
Briefing, Amio, Causeries de Comptoir). Chaque veille lit le site, met à jour
son fichier d'état, alerte par issue GitHub, puis reconstruit la base.

## Ce qui est écrit à la main et ce qui est dérivé

- `scripts/` et `.github/workflows/` : le code.
- `scripts/ponctuel/` : des rattrapages passés, gardés comme trace de ce qui a
  été corrigé à la main et de la source de chaque valeur. Jamais appelés par
  une veille, et à ne pas relancer sans raison.
- `data/<organisateur>_seen.json` : l'état de chaque veille, écrit par elle
  seule, une fiche par talk. Jamais purgé : un talk reste dans la base après
  sa soirée, même quand il disparaît du site.
- `data/talks.json` et `data/talks.csv` : **dérivés**, reconstruits par
  `scripts/construire_base.py` à chaque passage de chaque veille.

## Le site

`index.html` à la racine est le baromètre public, servi par GitHub Pages. Il lit
`data/talks.json` à côté de lui, donc il est à jour dès qu'une veille a commité,
sans intervention. Toute sa logique de rendu tient dans ce seul fichier ;
`assets/d3.min.js` est embarqué pour ne dépendre d'aucun service extérieur.

## Règle pour les pull requests

**Ne pas inclure `data/talks.json` ni `data/talks.csv` dans une pull
request.** Les quatre veilles les régénèrent et les poussent sur `main`
plusieurs fois par jour : toute branche qui y touche entre en conflit en
quelques heures. Laisser la première veille qui passe les reconstruire après
la fusion. Si une PR les contient déjà et qu'un conflit survient, le résoudre
en relançant `construire_base.py` sur les états fusionnés, jamais en
choisissant une version à la main.

Les fichiers d'état, eux, peuvent figurer dans une PR : ils fusionnent sans
heurt, et certaines corrections rétroactives passent par là.

## Deux règles de mesure à ne pas affaiblir

- **Une date ne compte que si elle est connue à 48 heures près.** Chaque fiche
  retient sa dernière observation « disponible » et l'écart avec la bascule.
  Au-delà, la date reste affichée mais ne nourrit aucun calcul.
- **La date d'apparition est celle de notre première détection**, seule trace
  de l'ouverture des réservations. Aucun organisateur ne publie la sienne ;
  celle qu'exposent les Causeries est la création du brouillon, de 4 à 49
  jours trop tôt.
