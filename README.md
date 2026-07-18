gg<div align="center">

  <img src="https://github.com/FwSchultz/assets/blob/main/bots/FwS-Bots/Bot.png" alt="logo" width="200" height="auto" />
  <h1>DISCORD-STREAM-BOT</h1>
  
<!-- Badges -->
<p>
  <a href="https://github.com/FwSchultz/dc-streamer-bot/graphs/contributors">
    <img src="https://img.shields.io/github/contributors/FwSchultz/dc-streamer-bot" alt="contributors" />
  </a>
  <a href="">
    <img src="https://img.shields.io/github/last-commit/FwSchultz/dc-streamer-bot" alt="last update" />
  </a>
  <a href="https://github.com/FwSchultz/dc-streamer-bot/network/members">
    <img src="https://img.shields.io/github/forks/FwSchultz/dc-streamer-bot" alt="forks" />
  </a>
  <a href="https://github.com/FwSchultz/dc-streamer-bot/stargazers">
    <img src="https://img.shields.io/github/stars/FwSchultz/dc-streamer-bot" alt="stars" />
  </a>
  <a href="https://github.com/FwSchultz/dc-streamer-bot/issues/">
    <img src="https://img.shields.io/github/issues/FwSchultz/dc-streamer-bot" alt="open issues" />
  </a>
  <a href="https://github.com/FwSchultz/dc-streamer-bot/blob/master/LICENSE">
    <img src="https://img.shields.io/github/license/FwSchultz/dc-streamer-bot.svg" alt="license" />
  </a>
</p>
   
<h4>
  <a href="https://github.com/FwSchultz/dc-streamer-bot">Documentation</a>
  <span> · </span>
  <a href="https://github.com/FwSchultz/dc-streamer-bot/issues/">Report Bug</a>
  <span> · </span>
  <a href="https://github.com/FwSchultz/dc-streamer-bot/issues/">Request Feature</a>
</h4>
</div>

<br />

# Table of Contents

