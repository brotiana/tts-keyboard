# tts-keyboard

Application de dictée clavier en français avec Whisper.

Parlez en français, puis insérez le texte dans votre application avec **F8**.
Whisper transforme la voix en texte : c'est de la dictée vocale (STT).
Le terme TTS désigne l'opération inverse, faire lire un texte à voix haute.

L'application Python fonctionne localement après installation. Elle ne télécharge
aucun modèle au lancement et n'envoie pas votre voix à un service distant.
Les téléchargements se font séparément, au cybercafé.

## Modèles adaptés à votre ordinateur

Configuration détectée : Linux Mint 22.2, Python 3.12, Intel Core i5-4590
(4 cœurs, AVX2), 16 Go de RAM, NVIDIA GeForce GT 730.
L'application utilise le **processeur, 4 threads et le calcul int8**, pour limiter
la mémoire. La GT 730 n'est pas utilisée. La rapidité réelle dépend de la durée
de la phrase ; aucune transcription en temps réel n'est garantie.

Les quatre modèles ci-dessous sont multilingues et comprennent le français.
La langue de transcription est fixée à `fr` dans l'application.

| Modèle | Téléchargement, environ | Usage conseillé | Dépôt officiel |
|---|---:|---|---|
| **tiny** | 78,2 Mo | Le plus léger ; gardez-le comme solution de secours. | [Systran / tiny](https://huggingface.co/Systran/faster-whisper-tiny/tree/main) |
| **base** | 148 Mo | Bon point de départ sur votre i5. | [Systran / base](https://huggingface.co/Systran/faster-whisper-base/tree/main) |
| **small** | 486 Mo | À essayer pour améliorer la reconnaissance, avec davantage d'attente. | [Systran / small](https://huggingface.co/Systran/faster-whisper-small/tree/main) |
| **medium** | 1,53 Go | Option plus lourde ; potentiellement très lente sur ce processeur. | [Systran / medium](https://huggingface.co/Systran/faster-whisper-medium/tree/main) |

Téléchargez au minimum **base et tiny** pour pouvoir changer immédiatement si
l'un échoue. Les tailles n'incluent pas les bibliothèques Python. N'utilisez pas
les versions `.en` (anglais seulement), ni les fichiers `.pt` d'OpenAI : cette
application attend le format **faster-whisper / CTranslate2**.

## Au cybercafé : préparer la clé USB

Emportez ce dossier de code. Vous avez besoin **des modèles et des bibliothèques
Python** ; télécharger seulement les modèles ne suffit pas à installer l'application.

### 1. Télécharger les modèles

**Avec un navigateur seulement :** ouvrez [TELECHARGEMENTS.html](TELECHARGEMENTS.html).
Cette page contient les **16 liens directs** (4 fichiers pour chacun des 4 modèles).
Téléchargez les quatre fichiers du modèle choisi et conservez leurs noms exacts :

```text
tts keyboard/
├── app.py
├── models/
│   ├── tiny/
│   │   ├── config.json
│   │   ├── model.bin
│   │   ├── tokenizer.json
│   │   └── vocabulary.txt
│   └── base/
│       ├── config.json
│       ├── model.bin
│       ├── tokenizer.json
│       └── vocabulary.txt
└── wheelhouse/
```

Si le navigateur ajoute `(1)` ou `.txt` à un nom, rétablissez le nom exact.
Un fichier `model.bin` de quelques octets est invalide : le vrai fichier pèse
environ 75,5 Mo pour tiny, 145 Mo pour base, 484 Mo pour small ou 1,53 Go pour medium.

**Avec Python 3.10 ou plus au cybercafé :** ouvrez un terminal dans ce dossier et lancez :

```bash
python3 telecharger_modeles.py tiny base
```

Sous Windows, utilisez `py` à la place de `python3` :

```powershell
py telecharger_modeles.py tiny base
```

Vous pouvez ajouter `small medium`, ou télécharger chaque modèle séparément.
Le script reprend les fichiers `.part` après une interruption et vérifie le SHA256
des poids. En cas de coupure, relancez exactement la même commande. Sans argument,
il affiche l'aide et ne télécharge rien. `--liens` affiche les URL sans connexion.

### 2. Télécharger les bibliothèques Python

Sur l'ordinateur du cybercafé, **Python et pip doivent être disponibles**.
Préférez Python **3.12**, comme sur votre ordinateur ; la préparation sur un autre
Linux Mint 22 avec Python 3.12 offre la meilleure correspondance des dépendances.
Dans le dossier du code, lancez :

```bash
python3 preparer_hors_ligne.py
```

Sous Windows : `py preparer_hors_ligne.py`.
Le script collecte dans `wheelhouse/` les paquets pour **votre Linux x86_64 avec
Python 3.12**, même si l'ordinateur du cybercafé utilise Windows. Il n'installe
rien sur cet ordinateur. Attendez le message « Paquets prêts » avant de partir.
Un échec de pip signifie que la préparation est incomplète.

Rapportez **tout le dossier**, en particulier `models/` et `wheelhouse/`.
Les archives `.whl` doivent rester telles quelles, sans extraction.

## Chez vous : installer et démarrer sans Internet

Ouvrez un terminal dans le dossier contenant `app.py` :

```bash
bash installer.sh
bash lancer.sh
```

L'installateur crée un environnement `.venv` dans ce dossier. Par défaut, il
utilise uniquement les paquets de `wheelhouse/` avec `--no-index`. S'ils manquent,
il s'arrête avec une explication, sans téléchargement automatique.

Les composants système nécessaires sont Python 3.12, `python3-venv`,
`python3-tk`, `libportaudio2`, `libx11-6` et `libxtst6`. Plusieurs sont déjà présents
sur Linux Mint. Si l'un manque, préparez aussi son installation avant de vous
retrouver sans connexion. Avec Internet, la commande Mint est :

```bash
sudo apt install python3-venv python3-tk libportaudio2 libx11-6 libxtst6
```

Cette commande est fournie à exécuter seulement si nécessaire ; les scripts ne
lancent pas `apt`. Si vous souhaitez volontairement installer les paquets Python
depuis Internet : `bash installer.sh --en-ligne`.

## Dicter dans une application

1. Sélectionnez un modèle et votre microphone. Tiny ou Base offrent généralement
   le délai le plus court.
2. Placez le curseur dans votre logiciel et appuyez sur **F8**, ou cliquez sur
   **Dicter ici** pour garder le résultat dans cette fenêtre.
3. Attendez l'ouverture du microphone, puis parlez. La transcription démarre
   automatiquement après environ **0,65 seconde sans voix détectée**.
   Il n'y a aucune découpe périodique : une phrase continue reste entière.
4. Le microphone reste ouvert pour la phrase suivante. **F8** ou **Couper le micro**
   termine la session et transcrit les derniers mots encore en attente.

Le collage automatique utilise le curseur actuel, sans vérification de la fenêtre.
Pour un terminal, cochez **Collage dans un terminal (Ctrl+Maj+V)**.
**Annuler la suite** ignore les passages en attente, sans effacer le texte déjà reçu.
La détection de pause utilise le niveau sonore : en présence de bruit constant,
réduisez le bruit ou ajustez le gain du microphone. Une sécurité arrête la capture
après cinq minutes sans pause détectée, sans déclencher une découpe temporelle.

## Résoudre les problèmes courants

- **Modèle absent ou incomplet :** vérifiez les quatre fichiers et leurs noms
  dans `models/tiny/`, `models/base/`, `models/small/` ou `models/medium/`, puis
  actualisez la liste ou relancez l'application. Évitez un sous-dossier supplémentaire.
- **Erreur de chargement ou manque de mémoire :** passez à **tiny**, fermez les
  applications lourdes et vérifiez que les fichiers n'ont pas été tronqués.
- **Le modèle est très lent :** essayez base ou tiny et des phrases plus courtes.
  Charger medium ne garantit pas un meilleur résultat sur chaque phrase.
- **Aucun son :** sélectionnez le bon microphone et vérifiez l'entrée audio dans
  les paramètres de Linux Mint. Le micro doit être accessible à votre session.
- **F8 ou collage indisponible :** utilisez la session **Cinnamon/X11**.
  Sous Wayland, la surveillance globale et le collage automatique peuvent être
  bloqués ; utilisez les boutons de l'application et copiez manuellement le texte.
- **Un autre logiciel intercepte F8 :** désactivez son raccourci concurrent ou
  utilisez les boutons de l'application. F8 n'est pas bloqué par Dictée clavier :
  il peut aussi déclencher l'action F8 de l'autre logiciel.
- **Le collage ne marche pas dans le terminal :** choisissez Ctrl+Maj+V.
  Les applications qui empêchent le collage nécessitent une insertion manuelle.
- **SHA256 incorrect au téléchargement :** supprimez seulement le fichier
  concerné (`model.bin` ou `model.bin.part`) et relancez le téléchargement au
  cybercafé. Les autres modèles restent utilisables.
- **Bibliothèque Python absente :** préparez tout `wheelhouse/` au cybercafé,
  puis relancez `bash installer.sh`. Déplacez le dossier avec le code et les
  modèles ; si vous changez son emplacement, recréez `.venv` avec l'installateur.

## Vérification et sources

Les tests automatisés utilisent des simulations et ne nécessitent aucun modèle :

```bash
python3 -m unittest discover -s tests -v
```

Pour examiner la configuration et les modèles sans ouvrir de fenêtre ni se connecter :

```bash
python3 app.py --diagnostic
```

L'ouverture de l'interface et le collage X11 d'une phrase avec des accents ont
été vérifiés sur votre session. Les tests automatisés couvrent notamment
le rééchantillonnage avec NumPy récupéré du cache local dans un environnement
de test temporaire. La reconnaissance réelle, les performances et le microphone
restent à essayer après installation des dépendances et d'un modèle.
Aucun modèle ni paquet Python n'a été téléchargé pour créer ce projet.

- [faster-whisper : exécution locale et CPU int8](https://github.com/SYSTRAN/faster-whisper)
- [faster-whisper 1.2.1 sur PyPI](https://pypi.org/project/faster-whisper/1.2.1/)
- [pip : préparer des paquets pour une autre plateforme](https://pip.pypa.io/en/stable/cli/pip_download/)
- Les dépôts Systran du tableau donnent les modèles multilingues et les tailles.

## Collage direct et écoute automatique

Le microphone peut démarrer au lancement. Parlez puis dites **« terminé »** à la
fin, suivi d’une pause. Le texte reconnu est copié et le programme simule
**Ctrl+V**, sans détecter de champ, de fenêtre ni utiliser AT-SPI.
Le collage agit là où se trouve votre curseur, comme un collage au clavier.
L’option terminal utilise Ctrl+Maj+V. Choisissez **Coller seulement** ou
**Coller et envoyer (Entrée)** dans l’interface ; le choix est mémorisé.

Après chaque message, l’écoute reprend automatiquement sans clic. **F8** ou
**Couper le micro** arrête explicitement l’écoute. **Annuler la suite** annule
les opérations en attente. Les textes reconnus restent disponibles dans la
fenêtre si le logiciel destinataire n’accepte pas le collage.
Aucun démarrage particulier de VS Code ni passage par Firefox n’est nécessaire.

## Panneau flottant

Le petit panneau vert se déplace en maintenant le bouton gauche de la souris
sur le panneau puis en glissant. Une barre animée et le message « Audio reçu —
transcription en cours… » apparaissent pendant le calcul Whisper. Le panneau
revient à « Dictée active » quand le traitement est fini.

### Tolérance de la commande « terminé »

La commande finale ignore les accents et la casse, et accepte au plus 15 %
d’éditions de caractères par rapport à « termine », rapportées à la longueur
du mot le plus long. « terminer » et « terminée » sont donc acceptés. Le mot
doit rester à la fin du passage. Les différences plus importantes, comme
« interminer », ne sont pas comprises dans cette tolérance. Le panneau de
chargement est compact et utilise une barre animée fine.
