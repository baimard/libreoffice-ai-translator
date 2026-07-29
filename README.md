# LibreOffice AI Translator

Extension LibreOffice Writer permettant de traduire une sélection ou un document complet à l'aide de l'API OpenAI, tout en conservant au mieux la mise en forme du document.

## Fonctionnalités

- traduction du texte sélectionné ;
- traduction du document Writer complet ;
- remplacement du texte ou insertion de la traduction sous le texte d'origine ;
- choix de la langue cible et, facultativement, de la langue source ;
- configuration du modèle OpenAI, de l'URL d'API et de la clé API ;
- traitement par blocs pour limiter la taille des requêtes ;
- conservation de la mise en forme grâce à une traduction paragraphe par paragraphe ;
- paquet `.oxt` construit sans dépendance Python externe.

## Prérequis

- LibreOffice 7.4 ou version ultérieure ;
- Python intégré à LibreOffice avec les modules UNO ;
- accès réseau à l'API OpenAI ;
- une clé API OpenAI.

## Construction

```bash
make build
```

Le paquet est produit dans `dist/libreoffice-ai-translator.oxt`.

## Installation

### Interface graphique

Ouvrez LibreOffice, puis :

1. **Outils > Gestionnaire des extensions** ;
2. cliquez sur **Ajouter** ;
3. sélectionnez `dist/libreoffice-ai-translator.oxt` ;
4. redémarrez LibreOffice.

### Ligne de commande

```bash
make install
```

Pour désinstaller :

```bash
make uninstall
```

## Utilisation

Dans Writer, utilisez le menu **Outils > LibreOffice AI Translator** :

- **Configurer…** : renseigne la clé API et les préférences ;
- **Traduire la sélection** : traduit uniquement la sélection courante ;
- **Traduire le document** : traduit le contenu textuel du document.

La clé API est enregistrée dans le profil utilisateur LibreOffice. Elle n'est jamais incluse dans le document ni envoyée ailleurs qu'à l'URL d'API configurée.

## Développement

La logique principale se trouve dans `extension/pythonpath/ai_translator.py`. L'extension utilise uniquement la bibliothèque standard Python afin de fonctionner avec le Python embarqué de LibreOffice.

```bash
make check
make build
```

## Limites actuelles

- Writer uniquement ;
- la traduction des tableaux, cadres et objets complexes est encore best effort ;
- la conservation parfaite des styles au milieu d'un même paragraphe dépend de la structure du document ;
- les notes de bas de page, champs automatiques et objets incorporés ne sont pas modifiés directement.

## Licence

GNU Affero General Public License v3.0 ou ultérieure.