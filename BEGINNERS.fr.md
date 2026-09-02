# Eclipse Cleaner — guide pour vrais débutants (Windows)

**[🇬🇧 English version](BEGINNERS.md)**

Jamais utilisé Python ? Jamais tapé une commande ? Cette page est pour vous.

D'abord, rassurez-vous : **vous n'écrirez aucun code, et il n'y a aucun script à créer.** Eclipse Cleaner est un programme tout prêt. Il suffit de l'installer une fois en copiant-collant deux commandes, puis tout se passe dans votre navigateur web, avec des boutons. Comptez une dizaine de minutes pour l'installation.

## Le raccourci — un programme Windows tout prêt, sans Python du tout

Si vous voulez simplement que ça marche, les étapes ci-dessous ne sont même pas nécessaires :

1. Ouvrez la [dernière release](https://github.com/bebkill/Eclipse-Cleaner/releases/latest) et, sous **Assets**, téléchargez le fichier se terminant par `-windows-x64.exe`.
2. Enregistrez-le n'importe où (le Bureau convient très bien) et **double-cliquez dessus**. Le premier démarrage prend un petit moment — le programme se décompresse — puis Eclipse Cleaner s'ouvre dans votre navigateur.

**Windows va se méfier — c'est attendu.** Le programme n'est pas signé avec un certificat d'éditeur payant, et un fichier tout juste publié n'a encore aucune « réputation » : Windows le traite donc durement. Vous pouvez passer outre en confiance : le programme est open source, ce fichier précis est construit publiquement par la CI du projet à partir du code source du tag, son empreinte SHA-256 est publiée juste à côté dans la release, et l'analyse Windows Defender ne trouve rien. Selon l'endroit où Windows vous arrête :

- **Dans le navigateur, au téléchargement** (Edge : *« … a été bloqué car il pourrait endommager votre appareil »*) : cliquez sur le menu **…** du téléchargement, puis **Conserver** → **Afficher plus** → **Conserver quand même**.
- **Au premier lancement** (*« Windows a protégé votre ordinateur »*) : cliquez sur **Informations complémentaires**, puis **Exécuter quand même**. Il ne le demande qu'une fois.
- **Si aucun de ces boutons n'apparaît** : clic droit sur le fichier téléchargé → **Propriétés** → cochez **Débloquer** en bas → **OK**, puis double-cliquez à nouveau.
- **Si ça ne se lance toujours pas** : ouvrez **Sécurité Windows → Contrôle des applications et du navigateur**. Si **Smart App Control** est *Activé* (le réglage d'usine des PC Windows 11 récents), Windows refuse catégoriquement tout programme non signé, sans exception possible — tant que ce programme n'est pas signé, passez par la voie Python ci-dessous : elle n'est pas concernée.

Encore une chose à savoir : une **fenêtre noire** s'ouvre à côté du navigateur. C'est le programme lui-même, qui affiche sa progression. La fermer arrête Eclipse Cleaner.

**Pour désinstaller** : supprimez le fichier exe — rien d'autre n'est installé sur votre système. Les fichiers de travail (`analysis.json`, `decisions.json`, un dossier de vignettes, la vidéo nettoyée) vivent tous dans un seul dossier créé à côté de votre vidéo source, nommé d'après elle avec `-eclipse` ajouté — `moneclipse.mp4-eclipse/` ; supprimez ce dossier aussi si vous le souhaitez. Si vous avez utilisé une version antérieure, dont les fichiers de travail traînaient à côté de la vidéo, ils sont déplacés dans ce dossier la première fois que la nouvelle version ouvre votre vidéo, et la fenêtre du terminal le dit.

C'est toute l'installation. Tout ce que décrit [l'utilisation dans le README](README.fr.md#utilisation) s'applique tel quel — vous pouvez arrêter votre lecture ici. La suite de cette page est la voie classique par Python, qui fonctionne aussi sous macOS et Linux et rend les mises à jour plus légères à télécharger.

## Étape 1 — Installer Python (une seule fois)

Python est le logiciel gratuit sur lequel tourne Eclipse Cleaner. Le plus simple sous Windows 10/11 est le Microsoft Store — aucune option à ne pas rater :

1. Cliquez sur le bouton **Démarrer** et tapez `Microsoft Store`, puis ouvrez-le.
2. Dans la barre de recherche du Store, tapez `Python 3.13`.
3. Choisissez l'application nommée **Python 3.13** publiée par la *Python Software Foundation*, et cliquez sur **Obtenir** (ou **Installer**).
4. Attendez la fin de l'installation, puis fermez le Store.

*(Alternative pour ceux qui préfèrent l'installateur classique de [python.org](https://www.python.org/downloads/) : sur le tout premier écran, cochez la case **« Add python.exe to PATH »** avant de cliquer sur Install. Si vous oubliez cette case, Windows ne trouvera pas Python ensuite.)*

## Étape 2 — Ouvrir un terminal

Le « terminal », c'est simplement une fenêtre où l'on tape (ou colle) des commandes. Windows en a déjà un :

1. Cliquez sur le bouton **Démarrer** et tapez `terminal`.
2. Ouvrez l'application appelée **Terminal** (ou **Windows PowerShell** — les deux conviennent).

Une fenêtre sombre s'ouvre avec un curseur qui clignote. Voilà — vous êtes prêt.

**Comment exécuter une commande :** chaque cadre gris de cette page a une petite icône de copie dans son coin supérieur droit (sur le site GitHub). Cliquez dessus, revenez dans la fenêtre du terminal, appuyez sur **Ctrl+V** pour coller (un simple clic droit colle aussi dans le terminal), puis appuyez sur **Entrée**. Une « commande », ce n'est rien de plus.

## Étape 3 — Vérifier que Python répond

Collez ceci dans le terminal et appuyez sur Entrée :

```
python --version
```

- S'il répond quelque chose comme `Python 3.13.2` (n'importe quel numéro **3.12 ou plus**), parfait — passez à l'étape 4.
- Si une fenêtre du Microsoft Store s'ouvre à la place, ou si vous obtenez *« 'python' n'est pas reconnu »*, Python n'est pas encore installé : retournez à l'étape 1.
- S'il répond un numéro **inférieur à 3.12**, installez la version actuelle depuis le Store (étape 1) ; elle prendra le relais.

## Étape 4 — Installer Eclipse Cleaner (une seule fois)

Collez cette commande et appuyez sur Entrée :

```
python -m pip install https://github.com/bebkill/Eclipse-Cleaner/archive/refs/heads/main.zip
```

Des lignes vont défiler pendant une minute ou deux — c'est normal, ça télécharge. Les lignes jaunes de type *warning* sont sans gravité. À la fin, les dernières lignes disent **`Successfully installed …`**.

## Étape 5 — Lancer Eclipse Cleaner

Collez cette commande et appuyez sur Entrée :

```
python -m eclipse viewer
```

Une page s'ouvre dans votre navigateur web. À partir de là, plus aucune commande — tout se fait avec des boutons :

1. Cliquez sur **Parcourir…** et choisissez votre vidéo d'éclipse.
2. Lancez les trois étapes affichées sur la page : extraire les vignettes, analyser les frames, produire la vidéo finale. Le type d'éclipse — Soleil, Lune ou transit planétaire — est détecté automatiquement à l'étape d'analyse ; si la suggestion semble fausse, changez-la dans le panneau Source.
3. La vidéo nettoyée est écrite **dans le dossier de travail créé à côté de votre originale** (`moneclipse.mp4-eclipse/`), avec `-clean` ajouté à son nom (ex. `moneclipse-clean.mp4`). Recopiez-la où vous voulez — elle garde un nom propre justement pour ça.

Gardez la fenêtre du terminal ouverte pendant que vous utilisez Eclipse Cleaner — c'est le moteur qui tourne derrière la page. Quand vous avez terminé, fermez simplement l'onglet du navigateur et la fenêtre du terminal.

## Toutes les fois suivantes

Une seule chose à faire : ouvrir un terminal (étape 2) et coller :

```
python -m eclipse viewer
```

## Pour mettre à jour Eclipse Cleaner plus tard

Collez cette commande (c'est celle de l'étape 4 plus `--force-reinstall`, qui garantit que la dernière version remplace bien l'ancienne) :

```
python -m pip install --force-reinstall https://github.com/bebkill/Eclipse-Cleaner/archive/refs/heads/main.zip
```

## Bonus — ouvrir un terminal directement dans un dossier

Ce n'est pas nécessaire pour le viewer (le bouton **Parcourir…** trouve votre vidéo pour vous), mais c'est pratique si vous essayez plus tard le mode ligne de commande décrit dans le [README](README.fr.md) :

1. Ouvrez l'**Explorateur de fichiers** et naviguez jusqu'au dossier contenant votre vidéo.
2. Faites un clic droit sur une zone vide du dossier et choisissez **« Ouvrir dans le Terminal »**.

Le terminal s'ouvre déjà « dans » ce dossier : vous pouvez alors désigner vos fichiers par leur simple nom.

## Si quelque chose ne va pas

| Symptôme | Solution |
|---|---|
| *« 'python' n'est pas reconnu »* ou le Store s'ouvre | Python n'est pas installé — faites l'étape 1. Si vous avez utilisé l'installateur python.org, relancez-le et cochez **« Add python.exe to PATH »**. |
| *« 'pip' n'est pas reconnu »* | Tapez toujours `python -m pip …` (comme sur cette page), jamais `pip` tout seul. |
| Des lignes d'erreur rouges à l'étape 4 | Vérifiez votre connexion internet et relancez la commande. Si ça persiste, vérifiez que `python --version` donne 3.12 ou plus. |
| La page ne s'ouvre pas dans le navigateur | Regardez dans le terminal : il affiche une adresse commençant par `http://127.0.0.1` — copiez-la dans la barre d'adresse de votre navigateur. |

Toujours bloqué ? Ouvrez une [issue](https://github.com/bebkill/Eclipse-Cleaner/issues) en décrivant ce que vous avez fait et ce que le terminal a répondu — les questions de débutants sont bienvenues.

Ciel dégagé ! 🌘
