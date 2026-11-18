# Publier le dépôt et les mises à jour sur GitHub

Dépôt : <https://github.com/satanosskm/emulateur-2xl>

Deux dossiers ont des rôles différents, à ne pas confondre :

- **`github/`** : c'est le contenu du dépôt. C'est ce dossier que l'on envoie sur GitHub.
- **`2xl-emulator/`** : l'atelier de développement. Il n'est jamais envoyé tel quel, il sert à
  préparer le dossier `github/`.

Pour une mise à jour, on recopie l'atelier vers `github/`, on envoie, puis on publie une nouvelle
version avec le nouvel exécutable.

## Mise à jour, méthode simple, sans ligne de commande

1. Ouvre le dossier `github` sur ton ordinateur.
2. Va sur <https://github.com/satanosskm/emulateur-2xl>. Le dépôt accepte l'envoi direct des fichiers.
3. Clique sur **Add file**, puis **Upload files**.
4. Sélectionne tout le contenu du dossier `github` et dépose-le dans la page. Les fichiers déjà
   présents sont remplacés, les nouveaux sont ajoutés.
5. Dans le champ de message, écris par exemple `Mise à jour 2.1.3 : lecture après changement de
   cassette`, puis clique sur **Commit changes**.
6. Ouvre l'onglet **Releases**, puis **Draft a new release**.
7. Étiquette : `v2.1.3`, en choisissant **Create new tag**. Titre : `Émulateur robot 2-XL 2.1.3`.
8. Colle la description qui se trouve dans `docs/note-de-version.md`.
9. Dans **Attach binaries**, dépose `Emulateur-2XL-2.1.3.exe` et renomme-le `Emulateur-2XL.exe`
   avant l'envoi, pour conserver une adresse de téléchargement stable.
10. Clique sur **Publish release**.

## Mise à jour, en ligne de commande

```bash
cd github
git init -b main                 # une seule fois, à la première utilisation
git add .
git commit -m "Mise à jour 2.1.3"
git remote add origin https://github.com/satanosskm/emulateur-2xl.git   # une seule fois
git push -u origin main
```

Pour les fois suivantes, seules ces trois lignes servent :

```bash
cd github
git add . && git commit -m "Mise à jour 2.1.3"
git push
```

## Publier la version avec l'exécutable

L'exécutable n'est pas dans le dépôt : il est joint à la version, dans l'onglet **Releases**.

Si le dossier `.github` a bien été envoyé, la compilation et la publication se font seules dès qu'une
étiquette est poussée :

```bash
git tag v2.1.3
git push origin v2.1.3
```

Sinon, la publication manuelle décrite plus haut prend deux minutes.

## Vérifications avant d'envoyer

- `python -m pytest tests -q` doit passer.
- `python -m ruff check twoxl tools tests scripts` doit passer.
- Le numéro de version doit être le même dans `twoxl/__init__.py` et `pyproject.toml`.
- `docs/note-de-version.md` doit décrire la nouvelle version.
- Aucun fichier audio ne doit se trouver dans `cassettes/`.
