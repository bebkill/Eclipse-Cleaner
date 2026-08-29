# Eclipse Cleaner

*Nettoyer et stabiliser un timelapse d'éclipse solaire instable — et peut-être sauver une vidéo que vous pensiez perdue.*

**[🇬🇧 English version](README.md)**

[![CI](https://github.com/bebkill/Eclipse-Cleaner/actions/workflows/ci.yml/badge.svg)](https://github.com/bebkill/Eclipse-Cleaner/actions/workflows/ci.yml)
[![Licence : MIT](https://img.shields.io/badge/Licence-MIT-green.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-soutenir-yellow?logo=buymeacoffee&logoColor=white)](https://www.buymeacoffee.com/bebkill)

## L'histoire de ce projet

Ce projet a commencé avec la capture de l'éclipse solaire du **12 août 2026**. L'installation de mon matériel n'était pas idéale — des personnes passaient régulièrement devant — mais j'ai quand même pu capturer l'éclipse. Puis j'ai découvert la vidéo produite par mon smart-télescope, et j'étais dégoûté : c'était inutilisable. Très instable, avec beaucoup de séquences masquées suivies de phases de récupération du tracking et d'ajustements brutaux de luminosité.

J'ai essayé de corriger avec les outils disponibles (SIRIL et PIPP), mais je n'ai sans doute pas su y faire — ou c'était tout simplement trop leur demander. Je me suis donc lancé dans la conception d'un outil, avec l'aide de mon assistant IA préféré. Le voici.

## Avant / après

| Originale (sortie brute du smart-télescope) | Nettoyée avec Eclipse Cleaner |
| :---: | :---: |
| ![Vidéo originale, instable](docs/assets/before.gif) | ![Vidéo nettoyée, stabilisée](docs/assets/after.gif) |

*Les deux extraits sont accélérés 6×.*

## Ce que fait le programme

- **Trie les frames** — seules les frames irréparables sont rejetées : flou, obscurité, éblouissement, échec de localisation. Une frame décadrée n'est *pas* une frame défectueuse ; le stabilisateur corrige la position.
- **Verrouille le disque solaire au centre** — un vote de Hough dirigé à rayon fixe trouve le disque même sur un croissant fin, et tolère qu'un nuage masque une partie du limbe.
- **Déplace le cadre comme le ferait un cadreur** — la fenêtre de recadrage est planifiée, butée en douceur contre les bords de la source, et absorbe les sauts de ré-acquisition du tracking (des centaines de pixels en une frame) au lieu de saccader.
- **Normalise l'exposition — la luminance seulement.** La couleur est laissée telle que filmée : un filtre solaire rouge reste rouge, un coucher de soleil reste chaud.
- **Comble les coupes courtes** par interpolation linéaire, et n'invente jamais d'images pour les longues : un vrai trou reste une coupe franche.
- **Un viewer de revue dans le navigateur** — inspecter chaque frame, corriger le tri automatique d'une touche, puis relancer le rendu. Bilingue (français/anglais), servi sur 127.0.0.1 uniquement.

Tout s'adapte à votre vidéo : résolution, format, cadence et rayon apparent du Soleil sont mesurés, pas supposés, et chaque seuil peut être ajusté en ligne de commande.

## Installation

Nécessite **Python 3.12+**. Le binaire ffmpeg est fourni par `imageio-ffmpeg` : rien d'autre à installer sur le système.

```bash
python -m pip install git+https://github.com/bebkill/Eclipse-Cleaner.git
```

Ou depuis un clone :

```bash
git clone https://github.com/bebkill/Eclipse-Cleaner.git
cd Eclipse-Cleaner
python -m pip install .
```

Les deux donnent la commande `eclipse-cleaner` (`python -m eclipse` fonctionne à l'identique). Pour une installation isolée, `pipx install git+https://github.com/bebkill/Eclipse-Cleaner.git` fonctionne aussi.

## Utilisation

### Le plus simple : le viewer (recommandé)

```bash
eclipse-cleaner viewer
```

Une page locale s'ouvre dans le navigateur. Cliquez sur **Parcourir…** pour choisir votre vidéo, puis lancez les trois étapes depuis la page :

1. **Extraire les images** (les vignettes de revue) ;
2. **Analyser les images** (mesures et verdicts automatiques) ;
3. **Produire la vidéo finale** → écrite à côté de la source, sous `<source>-clean.mp4` (avec en option une séquence PNG numérotée).

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
eclipse-cleaner run entree.mp4 sortie.mp4
```

Ou en deux temps, pour rejouer le rendu avec d'autres seuils sans refaire l'analyse :

```bash
eclipse-cleaner analyze entree.mp4 --cache analysis.json
eclipse-cleaner render entree.mp4 sortie.mp4 --cache analysis.json --blur-rel 0.35
```

### Options principales

| Option | Rôle |
|---|---|
| `--processus N` | Nombre de processus de travail (défaut : cœurs logiques − 1 ; `1` = séquentiel) |
| `--radius R` | Rayon apparent du Soleil en pixels, si l'estimation automatique échoue |
| `--taille LxH` | Taille de la fenêtre de recadrage (défaut : 7/9 de la source, même rapport) |
| `--sortie-taille LxH` | Taille du fichier encodé (défaut : celle de la source) |
| `--tolerance-bord PX` | Amputation du disque par le bord de la source tolérée avant rejet (défaut 5) |
| `--depassement-butee PX` | Dépassement maximal de la fenêtre au-delà de la source, comblé par réplication de bord (défaut 400) |
| `--interp-max N` | Longueur maximale d'une coupe comblée par interpolation (défaut 3, `0` désactive) |
| `--interp-deplacement-max PX` | Déplacement maximal de la fenêtre à travers une coupe comblée (défaut 30) |
| `--seuil-masque F` | Fraction minimale de la lumière que le masque solaire doit capturer pour qu'une mesure de centre soit crue (défaut 0,80) |
| `--dark-rel`, `--dark-abs`, `--blur-rel`, `--flare-rel`, `--conf-min`, `--ilot-min` | Seuils de tri (obscurité, flou, éblouissement, confiance de localisation, longueur minimale d'îlot conservé) |
| `--decisions FICHIER` / `--sans-decisions` | Utiliser un fichier de revue manuelle donné / ignorer toute revue manuelle |

## Limites connues

- **Les hautes lumières écrêtées ne se reconstruisent pas** — le niveau est rétabli, mais le détail perdu dans la saturation reste perdu.
- **Deux rendus colorimétriques peuvent cohabiter dans un même film** si un filtre solaire a été retiré en cours de séquence. C'est ce qui s'est réellement passé devant la caméra, donc c'est conservé, au même titre que les traversées nuageuses.
- Le bouton **Parcourir…** du viewer a besoin d'une session graphique (il utilise la boîte de dialogue du système via `tkinter`). Sur une machine sans affichage, passez le chemin de la vidéo en argument.
- La suite de tests est développée sous **Windows** ; trois tests propres à Windows se sautent automatiquement sous Linux/macOS.

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
