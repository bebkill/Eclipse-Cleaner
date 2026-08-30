"""Point d'entree du gel PyInstaller (executable Windows).

Deux ecarts voulus par rapport a `python -m eclipse`, tous deux propres a
l'executable :

- multiprocessing.freeze_support() en PREMIERE instruction : sous Windows,
  chaque travailleur de Pool relance l'executable lui-meme ; sans cet appel,
  le relancement rejoue main() au lieu d'entrer dans le protocole des
  travailleurs, et l'exe se re-lance en cascade au premier rendu parallele.
- sans argument, la sous-commande `viewer` est inseree : l'utilisateur de
  l'exe telecharge et double-clique, il n'a aucune sous-commande a
  connaitre. Avec des arguments, la ligne de commande garde exactement le
  comportement de `python -m eclipse`.
"""
import multiprocessing
import sys


def argv_effectif(argv):
    """`viewer` par defaut : un double-clic n'apporte aucun argument."""
    return argv if argv else ["viewer"]


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from eclipse.pipeline import main
    sys.exit(main(argv_effectif(sys.argv[1:])))