- [About the Project](#about-the-project)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Configuration](#configuration)
- [Permissions](#permissions)
- [Run with Docker Compose](#run-with-docker-compose)
- [Run Locally (without Docker)](#run-locally-without-docker)
- [Slash Commands](#slash-commands)
- [How the Live Check Works](#how-the-live-check-works)
- [Database & Persistence](#database--persistence)
- [Roadmap](#roadmap)
- [License](#license)
- [Contact](#contact)

---

## About the Project

**DISCORD-STREAM-BOT** überwacht eingetragene Streamer auf **Twitch** und **YouTube**.

Sobald ein Streamer live geht, postet der Bot automatisch einen **Embed** in einen definierten Discord-Channel – inklusive:

- Plattform (Twitch / YouTube)
- Streamtitel
- Direktlink zum Stream
- Vorschaubild (Thumbnail)
- Optional: Discord-Mention des Streamers

Streamer werden über Slash-Commands verwaltet und in einer SQLite-Datenbank gespeichert.

---

## Features

- Überwachung von **Twitch**-Streams über die Helix API
- Überwachung von **YouTube**-Livestreams über die YouTube Data API v3  
  - Unterstützt YouTube-Channel-IDs **und** `@Handles` (z.B. `@HandOfBlood`)
- Automatische Live-Embeds mit:
  - Titel
  - Streamlink
  - Thumbnail (mit Check, ob das Bild wirklich erreichbar ist)
  - Discord-Mention (falls Streamer mit Member verknüpft)
- Live-Status wird persistiert, damit nicht bei jedem Restart gespammt wird
- Verwaltung komplett in Discord:
  - Hinzufügen per Modal (`/streamer_add`)
  - Verwalten / Bearbeiten / Löschen über `/streamer_manage`
  - Listing via `/streamer_list`
  - Reset des Live-Status über `/streamer_reset_live`
- SQLite-Datenbank (`streamers.db`)
- Docker-Setup mit `Dockerfile` + `docker-compose.yml`
- Zugriff steuerbar über Discord-Berechtigung **und** Rollen-IDs aus der `.env`

---

## Tech Stack

- **Language:** Python 3.12
- **Libraries:**
  - [`discord.py`](https://github.com/Rapptz/discord.py)
  - [`aiohttp`](https://docs.aiohttp.org/)
  - [`aiosqlite`](https://github.com/omnilib/aiosqlite)
  - [`python-dotenv`](https://github.com/theskumar/python-dotenv)
- **Deployment:**
  - Docker
  - Docker Compose

---

## Project Structure

Beispielhafte Struktur:

```text
dc-streamer-bot/
├─ bot.py
├─ Dockerfile
├─ requirements.txt
├─ docker-compose.yml
├─ .env.example
├─ .env              # deine Konfiguration (nicht committen)
└─ streamers.db      # SQLite-Datenbank (wird automatisch erstellt)
```

---

## Configuration

Alle relevanten Einstellungen laufen über eine `.env`-Datei.

### 1. `.env` aus Vorlage erstellen

```bash
cp .env.example .env
```

### 2. Inhalt von `.env.example`

```env
# Discord Bot Token
DISCORD_TOKEN=dein_discord_bot_token_hier

# Optional: Guild ID für gilden-spezifische Slash-Commands
# 0 oder leer = globale Commands
GUILD_ID=0

# Channel-ID, in dem die Live-Embeds gepostet werden
ANNOUNCE_CHANNEL_ID=123456789012345678

# Twitch API
TWITCH_CLIENT_ID=dein_twitch_client_id
TWITCH_CLIENT_SECRET=dein_twitch_client_secret

# YouTube API
YOUTUBE_API_KEY=dein_youtube_api_key

# Bot
CHECK_INTERVAL_MINUTES=2
LOG_LEVEL=INFO

# Rollen, die zusätzlich zu "Server verwalten" die Commands nutzen dürfen
# Kommagetrennte Liste von Discord-Rollen-IDs, z.B. Admins, Streamer etc.
ALLOWED_ROLE_IDS=111111111111111111,222222222222222222
```

### 3. Parameter erklärt

- `DISCORD_TOKEN`  
  Bot-Token aus dem Discord Developer Portal.

- `GUILD_ID`  
  - `0` oder leer → globale Slash-Commands (für mehrere Server geeignet, Updates dauern etwas länger)  
  - konkrete Guild-ID → Commands werden nur dort registriert, Updates sind schneller.

- `ANNOUNCE_CHANNEL_ID`  
  Discord-Channel-ID, in dem die Live-Meldungen gepostet werden.

- `TWITCH_CLIENT_ID` & `TWITCH_CLIENT_SECRET`  
  Daten aus deiner Twitch Developer Application.

- `YOUTUBE_API_KEY`  
  API-Key aus der Google Cloud Console mit aktivierter **YouTube Data API v3**.
  
- `ALLOWED_ROLE_IDS`  
  Kommagetrennte Liste von Rollen-IDs.

- `CHECK_INTERVAL_MINUTES`  
  Abstand zwischen den Live-Prüfungen in Minuten. Mindestwert: 1.

- `LOG_LEVEL`  
  Protokollierungsstufe, zum Beispiel `INFO` oder `DEBUG`. Jeder Benutzer mit mindestens einer dieser Rollen darf die 
  Streamer-Commands benutzen – auch ohne „Server verwalten“.

---

## Permissions

Die Prüfung der Berechtigung läuft über eine zentrale Funktion im Bot:

1. User mit Server verwalten

	- `Haben immer Zugriff auf alle Slash-Commands des Bots.`

2. User ohne Server verwalten, aber mit Rolle in ALLOWED_ROLE_IDS

	- `Dürfen ebenfalls alle relevanten Commands nutzen (/streamer_add, /streamer_manage, /streamer_list, /streamer_reset_live).`

3. Alle anderen

	- `Erhalten eine Fehlermeldung und können keine Streamer verwalten.`

Damit kannst du z.B. Rollen wie Admins und Streamer freischalten, ohne global Adminrechte vergeben zu müssen.

---

## Run with Docker Compose

### 1. Build

Im Projektordner:

```bash
docker compose build
```

### 2. Starten

```bash
docker compose up -d
```

### 3. Logs ansehen

```bash
docker compose logs -f streambot
```

Erwartete Meldungen u.a.:

- `Datenbank initialisiert`
- `Twitch Token erhalten`
- `ggf. einzelne Logs zu Twitch/YouTube-Anfragen`

### 4. Stoppen & Entfernen

```bash
# Container stoppen
docker compose stop streambot

# Container entfernen (DB bleibt als Datei auf dem Host)
docker compose down
```

---

## Run Locally (without Docker)

Wenn du lieber ohne Container starten willst:

### 1. Virtualenv & Requirements

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. `.env` anlegen und füllen

Siehe Abschnitt [Configuration](#configuration).

### 3. Bot starten

```bash
python bot.py
```

---

## Slash Commands

### `/streamer_add`

Fügt einen Streamer zur Live-Überwachung hinzu.

**Parameter:**

- `discord_user` – optional; Zuordnung zu einem Discord-User (für Mention & Default-Anzeigename)
Nach dem Ausführen öffnet sich ein Modal mit folgenden Feldern:

- `Anzeigename` – frei wählbar, Default = Name des Discord-Users (falls angegeben)
- `twitch_login` – optional; Twitch-Name **ohne** URL, z.B. `pietsmiet`  
- `youtube_channel_id` – optional; YouTube Channel-ID **oder** `@Handle`  
  Beispiele:
  - `@HandOfBlood`
  - `UCxxxxxxxxxxxxxxxxxxxxxx`

Der Bot speichert u.a.:

- `display_name`
- `discord_id` (falls angegeben)
- `twitch_name` (kleingeschrieben)
- `twitch_url`
- `youtube_channel_id`

---

### `/streamer_manage`

Öffnet eine Verwaltungssicht (ephemeral):

- Dropdown mit allen Streamern
- Nach Auswahl:
  - Embed mit Infos:
    - Anzeigename
    - Discord-User (<@id>, falls verknüpft)
    - Twitch-URL
    - YouTube-Channel-ID / Handle
  - Buttons:
    - **Twitch öffnen** (falls Twitch gesetzt)
    - **YouTube öffnen** (Link aus `youtube_channel_id`/`@Handle` gebaut)
    - **Streamer bearbeiten** (Modal mit Textfeldern)
    - **Streamer löschen**

Bearbeiten-Modal:

- Anzeigename
- Twitch-Login
- YouTube Channel-ID / `@Handle`

---

### `/streamer_list`

Zeigt alle eingetragenen Streamer (nur für User mit `Server verwalten`):

- ID
- Anzeigename
- Optional Discord-User (`<@id>`)
- Twitch-Name
- YouTube-Channel-ID / `@Handle`

Beispielausgabe:

```text
**1** – BeispielStreamer (<@123456789012345678>) – Twitch: `pietsmiet`, YouTube: `@HandOfBlood`
**2** – AndererStreamer – Twitch: `irgendwer`
```

---

### `/streamer_reset_live`

Setzt `was_live_twitch` und `was_live_youtube` für **alle** Streamer auf `0`.

Nutzen:

- für Tests und Debugging
- um einen „frischen“ Live-Trigger zu erzwingen, wenn jemand schon live war und bereits ein Embed gepostet wurde.

---

## How the Live Check Works

- Das Prüfintervall wird über `CHECK_INTERVAL_MINUTES` gesteuert (Standard: 2 Minuten).
- Der Bot liest alle Streamer aus `streamers.db`.
- Twitch:
  - Abfrage der Streams über `helix/streams` mit `user_login`.
  - Filter `type == "live"`.
  - Titel + Thumbnail-Template werden ausgewertet.
- YouTube:
  - Wenn `youtube_channel_id` mit `@` beginnt:
    - Auflösung zu echter Channel-ID via `channels.list?forHandle=...`.
  - Mit Channel-ID wird die Live-Suche über `search` ausgeführt:
    - `eventType=live`
    - `type=video`
- Erkennung „neu live“:
  - Wenn `jetzt_live == True` und `was_live == 0` → **neuer Live-Eintrag** → Embed.
  - Danach werden `was_live_twitch` / `was_live_youtube` in der DB auf `1` gesetzt.

Ein Streamer, der gleichzeitig auf Twitch und YouTube live ist, erzeugt zwei Embeds:
eines für „Twitch“, eines für „YouTube“.

---

## Database & Persistence

- SQLite-Datenbank: `streamers.db`
- Tabelle `streamers` enthält u.a.:

  - `id` (PK)
  - `display_name`
  - `discord_id`
  - `twitch_login`
  - `youtube_channel_id`
  - `twitch_url`
  - `youtube_url (aktuell praktisch ungenutzt, bleibt für Kompatibilität im Schema)`
  - `was_live_twitch`
  - `was_live_youtube`

### Docker Volume

In `docker-compose.yml` ist z.B. definiert:

```yaml
services:
  streambot:
    build: .
	image: fwschultz/dc-streamer-bot:1.0.0
    container_name: dc-streamer-bot
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - TZ=Europe/Berlin
    volumes:
      - ./streamers.db:/app/streamers.db
```

Damit:

- liegt die echte Datenbankdatei im Projektordner auf dem Host,
- der Container nutzt sie unter `/app/streamers.db`,
- Backups sind trivial: `cp streamers.db streamers.db.bak`,
- Migration auf einen anderen Server: Code + `.env` + `streamers.db` rüberkopieren.

---

## Roadmap

* [ ] Per-Streamer-Optionen (nur Twitch / nur YouTube)
* [ ] Ping einer konfigurierbaren Rolle (z.B. `@Stream-Notify`)
* [ ] Konfigurierbare Check-Intervalle
* [ ] Mehrsprachige Antworttexte (`de`, `en`)
* [x] Bearbeiten von Streamern via Modal (`/streamer_manage`)
* [x] Docker Compose Support
* [x] YouTube-Handle-Unterstützung (`@Handle`)

---

## License

Dieses Projekt ist unter der **MIT License** lizenziert.  
Details findest du in der Datei [LICENSE](./LICENSE).

---

## Contact

Created by **Fw.Schultz**.

Bei Fragen, Bugs oder Feature-Wünschen:

- GitHub Issues: [https://github.com/FwSchultz/dc-streamer-bot/issues](https://github.com/FwSchultz/dc-streamer-bot/issues)  
- Discord: [Fw.Schultz](https://discordapp.com/users/275297833970565121)
