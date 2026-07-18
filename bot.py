import os
import asyncio
import logging
import datetime
from datetime import timezone
from typing import List, Optional
import time  # für Cache-Buster bei Twitch-Thumbnails

import aiohttp
import aiosqlite
import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0")) or None
ANNOUNCE_CHANNEL_ID = int(os.getenv("ANNOUNCE_CHANNEL_ID", "0"))
ALLOWED_ROLE_IDS_RAW = os.getenv("ALLOWED_ROLE_IDS", "")
ALLOWED_ROLE_IDS: set[int] = {
    int(x.strip()) for x in ALLOWED_ROLE_IDS_RAW.split(",") if x.strip().isdigit()
}
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
CHECK_INTERVAL_MINUTES = max(1, int(os.getenv("CHECK_INTERVAL_MINUTES", "2")))


def chunk_list(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]

class StreamBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.http_session: Optional[aiohttp.ClientSession] = None
        self.db: Optional[aiosqlite.Connection] = None
        self.twitch_token: Optional[str] = None

    async def setup_hook(self) -> None:
        timeout = aiohttp.ClientTimeout(total=30)
        self.http_session = aiohttp.ClientSession(timeout=timeout)
        os.makedirs("data", exist_ok=True)
        self.db = await aiosqlite.connect("data/streamers.db")
        await self._init_db()

        # Slash Commands syncen
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logging.info("Commands für Guild %s synchronisiert", GUILD_ID)
        else:
            await self.tree.sync()
            logging.info("Globale Commands synchronisiert")

        # Background-Task starten
        self.check_streams.change_interval(minutes=CHECK_INTERVAL_MINUTES)
        self.check_streams.start()

    async def close(self) -> None:
        if self.http_session:
            await self.http_session.close()
        if self.db:
            await self.db.close()
        await super().close()

    async def _init_db(self) -> None:
        await self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS streamers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                display_name TEXT NOT NULL,
                discord_id INTEGER,
                twitch_login TEXT,
                youtube_channel_id TEXT,
                twitch_url TEXT,
                youtube_url TEXT,
                was_live_twitch INTEGER DEFAULT 0,
                was_live_youtube INTEGER DEFAULT 0
            );
            """
        )
        await self.db.commit()
        logging.info("Datenbank initialisiert")

    async def get_twitch_token(self) -> Optional[str]:
        if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
            return None

        if self.twitch_token:
            return self.twitch_token

        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": TWITCH_CLIENT_ID,
            "client_secret": TWITCH_CLIENT_SECRET,
            "grant_type": "client_credentials",
        }
        async with self.http_session.post(url, params=params) as resp:
            if resp.status != 200:
                logging.error("Twitch Token-Request fehlgeschlagen: %s", resp.status)
                return None
            data = await resp.json()
            self.twitch_token = data.get("access_token")
            logging.info("Twitch Token erhalten")
            return self.twitch_token

    async def fetch_twitch_live(self, logins: List[str]) -> dict:
        """
        Gibt dict {login: {title, thumbnail_url, game_name}} für aktuell live gehende Channels zurück.
        """
        token = await self.get_twitch_token()
        if not token or not logins:
            return {}

        headers = {
            "Client-Id": TWITCH_CLIENT_ID,
            "Authorization": f"Bearer {token}",
        }
        url = "https://api.twitch.tv/helix/streams"

        live_streams: dict[str, dict] = {}

        for chunk in chunk_list(list(set(logins)), 100):
            params = [("user_login", login) for login in chunk]
            async with self.http_session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    logging.error("Twitch Streams-Request fehlgeschlagen: %s", resp.status)
                    continue
                data = await resp.json()
                for stream in data.get("data", []):
                    if stream.get("type") != "live":
                        continue
                    login = stream["user_login"].lower()
                    thumb_template = stream.get("thumbnail_url")
                    thumb_url = None
                    if thumb_template:
                        # {width}x{height} durch feste Größe ersetzen
                        base = thumb_template.replace("{width}x{height}", "1280x720")
                        # Cache-Buster, damit Discord/Twitch nicht ewig ein altes/kaputtes Bild cachen
                        thumb_url = f"{base}?t={int(time.time())}"

                    live_streams[login] = {
                        "title": stream.get("title"),
                        "thumbnail_url": thumb_url,
                        "game_name": stream.get("game_name"),
                    }

        return live_streams

    async def fetch_youtube_live(self, channel_ids: List[str]) -> dict:
        """
        Gibt dict {eingabe_wert: {title, thumbnail_url, video_url}} für aktuell live gehende Channels zurück.

        Unterstützt:
        - reine Channel-IDs (UC...)
        - YouTube-Handles mit @ (z.B. @HandOfBlood)
        """
        if not YOUTUBE_API_KEY or not channel_ids:
            return {}

        live_channels: dict[str, dict] = {}
        base_search_url = "https://www.googleapis.com/youtube/v3/search"
        base_channels_url = "https://www.googleapis.com/youtube/v3/channels"

        for raw_id in set(channel_ids):
            if not raw_id:
                continue

            ident = raw_id.strip()
            canonical_channel_id = None

            # 1) Handle (@Name) -> über channels.list mit forHandle in echte Channel-ID auflösen
            if ident.startswith("@"):
                handle = ident.lstrip("@")
                params_channels = {
                    "part": "id",
                    "forHandle": handle,
                    "key": YOUTUBE_API_KEY,
                    "maxResults": 1,
                }
                async with self.http_session.get(base_channels_url, params=params_channels) as resp:
                    if resp.status != 200:
                        logging.error("YouTube channels.list (forHandle) fehlgeschlagen (%s) für %s", resp.status, ident)
                        continue
                    data = await resp.json()
                    items = data.get("items", [])
                    if not items:
                        logging.error("YouTube: kein Channel für Handle %s gefunden", ident)
                        continue
                    canonical_channel_id = items[0].get("id")
                    if not canonical_channel_id:
                        logging.error("YouTube: Channel-ID für Handle %s fehlt", ident)
                        continue
            else:
                # 2) Ansonsten nehmen wir an, dass es schon eine Channel-ID ist
                canonical_channel_id = ident

            if not canonical_channel_id:
                continue

            # 3) Search-Endpoint mit der echten Channel-ID nutzen
            params_search = {
                "part": "snippet",
                "channelId": canonical_channel_id,
                "eventType": "live",
                "type": "video",
                "key": YOUTUBE_API_KEY,
                "maxResults": 1,
            }
            async with self.http_session.get(base_search_url, params=params_search) as resp:
                if resp.status != 200:
                    logging.error(
                        "YouTube search (live) fehlgeschlagen (%s) für %s (kanonisch: %s)",
                        resp.status, raw_id, canonical_channel_id
                    )
                    continue
                data = await resp.json()
                items = data.get("items", [])
                if not items:
                    # Channel ist einfach gerade nicht live
                    continue

                item = items[0]
                snippet = item.get("snippet", {})
                thumbnails = snippet.get("thumbnails", {})
                thumb_url = (
                    thumbnails.get("high", {}).get("url")
                    or thumbnails.get("medium", {}).get("url")
                    or thumbnails.get("default", {}).get("url")
                )

                video_id = item.get("id", {}).get("videoId")
                video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None

                # key = raw_id (also das, was in der DB steht)
                live_channels[raw_id] = {
                    "title": snippet.get("title"),
                    "thumbnail_url": thumb_url,
                    "video_url": video_url,
                }

        return live_channels

    async def url_usable(self, url: str) -> bool:
        """
        Prüft grob, ob eine Bild-URL nutzbar ist (Status 2xx).
        Verhindert graues Kamera-Icon bei kaputten Thumbnails.
        """
        if not url:
            return False

        try:
            async with self.http_session.head(url) as resp:
                if 200 <= resp.status < 300:
                    return True
                logging.warning("Thumbnail nicht nutzbar (%s) für %s", resp.status, url)
                return False
        except Exception as e:
            logging.warning("Fehler bei Thumbnail-Check für %s: %s", url, e)
            return False

    @tasks.loop(minutes=2)
    async def check_streams(self):
        if not self.db:
            return

        # Streamer aus DB holen
        async with self.db.execute(
            "SELECT id, display_name, discord_id, twitch_login, youtube_channel_id, "
            "twitch_url, was_live_twitch, was_live_youtube FROM streamers"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return

        twitch_logins = [r[3] for r in rows if r[3]]
        youtube_channels = [r[4] for r in rows if r[4]]

        # Aktuellen Live-Status inkl. Thumbnails abfragen
        live_twitch = await self.fetch_twitch_live(twitch_logins)     # dict login -> info
        live_yt = await self.fetch_youtube_live(youtube_channels)     # dict channel_id -> info

        live_twitch_set = set(live_twitch.keys())
        live_yt_set = set(live_yt.keys())

        new_live_entries = []
        updates = []

        for row in rows:
            (
                sid,
                display_name,
                discord_id,
                twitch_login,
                youtube_channel_id,
                twitch_url,
                was_live_twitch,
                was_live_youtube,
            ) = row

            login_norm = twitch_login.lower() if twitch_login else None

            now_live_twitch = bool(login_norm and login_norm in live_twitch_set)
            now_live_youtube = bool(youtube_channel_id and youtube_channel_id in live_yt_set)

            # Twitch: neue Lives erkennen
            if now_live_twitch and not was_live_twitch:
                info = live_twitch.get(login_norm, {})
                new_live_entries.append(
                    {
                        "display_name": display_name,
                        "discord_id": discord_id,
                        "platform": "Twitch",
                        "url": twitch_url or (f"https://twitch.tv/{login_norm}" if login_norm else None),
                        "title": info.get("title"),
                        "thumbnail_url": info.get("thumbnail_url"),
                    }
                )

            # YouTube: neue Lives erkennen
            if now_live_youtube and not was_live_youtube:
                info = live_yt.get(youtube_channel_id, {})
                new_live_entries.append(
                    {
                        "display_name": display_name,
                        "discord_id": discord_id,
                        "platform": "YouTube",
                        "url": info.get("video_url"),
                        "title": info.get("title"),
                        "thumbnail_url": info.get("thumbnail_url"),
                    }
                )

            updates.append(
                (
                    1 if now_live_twitch else 0,
                    1 if now_live_youtube else 0,
                    sid,
                )
            )

        # DB-Status aktualisieren
        await self.db.executemany(
            "UPDATE streamers SET was_live_twitch = ?, was_live_youtube = ? WHERE id = ?",
            updates,
        )
        await self.db.commit()

        # Ankündigungs-Embeds schicken (ein Embed pro neuen Live-Eintrag)
        if new_live_entries and ANNOUNCE_CHANNEL_ID:
            channel = self.get_channel(ANNOUNCE_CHANNEL_ID)
            if channel is None:
                try:
                    channel = await self.fetch_channel(ANNOUNCE_CHANNEL_ID)
                except Exception as e:
                    logging.error("Announcement-Channel nicht gefunden: %s", e)
                    return

            for entry in new_live_entries:
                logging.info(
                    "Live-Embed: %s (%s) url=%s thumb=%s title=%s",
                    entry["display_name"],
                    entry["platform"],
                    entry["url"],
                    entry.get("thumbnail_url"),
                    entry.get("title"),
                )

                description_lines = []

                # Streamtitel zuerst, falls vorhanden
                if entry.get("title"):
                    description_lines.append(f"**{entry['title']}**")

                if entry["url"]:
                    description_lines.append(f"[Stream ansehen]({entry['url']})")
                if entry["discord_id"]:
                    description_lines.append(f"Discord: <@{entry['discord_id']}>")

                embed = discord.Embed(
                    title=f"{entry['display_name']} ist live auf {entry['platform']}",
                    description="\n".join(description_lines) or None,
                    color=discord.Color.purple(),
                    timestamp=datetime.datetime.now(timezone.utc),
                )

                # Vorschaubild setzen, wenn vorhanden und tatsächlich nutzbar
                thumb = entry.get("thumbnail_url")
                if thumb and await self.url_usable(thumb):
                    embed.set_image(url=thumb)

                await channel.send(embed=embed)

    @check_streams.before_loop
    async def before_check_streams(self) -> None:
        await self.wait_until_ready()

    @check_streams.error
    async def check_streams_error(self, error: Exception) -> None:
        logging.exception("Fehler im Live-Check", exc_info=error)


bot = StreamBot()


def is_admin(interaction: discord.Interaction) -> bool:
    """
    Berechtigung für Bot-Commands:
    - User mit 'Server verwalten' sind immer erlaubt
    - zusätzlich: jede Rolle in ALLOWED_ROLE_IDS
    """
    user = interaction.user

    # 1) User mit 'Server verwalten' immer durchlassen
    if user.guild_permissions.manage_guild:
        return True

    # 2) Wenn keine Rollen konfiguriert sind, nur 'Server verwalten' erlauben
    if not ALLOWED_ROLE_IDS:
        return False

    # 3) Rollen gegen ALLOWED_ROLE_IDS prüfen
    if isinstance(user, discord.Member):
        return any(role.id in ALLOWED_ROLE_IDS for role in user.roles)

    return False


class StreamerManageView(discord.ui.View):
    """
    View mit Dropdown zur Streamer-Auswahl und optionalen Aktions-Buttons.
    """
    def __init__(self, bot: StreamBot, options: list[discord.SelectOption], selected_streamer: dict | None = None):
        super().__init__(timeout=300)
        self.bot = bot
        self.options = options
        self.selected_streamer = selected_streamer

        # Dropdown einbauen
        self.add_item(StreamerSelect(bot=self.bot, options=self.options))

        # Falls schon ein Streamer ausgewählt ist -> Buttons hinzufügen
        if self.selected_streamer:
            sid = self.selected_streamer["id"]
            display_name = self.selected_streamer["display_name"]
            twitch_url = self.selected_streamer["twitch_url"]
            youtube_channel_id = self.selected_streamer["youtube_channel_id"]

            if twitch_url:
                self.add_item(
                    discord.ui.Button(
                        label="Twitch öffnen",
                        style=discord.ButtonStyle.link,
                        url=twitch_url,
                    )
                )

            youtube_link = None
            if youtube_channel_id:
                ident = youtube_channel_id.strip()
                if ident.startswith("@"):
                    youtube_link = f"https://www.youtube.com/{ident}"
                else:
                    youtube_link = f"https://www.youtube.com/channel/{ident}"

            if youtube_link:
                self.add_item(
                    discord.ui.Button(
                        label="YouTube öffnen",
                        style=discord.ButtonStyle.link,
                        url=youtube_link,
                    )
                )

            # Edit-Button
            self.add_item(EditStreamerButton(bot=self.bot, streamer_id=sid))
            # Delete-Button
            self.add_item(DeleteStreamerButton(bot=self.bot, streamer_id=sid, streamer_name=display_name))


class StreamerSelect(discord.ui.Select):
    """
    Dropdown zur Auswahl eines Streamers.
    """
    def __init__(self, bot: StreamBot, options: list[discord.SelectOption]):
        super().__init__(
            placeholder="Streamer auswählen …",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        streamer_id = int(self.values[0])

        # Streamer-Daten aus DB holen
        async with self.bot.db.execute(
            """
            SELECT id, display_name, discord_id, twitch_url, youtube_channel_id
            FROM streamers
            WHERE id = ?
            """,
            (streamer_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "Streamer existiert nicht mehr.",
                ephemeral=True,
            )
            return

        sid, display_name, discord_id, twitch_url, youtube_channel_id = row

        # Embed mit Details
        embed = discord.Embed(
            title=f"Streamer verwalten: {display_name}",
            color=discord.Color.blurple(),
        )
        if discord_id:
            embed.add_field(name="Discord", value=f"<@{discord_id}>", inline=False)
        if twitch_url:
            embed.add_field(name="Twitch", value=twitch_url, inline=False)
        if youtube_channel_id:
            embed.add_field(name="YouTube", value=youtube_channel_id, inline=False)

        # Optionen neu aufbauen
        async with self.bot.db.execute(
            "SELECT id, display_name FROM streamers ORDER BY display_name"
        ) as cursor:
            rows = await cursor.fetchall()

        new_options = [
            discord.SelectOption(
                label=name,
                description=f"ID {sid_}",
                value=str(sid_),
            )
            for sid_, name in rows
        ]

        selected_streamer = {
            "id": sid,
            "display_name": display_name,
            "twitch_url": twitch_url,
            "youtube_channel_id": youtube_channel_id,
        }

        new_view = StreamerManageView(bot=self.bot, options=new_options, selected_streamer=selected_streamer)

        await interaction.response.edit_message(
            content="Streamer verwalten:",
            embed=embed,
            view=new_view,
        )


class DeleteStreamerButton(discord.ui.Button):
    def __init__(self, bot: StreamBot, streamer_id: int, streamer_name: str):
        super().__init__(label="Streamer löschen", style=discord.ButtonStyle.danger)
        self.bot = bot
        self.streamer_id = streamer_id
        self.streamer_name = streamer_name

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Du brauchst `Server verwalten`, um Streamer zu löschen.",
                ephemeral=True,
            )
            return

        # Streamer wirklich löschen
        async with self.bot.db.execute(
            "SELECT display_name FROM streamers WHERE id = ?",
            (self.streamer_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "Streamer existiert bereits nicht mehr.",
                ephemeral=True,
            )
            return

        display_name = row[0]

        await self.bot.db.execute(
            "DELETE FROM streamers WHERE id = ?",
            (self.streamer_id,),
        )
        await self.bot.db.commit()

        # Neue Options-Liste holen
        async with self.bot.db.execute(
            "SELECT id, display_name FROM streamers ORDER BY display_name"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            await interaction.response.edit_message(
                content=f"Streamer **{display_name}** gelöscht. Es sind keine Streamer mehr eingetragen.",
                embed=None,
                view=None,
            )
            return

        options = [
            discord.SelectOption(
                label=name,
                description=f"ID {sid_}",
                value=str(sid_),
            )
            for sid_, name in rows
        ]

        new_view = StreamerManageView(bot=self.bot, options=options, selected_streamer=None)

        await interaction.response.edit_message(
            content=f"Streamer **{display_name}** gelöscht. Wähle einen anderen Streamer:",
            embed=None,
            view=new_view,
        )


class AddStreamerModal(discord.ui.Modal):
    def __init__(self, bot: StreamBot, discord_user: Optional[discord.Member]):
        super().__init__(title="Streamer hinzufügen")
        self.bot = bot
        self.discord_user = discord_user

        default_display = discord_user.display_name if discord_user else ""

        self.display_name_input = discord.ui.TextInput(
            label="Anzeigename",
            default=default_display,
            required=True,
            max_length=100,
        )
        self.twitch_login_input = discord.ui.TextInput(
            label="Twitch-Name (ohne URL)",
            default="",
            required=False,
            max_length=50,
        )
        self.youtube_channel_id_input = discord.ui.TextInput(
            label="YouTube Channel-ID oder @Handle",
            default="",
            required=False,
            max_length=100,
        )

        self.add_item(self.display_name_input)
        self.add_item(self.twitch_login_input)
        self.add_item(self.youtube_channel_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.bot.db:
            await interaction.response.send_message(
                "Datenbank nicht bereit.",
                ephemeral=True,
            )
            return

        display_name = str(self.display_name_input).strip()
        twitch_login = str(self.twitch_login_input).strip() or None
        youtube_channel_id = str(self.youtube_channel_id_input).strip() or None

        if twitch_login:
            twitch_login = twitch_login.lower()
            twitch_url = f"https://twitch.tv/{twitch_login}"
        else:
            twitch_url = None

        discord_id = self.discord_user.id if self.discord_user else None

        await self.bot.db.execute(
            """
            INSERT INTO streamers (
                display_name, discord_id, twitch_login, youtube_channel_id,
                twitch_url
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                display_name,
                discord_id,
                twitch_login,
                youtube_channel_id,
                twitch_url,
            ),
        )
        await self.bot.db.commit()

        await interaction.response.send_message(
            f"Streamer **{display_name}** wurde hinzugefügt.",
            ephemeral=True,
        )


