# ✈️ Chasseur de vols

Alertes Telegram quasi-instantanées sur les vols aller-retour pas chers vers les États-Unis, le Canada,
le Mexique, l'Asie et l'Océanie. Départs possibles : Marseille, Paris, Barcelone, Milan, Madrid,
Londres, Francfort et Amsterdam. Chaque prix inclut le trajet depuis Marseille, et le total est donné pour 2.

| Robot | Fréquence | Ce qu'il fait |
|---|---|---|
| **Flash** | toutes les 10 min | lit 8 sites de chasseurs de deals (FR, ES, IT, DE, UK) → erreurs de prix 🚨, promos flash |
| **Prix** | toutes les heures | scanne une ville de départ × 33 destinations × 11 mois → prix sous le seuil ou anormalement bas |

Telegram ne sonne que quand il y a quelque chose. **Coût : 0 €.**

---

# 🧭 Marche à suivre (environ 30 minutes, une seule fois)

Prends une feuille ou une note : tu vas récupérer **3 codes** à recopier à l'étape 4.

## Étape 1 — Créer ton bot Telegram (5 min)

1. Dans Telegram, cherche **@BotFather** (avec la coche bleue) et ouvre la conversation.
2. Envoie `/newbot`.
3. Il demande un **nom** : par exemple `Chasseur de vols`.
4. Il demande un **identifiant** finissant par `bot` : par exemple `chasseur_vols_lea_bot`. S'il est déjà pris, essaie-en un autre.
5. BotFather répond avec un long code du type `7123456789:AAH...` : c'est le **CODE 1 = TELEGRAM_BOT_TOKEN**. Note-le.
6. Clique sur le lien de ton bot (`t.me/...`) dans sa réponse, appuie sur **Démarrer** et envoie-lui « salut ».
7. Dans ton navigateur, ouvre cette adresse en remplaçant `CODE1` par ton code :
   `https://api.telegram.org/botCODE1/getUpdates`
8. Sur la page, cherche `"chat":{"id":` suivi d'un nombre (ex. `"chat":{"id":512345678`).
   Ce nombre est le **CODE 2 = TELEGRAM_CHAT_ID**. Note-le.
   *(Si la page affiche `"result":[]`, renvoie un message au bot et recharge la page.)*

> 💡 Pour partager les alertes avec la personne qui voyage avec toi : crée un **groupe Telegram** avec
> vous deux et le bot, envoie un message dans le groupe, puis refais le point 7. Utilise l'id du
> groupe (il commence par `-`).

## Étape 2 — Créer ta clé de prix Travelpayouts (5 min, gratuit)

1. Va sur **travelpayouts.com** → **Sign up** (inscription gratuite).
2. Si on te demande ton site ou ton projet, mets par exemple « usage personnel / blog voyage ».
3. Dans le catalogue des programmes, rejoins **Aviasales** (bouton *Join*).
4. Dans ton espace, cherche la rubrique **API** (souvent dans *Profile* ou *Tools → API*).
   Copie le **API token** (une suite de lettres et de chiffres) : c'est le **CODE 3 = TRAVELPAYOUTS_TOKEN**.

## Étape 3 — Mettre le programme sur GitHub (10 min)

1. Crée un compte sur **github.com** (gratuit).
2. En haut à droite, **+** → **New repository**.
   - Nom : `chasseur-de-vols`
   - Choisis **Public**. C'est ce qui rend les vérifications toutes les 10 minutes gratuites et illimitées.
     Seul le code est visible. Tes codes secrets ne le sont pas, ni l'historique des prix.
   - Clique **Create repository**.
3. Sur la page du dépôt vide, clique sur **uploading an existing file**.
4. Dans le Finder, ouvre le dossier du projet et **glisse dans la page** :
   `config.yaml`, `requirements.txt`, `README.md` et le dossier `flightdeals`.
   *(Ne glisse pas `.venv` ni `out` s'ils apparaissent.)* Clique **Commit changes**.
5. Le fichier de planification est caché sur Mac, donc on le crée à la main :
   **Add file** → **Create new file**. Dans le nom, tape exactement :
   `.github/workflows/deals.yml`
   puis colle tout le contenu du fichier `deals.yml` (je te le donne si besoin). Clique **Commit changes**.

## Étape 4 — Donner tes 3 codes à GitHub (3 min)

Dans ton dépôt : **Settings** → menu de gauche **Secrets and variables** → **Actions** →
**New repository secret**. Crée ces 3 secrets, un par un. Le nom doit être exactement celui-ci :

| Name | Secret |
|---|---|
| `TELEGRAM_BOT_TOKEN` | CODE 1 |
| `TELEGRAM_CHAT_ID` | CODE 2 |
| `TRAVELPAYOUTS_TOKEN` | CODE 3 |

## Étape 5 — Allumer et tester (2 min)

1. Onglet **Actions**. Si GitHub demande d'activer les workflows, clique sur le bouton vert.
2. À gauche, **Chasseur de vols** → **Run workflow** → mode `flash` → **Run workflow**.
   Environ 1 minute après, tu reçois sur Telegram les deals des sites (le premier message est long, c'est normal).
3. Refais **Run workflow** avec le mode `prix`. 3 à 5 minutes après, tu reçois les bons prix d'une première ville de départ.
4. Si une ligne devient rouge ❌, clique dessus pour voir l'erreur et envoie-moi une capture.

**C'est fini.** Ensuite tout tourne seul, même Mac éteint.

---

## Bon à savoir

- **Les 2 ou 3 premiers jours**, seuls les prix sous les seuils fixes sont signalés. Ensuite, le système
  connaît le prix habituel de chaque route et repère aussi les baisses de plus de 30 %, typiques des erreurs de prix.
- **Toujours vérifier** sur Google Flights (lien dans chaque alerte) : les prix viennent de recherches faites dans les 48 dernières heures.
- **Erreur de prix 🚨** : réserve vite, avec annulation gratuite sous 24 h si possible (règle des vols vers les USA),
  et attends la confirmation avant de réserver hôtels et trains.
- **Départ d'une autre ville** : ce sont des billets séparés. Prévois d'arriver la veille.
- GitHub peut décaler les passages de quelques minutes aux heures chargées.

## Réglages (fichier `config.yaml`, modifiable directement sur GitHub avec le crayon ✏️)

- **Seuils** (`regions`) : coût total par personne depuis Marseille. `deal` = alerte, `exceptional` = 🚨.
- **Destinations** : une ligne par ville (code IATA, ex. `HNL` pour Honolulu).
- **Villes de départ** (`origins`) : ajoute par exemple `LIS: { name: Lisbonne, positioning_from: MRS }`.
- **Trop ou pas assez d'alertes** : `drop_vs_usual` et `telegram_max_routes`.
