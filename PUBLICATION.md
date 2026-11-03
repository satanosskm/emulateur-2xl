# Publier le dépôt sur GitHub

Le dossier est prêt : il n'y a rien à modifier avant la mise en ligne. Choisis l'une des deux
méthodes, la première est la plus simple.

## Méthode 1 : depuis le site GitHub, sans ligne de commande

1. Va sur <https://github.com/new>.
2. Nom du dépôt : `emulateur-2xl`. Coche **Public** si tu veux que le README et les versions soient
   visibles par tout le monde, ou **Private** sinon. Ne coche ni README, ni .gitignore, ni licence :
   ils sont déjà dans le dossier.
3. Clique sur **Create repository**.
4. Sur la page qui s'affiche, clique sur le lien **uploading an existing file**.
5. Ouvre le dossier `github`, sélectionne tout son contenu, puis fais-le glisser dans la page.
   Vérifie que le dossier caché `.github` a bien été envoyé.
6. Clique sur **Commit changes**.
7. Ouvre l'onglet **Actions** : la compilation Windows démarre seule et produit une archive
   téléchargeable après quelques minutes.

## Méthode 2 : en ligne de commande

```bash
cd github
git init -b main
git add .
git commit -m "Émulateur robot 2-XL 2.1.2"
git remote add origin https://github.com/SATANOS/emulateur-2xl.git
git push -u origin main
```

Remplace `SATANOS` par ton identifiant GitHub. Si le dépôt a déjà un commit initial, fais
`git pull --rebase origin main` avant de pousser.

## Méthode 3 : GitHub Desktop

1. **File** puis **Add local repository**, choisis le dossier `github`.
2. Clique sur **Publish repository**, décoche éventuellement **Keep this code private**.

## Publier la version avec l'exécutable

L'exécutable reste hors du dépôt, comme prévu. Deux façons de le distribuer :

### Publication automatique à partir d'une étiquette

Le fichier `.github/workflows/build.yml` compile, teste et publie l'archive dès qu'une étiquette
`v*` est poussée :

```bash
git tag v2.1.2
git push origin v2.1.2
```

La version apparaît alors dans l'onglet **Releases**, avec l'archive
`Emulateur-2XL-2.1.2-windows.zip` en pièce jointe.

### Publication manuelle avec ton propre exécutable

1. Ouvre l'onglet **Releases**, puis clique sur **Draft a new release**.
2. Étiquette : `v2.1.2`. Titre : `Émulateur robot 2-XL 2.1.2`.
3. Dans la zone de description, colle le contenu de `docs/note-de-version.md`.
4. Clique sur **Attach binaries**, puis dépose le fichier
   `Emulateur-2XL-2.1.2-windows.zip` se trouvant à la racine de ce dossier.
5. Clique sur **Publish release**.

Pour régénérer l'archive après une nouvelle compilation :

```bash
python scripts/release.py
```

## Vérifications avant publication

- Le README et le `LISEZ-MOI.txt` sont à jour.
- `dist/` et les fichiers audio ne sont jamais envoyés : ils figurent dans `.gitignore`.
- La licence MIT porte le nom de Satanos.
