# Feuille de route

**[🇬🇧 English version](ROADMAP.md)**

Idées et améliorations à l'étude, venues pour la plupart des retours d'utilisateurs. Sans date ni promesse — c'est un projet de loisir, entretenu sur le temps libre. Si l'un de ces points compte pour vous, ou si vous avez une autre suggestion, [ouvrez une issue](https://github.com/bebkill/Eclipse-Cleaner/issues) : c'est le meilleur moyen de faire remonter un sujet.

## Types d'éclipse

**Livré.** De nombreuses demandes de traitement d'éclipses lunaires — et un rapport de plantage sur l'une d'elles — ont mené aux presets de type d'éclipse : `sun`, `moon`, `planetary` et `custom`, détectés automatiquement depuis la vidéo source et modifiables via `--preset` ou le sélecteur du panneau Source du viewer. Voir [Types d'éclipse et presets](README.fr.md#types-déclipse-et-presets) pour les tables de paramètres.

- **Des images réelles de transit planétaire, pour la calibration.** Le preset `planetary` n'a été validé que sur des frames synthétiques, jamais sur une vidéo réelle. Si vous avez des images d'un transit planétaire (ou de tout petit disque uniformément brillant sur fond noir) que l'outil traite mal, [ouvrez une issue](https://github.com/bebkill/Eclipse-Cleaner/issues) avec un échantillon : c'est le moyen le plus rapide de le régler.

## Formats d'entrée

- **AVI brut Bayer (caméras planétaires).** Un AVI non dématricé — par exemple sorti tel quel d'un logiciel de capture planétaire — ressort aujourd'hui avec une « pixellisation » en damier : les images sont décodées telles quelles et la matrice de Bayer n'est jamais interprétée. À l'étude : reconnaître (ou laisser l'utilisateur préciser) le motif de Bayer et dématricer les images à l'extraction. *(Signalé par un utilisateur, août 2026.)*
- **Entrée SER.** Le format de capture natif de SharpCap et de la plupart des outils d'astronomie planétaire, et un conteneur courant pour les petites vidéos solaires. *(Suggéré par le même utilisateur.)*

## Formats de sortie

- **Choix du conteneur de sortie : MP4, AVI ou SER.** Aujourd'hui la sortie est toujours un MP4 écrit à côté de la source (plus, en option, une séquence PNG numérotée).

## Viewer

- **Recadrage dynamique (ROI). Partiellement livré.** La *taille* de la fenêtre de recadrage se pilote désormais à la souris dans le viewer — poignée sur le cadre posé sur la vignette centrale, ou retour au recommandé — et sert exactement comme le ferait `--taille`, sans toucher à la ligne de commande. Sa *position* reste volontairement automatique : la fenêtre suit le disque repéré, et ce suivi est la raison d'être du pipeline ; il n'y a donc pas de contrôle de position manuel, et aucun n'est prévu. La *rotation manuelle* n'a pas non plus été reprise, pour la même raison : rien dans les vidéos traitées jusqu'ici n'a exigé un cadrage incliné. Les deux restent ouverts à une nouvelle issue si un cas réel se présente. Ce travail s'appuie sur [le fork de @mireianievas](https://github.com/mireianievas/Eclipse-Cleaner), un prototype fonctionnel de recadrage entièrement manuel (position, taille et rotation) qui en a montré le premier le besoin — voir [#1](https://github.com/bebkill/Eclipse-Cleaner/issues/1). Son fork a aussi diagnostiqué et corrigé le plantage de la boîte de sélection de fichier sous macOS ; ce correctif est intégré (co-auteure).

## Ligne de commande

- **Messages CLI en anglais.** Le viewer est entièrement bilingue, mais la ligne de commande ne parle encore que français. Contribution bienvenue.
