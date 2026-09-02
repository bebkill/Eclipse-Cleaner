# Eclipse Cleaner

*Nettoyer et stabiliser un timelapse d'éclipse solaire instable — et peut-être sauver une vidéo que vous pensiez perdue.*

**[🇬🇧 English version](README.md)**

[![CI](https://github.com/bebkill/Eclipse-Cleaner/actions/workflows/ci.yml/badge.svg)](https://github.com/bebkill/Eclipse-Cleaner/actions/workflows/ci.yml)
[![Licence : MIT](https://img.shields.io/badge/Licence-MIT-green.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-soutenir-yellow?logo=buymeacoffee&logoColor=white)](https://www.buymeacoffee.com/bebkill)

> 🔰 **Jamais utilisé Python ni un terminal ?** Suivez le **[guide pour vrais débutants (Windows)](BEGINNERS.fr.md)** — uniquement du copier-coller, aucune expérience requise, aucun code à écrire.

## L'histoire de ce projet

Ce projet a commencé avec la capture de l'éclipse solaire du **12 août 2026**. L'installation de mon matériel n'était pas idéale — des personnes passaient régulièrement devant — mais j'ai quand même pu capturer l'éclipse. Puis j'ai découvert la vidéo produite par mon smart-télescope, et j'étais dégoûté : c'était inutilisable. Très instable, avec beaucoup de séquences masquées suivies de phases de récupération du tracking et d'ajustements brutaux de luminosité.

J'ai essayé de corriger avec les outils disponibles (SIRIL et PIPP), mais je n'ai sans doute pas su y faire — ou c'était tout simplement trop leur demander. Je me suis donc lancé dans la conception d'un outil, avec l'aide de mon assistant IA préféré. Le voici.

## Avant / après

| Originale (sortie brute du smart-télescope) | Nettoyée avec Eclipse Cleaner |
| :---: | :---: |
| ![Vidéo originale, instable](docs/assets/before.gif) | ![Vidéo nettoyée, stabilisée](docs/assets/after.gif) |

*Les deux extraits sont accélérés 6×.*

## Ce que fait le programme

- **S'adapte à ce qui s'éclipse réellement** — le Soleil, la Lune ou une planète échouent chacun différemment, donc un preset de type d'éclipse choisit la bonne stratégie de mesure et les bons seuils de tri par défaut pour chacun. Le type est détecté automatiquement et toujours modifiable. Voir [Types d'éclipse et presets](#types-déclipse-et-presets).
- **Trie les frames** — seules les frames irréparables sont rejetées : flou, obscurité, éblouissement, échec de localisation. Une frame décadrée n'est *pas* une frame défectueuse ; le stabilisateur corrige la position.
- **Verrouille le disque solaire au centre** — un vote de Hough dirigé à rayon fixe trouve le disque même sur un croissant fin, et tolère qu'un nuage masque une partie du limbe.
- **Déplace le cadre comme le ferait un cadreur** — la fenêtre de recadrage est planifiée, butée en douceur contre les bords de la source, et absorbe les sauts de ré-acquisition du tracking (des centaines de pixels en une frame) au lieu de saccader.
- **Supprime le scintillement de l'exposition automatique** — le niveau est corrigé image par image vers la médiane de la séquence, et la balance des blancs est stabilisée vers sa *propre* trajectoire, jamais vers le neutre : un filtre solaire rouge reste rouge, un coucher de soleil reste chaud, et son retrait en cours de séquence reste un vrai changement. (`--sans-couleur` désactive la partie couleur.)
- **Comble les coupes courtes** par interpolation linéaire, et n'invente jamais d'images pour les longues : un vrai trou reste une coupe franche.
- **Un viewer de revue dans le navigateur** — inspecter chaque frame, corriger le tri automatique d'une touche, puis relancer le rendu. Bilingue (français/anglais), servi sur 127.0.0.1 uniquement. Il affiche un état de chargement pendant la sonde d'une vidéo tout juste choisie, et propose un lecteur repliable pour visionner la source brute.

Tout s'adapte à votre vidéo : résolution, format, cadence et rayon apparent du Soleil sont mesurés, pas supposés, et chaque seuil peut être ajusté en ligne de commande.

## Installation

**Sous Windows, Python n'est même pas nécessaire** : chaque release fournit un exécutable autonome `eclipse-cleaner-…-windows-x64.exe` — téléchargez-le depuis la [dernière release](https://github.com/bebkill/Eclipse-Cleaner/releases/latest) et double-cliquez. Détails (et avertissement SmartScreen à prévoir) dans [le guide débutants](BEGINNERS.fr.md#le-raccourci--un-programme-windows-tout-prêt-sans-python-du-tout).

Sinon : nécessite **Python 3.12+**. Le binaire ffmpeg est fourni par `imageio-ffmpeg` : rien d'autre à installer sur le système.

```bash
python -m pip install git+https://github.com/bebkill/Eclipse-Cleaner.git
```

Cette forme `git+` nécessite que [git](https://git-scm.com/) soit installé (ce n'est généralement pas le cas sous Windows). Pas de git ? Utilisez la forme zip — même résultat :

```bash
python -m pip install https://github.com/bebkill/Eclipse-Cleaner/archive/refs/heads/main.zip
```

Ou depuis un clone :

```bash
git clone https://github.com/bebkill/Eclipse-Cleaner.git
cd Eclipse-Cleaner
python -m pip install .
```

⚠️ N'oubliez pas le point final de `pip install .` — il signifie « installer depuis ce répertoire ».

Une fois installé, lancez l'outil avec :

```bash
python -m eclipse viewer
```

Cette forme fonctionne toujours. L'installation crée aussi une commande plus courte, `eclipse-cleaner`, mais elle n'est trouvée que si le répertoire `Scripts` de votre Python est dans le PATH — sous Windows, ce n'est souvent pas le cas. Si votre terminal répond *« 'eclipse-cleaner' n'est pas reconnu… »*, rien n'est cassé : utilisez simplement `python -m eclipse`. (Une installation via `pipx install git+https://github.com/bebkill/Eclipse-Cleaner.git` configure le PATH pour vous, si vous préférez la commande courte.)

## Utilisation

### Le plus simple : le viewer (recommandé)

```bash
python -m eclipse viewer
```

Une page locale s'ouvre dans le navigateur. Cliquez sur **Parcourir…** pour choisir votre vidéo, puis lancez les trois étapes depuis la page :

1. **Extraire les images** (les vignettes de revue) ;
2. **Analyser les images** (mesures et verdicts automatiques) ;
3. **Produire la vidéo finale** → écrite sous `<source>-eclipse/<source>-clean.mp4` (avec en option une séquence PNG numérotée, dans `<source>-eclipse/frames/`).

Tout ce que la page dérive d'une vidéo — le cache d'analyse, vos décisions de tri, les vignettes de revue, le rendu et l'export PNG — vit dans un unique dossier de travail créé à côté d'elle, `<source>-eclipse/` : un dossier qui contient plusieurs éclipses reste lisible, et deux vidéos ne peuvent jamais partager un cache ni un tri. Le rendu `-clean.mp4` garde un nom propre à l'intérieur de ce dossier, puisque c'est le fichier qu'on en ressort. Les fichiers de travail laissés en vrac à côté d'une vidéo par une version antérieure sont déplacés dans le dossier la première fois que le viewer ouvre cette vidéo, et il le dit au terminal. Une vidéo rendue ne se déplace que si le descripteur écrit à côté d'elle nomme bien *cette* vidéo : l'ancien nom de rendu était partagé entre une source et son transcodage, donc un rendu sans provenance attestée reste exactement là où il est.

Sous le bouton de rendu, la case **stabiliser la couleur** (cochée par défaut) supprime les oscillations de balance des blancs de l'exposition automatique, vers la teinte propre de la séquence — jamais vers le neutre. Sa section dépliable **paramètres** porte les réglages fins : la *fenêtre* de la référence de teinte (typique : 31 images, plafonnée au nombre d'images de la séquence — au-delà la référence sature et agrandir la fenêtre ne change plus rien) et la *correction max* par canal (typique : 0,25, soit ±25 %). La luminosité, elle, est toujours normalisée, case cochée ou non. Changer un paramètre marque le rendu existant « à refaire » : le bandeau ne présente jamais une sortie périmée comme à jour.

Entre les étapes 2 et 3, **revoyez le tri** : la timeline montre les frames conservées en vert, les écartées en rouge, vos propres corrections en bleu. La touche `k` conserve ou écarte la frame courante — chaque bascule est enregistrée immédiatement, et le rendu applique vos décisions automatiquement.

| Touche | Effet |
|---|---|
| `espace` | Lecture / pause |
| `←` `→`, `n` / `p` | Frame précédente / suivante dans la sélection courante |
| `k` | Bascule conserver / écarter sur la frame courante |
| `r` | Filtre : frames retenues seulement |
| `e` | Filtre : frames écartées seulement |
| `m` | Filtre : mes modifications seulement |
| `+` / `-` | Zoom du bandeau de vignettes |

Barres d'avancement, annulation et état des tâches vivent côté serveur : fermer l'onglet ne perd rien.

### En ligne de commande

```bash
python -m eclipse run entree.mp4 sortie.mp4
```

Ou en deux temps, pour rejouer le rendu avec d'autres seuils sans refaire l'analyse :

```bash
python -m eclipse analyze entree.mp4 --cache analysis.json
python -m eclipse render entree.mp4 sortie.mp4 --cache analysis.json --blur-rel 0.35
```

### Options principales

| Option | Rôle |
|---|---|
| `--processus N` | Nombre de processus de travail (défaut : cœurs logiques − 1 ; `1` = séquentiel) |
| `--preset sun\|moon\|planetary\|custom` | Profil de type d'éclipse — voir [Types d'éclipse et presets](#types-déclipse-et-presets) (défaut : détection automatique à l'analyse, preset du cache au rendu) |
| `--seuil-lumiere F` | `analyze`/`run` seulement — fraction du pic de la frame comptée comme « lumière » pour la mesure de capture du masque (défaut : celle du preset) |
| `--radius R` | Rayon apparent du Soleil en pixels, si l'estimation automatique échoue |
| `--taille LxH` | Taille de la fenêtre de recadrage (défaut : 7/9 de la source, même rapport) |
| `--sortie-taille LxH` | Taille du fichier encodé (défaut : celle de la source) |
| `--tolerance-bord PX` | Amputation du disque par le bord de la source tolérée avant rejet (défaut 5) |
| `--depassement-butee PX` | Dépassement maximal de la fenêtre au-delà de la source, comblé par réplication de bord (défaut 400) |
| `--interp-max N` | Longueur maximale d'une coupe comblée par interpolation (défaut 3, `0` désactive) |
| `--interp-deplacement-max PX` | Déplacement maximal de la fenêtre à travers une coupe comblée (défaut 30) |
| `--seuil-masque F` | Fraction minimale de la lumière que le masque solaire doit capturer pour qu'une mesure de centre soit crue (défaut 0,80) |
| `--sans-couleur` | Désactive la stabilisation de la balance des blancs (la luminance reste normalisée) |
| `--couleur-fenetre N` | Fenêtre, en images, de la référence de teinte de la stabilisation (défaut 31) |
| `--couleur-amplitude F` | Correction de teinte maximale par canal, en fraction (défaut 0,25) |
| `--dark-rel`, `--dark-abs`, `--blur-rel`, `--flare-rel`, `--conf-min`, `--ilot-min` | Seuils de tri (obscurité, flou, éblouissement, confiance de localisation, longueur minimale d'îlot conservé) |
| `--decisions FICHIER` / `--sans-decisions` | Utiliser un fichier de revue manuelle donné / ignorer toute revue manuelle |

## Types d'éclipse et presets

Un croissant solaire, une Lune ombrée et un petit disque planétaire brillant n'échouent pas de la même façon : un jeu de seuils calé sur l'un peut laisser un masque de lumière vide ou un vote inversé sur l'autre. Un **preset** fixe donc, par type d'éclipse, à la fois les stratégies de mesure de la passe 1 (figées dans le cache d'analyse) et les seuils de tri par défaut de la passe 2 (que de simples options en ligne de commande peuvent toujours surcharger sans invalider le cache) :

| Preset | mode d'éclairement | mode de rayon | vote | `--seuil-lumiere` | `--dark-abs` | `--seuil-masque` |
|---|---|---|---|---|---|---|
| `sun` | percentile | balayage | double | 0,70 | 40,0 | 0,80 |
| `moon` | maximum | balayage | clair | 0,35 | 5,0 | 0,80 |
| `planetary` | maximum | balayage | clair | 0,35 | 40,0 | 0,80 |
| `custom` | percentile | aire | clair | 0,35 | 40,0 | 0,80 |

- **mode d'éclairement** — comment la région éclairée est seuillée pour estimer le rayon apparent : `percentile` (à mi-chemin entre le fond et le 99e percentile de la frame, le comportement historique) ou `maximum` (relatif au pic de la frame), nécessaire quand le sujet peut être petit ou largement ombré.
- **mode de rayon** — `aire` convertit l'aire de la région éclairée en rayon (exact pour un disque plein, mais dérive à mesure que l'ombre d'une éclipse avance) ou `balayage`, qui trouve le rayon qui maximise la confiance du vote de Hough dirigé (précis à environ ±1,5 % sur les vidéos de calibration, même quand l'ombre grandit).
- **vote** — le régime de vote de Hough utilisé pour trouver le centre du disque : `clair` (un disque lumineux), `sombre` (un disque sombre cerné de lumière — une totalité solaire, où le gradient du limbe pointe vers l'extérieur), ou `double` (évalue les deux par frame et garde le pic le plus net, nécessaire quand une vidéo passe du croissant à la totalité puis inversement).
- `custom` reproduit exactement le comportement d'origine de l'outil, avant les types d'éclipse (identique au bit près sur la séquence solaire de référence).

Pour une **analyse personnalisée**, ou pour comprendre l'effet d'un écart aux réglages par défaut d'un preset, voici ce que le code documente pour chaque seuil de tri ou d'analyse :

| Option | Défaut | Plage / comportement documenté | Effet d'un déplacement |
|---|---|---|---|
| `--dark-rel` | 0,35 (fraction de la médiane locale de `disk_p90`) | Non balayé lors de la calibration ; aucune borne documentée. | Plus haut exige qu'une frame reste plus proche de sa propre référence locale de luminosité pour éviter `too_dark` ; plus bas tolère un assombrissement local plus marqué. |
| `--dark-abs` | 40,0 (5,0 sous le preset `moon`) | Sur les vidéos de calibration, toutes les valeurs de 0,0 à 10,0 donnent des verdicts identiques, et 20,0 coûte déjà sensiblement plus de frames conservées — le 5,0 du preset `moon` se situe donc au milieu de ce plateau, pas sur un bord. | Plancher absolu de luminosité en dessous duquel une frame est rejetée d'office, quelle que soit sa référence locale ; doit être bas pour une Lune totalement ombrée, dont les frames conservées les plus sombres le sont par nature. |
| `--blur-rel` | 0,40 (fraction de la médiane locale de `limb_sharpness`) | Mesuré sur la séquence de référence : le monter à 0,50 rejetait à tort les toutes dernières frames du coucher de Soleil, naturellement plus douces ; le descendre sous 0,40 ne rejetait aucune frame supplémentaire. | Plus haut est plus strict sur la netteté du limbe par rapport à la référence locale ; poussé trop haut, il commence à rejeter des frames qui ne sont plus douces que pour une raison légitime (l'horizon qui mange le limbe, un croissant fin). |
| `--flare-rel` | 3,0 (multiple de la médiane locale de `flare_ratio`) | Non balayé lors de la calibration ; aucune borne documentée. | Plus haut tolère davantage de lumière captée loin du disque (éblouissement, nuage éclairé) avant de rejeter pour `flare` ; plus bas est plus strict. |
| `--conf-min` | 0,02 (confiance minimale du vote de Hough) | Non balayé, mais un point faible documenté : sur la séquence de référence, des frames dont le centre était mal placé affichaient tout de même 0,072 à 0,094 — bien au-dessus de ce défaut. | Ce seuil seul distingue rarement un centre juste d'un centre faux ; `--seuil-masque` (voir plus bas) est la mesure qui attrape réellement un centre qui n'explique pas l'image. |
| `--ilot-min` | 1 (suppression d'îlots effectivement désactivée) | Neutre par construction : lors d'une revue humaine complète de la séquence de référence, les 29 « îlots » d'une seule frame que l'ancien réglage, plus strict, aurait supprimés se sont tous révélés être des conservations réelles et voulues. | Le monter (ex. 5) supprime les plages conservées courtes bordées des deux côtés par au moins autant de frames rejetées — à réactiver seulement si une vidéo produit de vrais éclairs d'une seule frame. |
| `--seuil-masque` | 0,80 (fraction de la lumière de la frame que le masque du disque doit capturer) | Sur la séquence de référence, les 33 échecs francs se situent tous sous 0,50 ; entre 0,50 et 0,92 les valeurs se répartissent de façon continue plutôt qu'en deux groupes distincts, si bien qu'aucun seuil de cette plage n'est objectivement « le » bon. | Plus haut ne fait confiance à un centre que si le masque explique une plus grande part de la lumière de la frame ; trop haut, il commence à écarter des centres justes dès que la zone éclairée rétrécit légitimement (croissant profond, halo d'une totalité non filtrée — voir `--seuil-lumiere` plus bas). |
| `--seuil-lumiere` | selon le preset : 0,35 (`custom`/`moon`/`planetary`), 0,70 (`sun`) | Mesuré sur une vidéo de totalité solaire : 0,60 → une médiane de `masse_captee` de 0,730, 0,70 → 0,898 (le coude de la courbe), aucun gain supplémentaire au-delà ; à 0,90 chaque frame atteint 1,000 et la mesure cesse de discriminer quoi que ce soit. | Fraction du pic de luminosité d'une frame comptée comme « lumière » lors de la vérification que le masque du disque la capture (passe 1, figée dans le cache). Trop bas sur une totalité non filtrée, la couronne échappe à un masque de la taille du disque et fait échouer à tort un centre pourtant juste ; trop haut, la mesure sature et cesse de distinguer les centres justes des faux. |
| `--tolerance-bord` | 5,0 px (amputation du disque tolérée au bord de la source avant rejet) | Mesuré sur un disque de référence de 799 px : 25 px représentent 3 % du diamètre et sont visiblement amputés, 5 px (0,6 %) ne le sont pas ; le nombre de frames conservées croît lentement avec elle (1785 à 0 px, 1805 à 5 px, 1846 à 10 px, 1896 à 25 px). Une marge de sécurité de réplication de bord, sans rapport direct, cesse aussi de protéger pleinement la frame au-delà d'un point qui dépend de la géométrie de la source (environ 37 px sur cette même vidéo). | Plus haut tolère une amputation plus importante du disque par le bord de la source avant de rejeter la frame comme `hors_source`, au prix d'un rognage visible une fois qu'il devient important par rapport au disque. |

**Détection automatique, et comment la contourner.** `analyze` et `run` sondent un échantillon de frames réparties sur la source et affichent le type détecté avant d'analyser — la ligne de commande affiche par exemple `Type d'eclipse detecte : moon`. La détection s'appuie sur le fond du ciel (lumière du jour ou halo, contre un ciel noir), le contraste interne propre du disque (une ombre qui le traverse), un seuil de taille pour un petit disque planétaire, et, en dernier recours, une teinte chaude (un Soleil filtré). C'est une *suggestion*, jamais appliquée en silence ni définitive : passez `--preset sun|moon|planetary|custom` en ligne de commande pour en forcer un, ou utilisez le sélecteur du panneau Source du viewer, qui montre le type actuellement en vigueur : tant que rien n'y a été choisi, c'est celui sous lequel le cache a été analysé qui s'applique, ou, à défaut, la suggestion. Le preset pilotant les stratégies de mesure de la passe 1, **le changer — en ligne de commande ou dans le viewer — exige toujours une nouvelle analyse** : le cache enregistre son propre preset et refuse d'être réutilisé sous un autre, avec un message qui dit quoi relancer.

## Limites connues

- **Les hautes lumières écrêtées ne se reconstruisent pas** — le niveau est rétabli, mais le détail perdu dans la saturation reste perdu.
- **La couronne d'une totalité solaire peut être écrêtée par la normalisation de luminosité.** La photométrie elle-même n'est pas modifiée par la fonctionnalité de type d'éclipse ; c'est une limite déjà existante qu'une totalité rend simplement plus visible.
- **Le preset `planetary` n'a jamais été calibré sur des images réelles** — seulement sur des frames synthétiques. Si vous disposez d'images réelles d'un transit planétaire, [ouvrez une issue](https://github.com/bebkill/Eclipse-Cleaner/issues) avec un échantillon : c'est le moyen le plus rapide de le régler.
- **Sur une vidéo solaire partielle seulement, la détection automatique choisit le preset `sun`**, dont le tri est légèrement plus strict que le comportement historique (`custom`) — 1661 frames conservées contre 1726 sur la séquence de référence. Passez `--preset custom` pour reproduire exactement le résultat d'avant les presets.
- **Deux rendus colorimétriques peuvent cohabiter dans un même film** si un filtre solaire a été retiré en cours de séquence. C'est ce qui s'est réellement passé devant la caméra, donc c'est conservé, au même titre que les traversées nuageuses.
- Le bouton **Parcourir…** du viewer a besoin d'une session graphique (il utilise la boîte de dialogue du système via `tkinter`). Sur une machine sans affichage, passez le chemin de la vidéo en argument.
- Sous **macOS**, le bouton Parcourir… est désactivé pour l'instant : l'ouvrir depuis le viewer ferait planter Python (macOS n'autorise les fenêtres système que depuis le fil principal — voir [#4](https://github.com/bebkill/Eclipse-Cleaner/issues/4)). Passez le chemin de la vidéo en argument : `python -m eclipse viewer chemin/vers/video.mp4`. Une boîte de dialogue native macOS est prévue ([#1](https://github.com/bebkill/Eclipse-Cleaner/issues/1)).
- La suite de tests est développée sous **Windows** ; quatre tests propres à Windows se sautent automatiquement sous Linux/macOS.

Ce qui est à l'étude pour la suite (entrée SER, AVI brut Bayer, choix du format de sortie…) se trouve dans la [feuille de route](ROADMAP.fr.md).

## Tests

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Les assertions portent sur des frames synthétiques à vérité terrain connue ; aucune vidéo réelle n'est nécessaire.

## Partagez vos résultats — et soutenez le projet

Ce programme a sauvé ma vidéo, et j'espère qu'il pourra aider d'autres chasseurs d'éclipses dans mon cas. N'hésitez pas à partager vos résultats, vos commentaires et vos suggestions d'amélioration pour cet humble programme — les [issues](https://github.com/bebkill/Eclipse-Cleaner/issues) et les pull requests sont les bienvenues.

Et si, comme pour moi, il a permis de sauver votre vidéo, vous pouvez soutenir le projet :

<a href="https://www.buymeacoffee.com/bebkill"><img src="https://img.shields.io/badge/☕%20Buy%20Me%20a%20Coffee-merci%20!-yellow?style=for-the-badge" alt="Buy Me a Coffee"></a>

## Licence

[MIT](LICENSE) — libre d'utilisation, de modification et de partage.

Ciel clair ! 🌘