class EditStreamerModal(discord.ui.Modal):
    def __init__(
        self,
        bot: StreamBot,
        streamer_id: int,
        display_name: str,
        twitch_login: Optional[str],
        youtube_channel_id: Optional[str],
    ):
        super().__init__(title="Streamer bearbeiten")
        self.bot = bot
        self.streamer_id = streamer_id

        self.display_name_input = discord.ui.TextInput(
            label="Anzeigename",
            default=display_name,
            required=True,
            max_length=100,
        )
        self.twitch_login_input = discord.ui.TextInput(
            label="Twitch-Name (ohne URL)",
            default=twitch_login or "",
            required=False,
            max_length=50,
        )
        self.youtube_channel_id_input = discord.ui.TextInput(
            label="YouTube Channel-ID oder @Handle",
            default=youtube_channel_id or "",
            required=False,
            max_length=100,
        )

        self.add_item(self.display_name_input)
        self.add_item(self.twitch_login_input)
        self.add_item(self.youtube_channel_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_display_name = str(self.display_name_input).strip()
        new_twitch_login = str(self.twitch_login_input).strip() or None
        new_youtube_channel_id = str(self.youtube_channel_id_input).strip() or None

        if new_twitch_login:
            new_twitch_login = new_twitch_login.lower()
            new_twitch_url = f"https://twitch.tv/{new_twitch_login}"
        else:
            new_twitch_url = None

        if not self.bot.db:
            await interaction.response.send_message(
                "Datenbank nicht bereit.",
                ephemeral=True,
            )
            return

        await self.bot.db.execute(
            """
            UPDATE streamers
            SET display_name = ?, twitch_login = ?, youtube_channel_id = ?,
                twitch_url = ?
            WHERE id = ?
            """,
            (
                new_display_name,
                new_twitch_login,
                new_youtube_channel_id,
                new_twitch_url,
                self.streamer_id,
            ),
        )
        await self.bot.db.commit()

        await interaction.response.send_message(
            f"Streamer **{new_display_name}** wurde aktualisiert.",
            ephemeral=True,
        )


class EditStreamerButton(discord.ui.Button):
    def __init__(self, bot: StreamBot, streamer_id: int):
        super().__init__(label="Streamer bearbeiten", style=discord.ButtonStyle.primary)
        self.bot = bot
        self.streamer_id = streamer_id

    async def callback(self, interaction: discord.Interaction):
        if not is_admin(interaction):
            await interaction.response.send_message(
                "Du hast keine Berechtigung, diesen Befehl zu nutzen.",
                ephemeral=True,
            )
            return

        if not self.bot.db:
            await interaction.response.send_message(
                "Datenbank nicht bereit.",
                ephemeral=True,
            )
            return

        async with self.bot.db.execute(
            """
            SELECT display_name, twitch_login, youtube_channel_id
            FROM streamers
            WHERE id = ?
            """,
            (self.streamer_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await interaction.response.send_message(
                "Streamer existiert nicht mehr.",
                ephemeral=True,
            )
            return

        display_name, twitch_login, youtube_channel_id = row

        modal = EditStreamerModal(
            bot=self.bot,
            streamer_id=self.streamer_id,
            display_name=display_name,
            twitch_login=twitch_login,
            youtube_channel_id=youtube_channel_id,
        )
        await interaction.response.send_modal(modal)


@bot.tree.command(name="streamer_add", description="Streamer zur Live-Überwachung hinzufügen")
@app_commands.describe(
    discord_user="Zuordnung zu einem Discord-User (optional)",
)
async def streamer_add(
    interaction: discord.Interaction,
    discord_user: Optional[discord.Member] = None,
):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Du hast keine Berechtigung, diesen Befehl zu nutzen.",
            ephemeral=True,
        )
        return

    if not bot.db:
        await interaction.response.send_message("Datenbank nicht bereit.", ephemeral=True)
        return

    modal = AddStreamerModal(bot=bot, discord_user=discord_user)
    await interaction.response.send_modal(modal)


@bot.tree.command(name="streamer_manage", description="Streamer per Dropdown verwalten")
async def streamer_manage(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Du brauchst `Server verwalten`, um Streamer zu verwalten.",
            ephemeral=True,
        )
        return

    if not bot.db:
        await interaction.response.send_message(
            "Datenbank nicht bereit.",
            ephemeral=True,
        )
        return

    async with bot.db.execute(
        "SELECT id, display_name FROM streamers ORDER BY display_name"
    ) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "Es sind noch keine Streamer eingetragen.",
            ephemeral=True,
        )
        return

    options = [
        discord.SelectOption(
            label=name,
            description=f"ID {sid}",
            value=str(sid),
        )
        for sid, name in rows
    ]

    view = StreamerManageView(bot=bot, options=options, selected_streamer=None)

    await interaction.response.send_message(
        "Streamer verwalten:",
        view=view,
        ephemeral=True,
    )


@bot.tree.command(name="streamer_list", description="Alle überwachten Streamer anzeigen")
async def streamer_list(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Du brauchst `Server verwalten`, um Streamer zu sehen.",
            ephemeral=True,
        )
        return

    if not bot.db:
        await interaction.response.send_message("Datenbank nicht bereit.", ephemeral=True)
        return

    async with bot.db.execute(
        "SELECT id, display_name, discord_id, twitch_login, youtube_channel_id FROM streamers"
    ) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "Es sind noch keine Streamer eingetragen.",
            ephemeral=True,
        )
        return

    lines = []
    for sid, name, discord_id, twitch_login, yt_id in rows:
        parts = [f"**{sid}** – {name}"]
        if discord_id:
            parts.append(f"(<@{discord_id}>)")
        sub = []
        if twitch_login:
            sub.append(f"Twitch: `{twitch_login}`")
        if yt_id:
            sub.append(f"YouTube: `{yt_id}`")
        if sub:
            parts.append(" – " + ", ".join(sub))
        lines.append(" ".join(parts))

    await interaction.response.send_message(
        "\n".join(lines),
        ephemeral=True,
    )


@bot.tree.command(name="streamer_reset_live", description="Live-Status aller Streamer zurücksetzen")
async def streamer_reset_live(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Du brauchst `Server verwalten`, um den Live-Status zurückzusetzen.",
            ephemeral=True,
        )
        return

    if not bot.db:
        await interaction.response.send_message(
            "Datenbank nicht bereit.",
            ephemeral=True,
        )
        return

    await bot.db.execute(
        "UPDATE streamers SET was_live_twitch = 0, was_live_youtube = 0"
    )
    await bot.db.commit()

    await interaction.response.send_message(
        "Live-Status aller Streamer wurde zurückgesetzt. "
        "Wenn jetzt jemand bereits live ist, wird beim nächsten Check wieder ein Embed gepostet.",
        ephemeral=True,
    )

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("DISCORD_TOKEN in .env fehlt")
    bot.run(DISCORD_TOKEN)
