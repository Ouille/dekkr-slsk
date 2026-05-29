# dekkr-slsk

App desktop compagnon pour [DekkR](https://github.com/Ouille/DekkR) — client Soulseek automatisé.

Quand DekkR lui envoie les métadonnées d'un track, `dekkr-slsk` :
1. Recherche le track sur le réseau Soulseek
2. Filtre et score les résultats (format, qualité, durée)
3. Télécharge le meilleur candidat
4. Le fait analyser (dekkr-essentia-bridge local ou backend cloud)
5. Vérifie la cohérence BPM/durée
6. Notifie DekkR si le fichier est valide

## Installation

1. Téléchargez `dekkr-slsk.exe` depuis les [Releases](https://github.com/Ouille/dekkr-slsk/releases)
2. Double-cliquez pour lancer — aucune installation requise
3. Entrez vos identifiants Soulseek et choisissez un dossier de téléchargement
4. Une icône verte apparaît dans la barre système

Vous avez besoin d'un compte Soulseek gratuit : [soulseeknet.org](http://www.soulseeknet.org/)

## Note légale

`dekkr-slsk` est un outil technique de recherche et téléchargement sur le réseau Soulseek.
L'usage de musique protégée par le droit d'auteur relève de la responsabilité exclusive
de l'utilisateur. Ce logiciel ne contient aucun contenu copyrighté.

## Licence

GPL-3.0 — compatible avec [aioslsk](https://github.com/JurgenR/aioslsk) (GPLv3).
