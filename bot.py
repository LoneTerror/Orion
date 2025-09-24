# --- Import Libraries ---
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
import os
from dotenv import load_dotenv
import concurrent.futures
import aiohttp
import re
import time
import json
from datetime import datetime
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# Load environment variables
load_dotenv()

# --- Fix yt_dlp bug_reports_message lambda issue ---
def no_bug_report_message(*args, **kwargs):
    return ''
yt_dlp.utils.bug_reports_message = no_bug_report_message

# --- Configuration ---
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
SPOTIPY_CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')

SONG_LOG_FILE = 'song_log.json'
EVENT_LOG_FILE = 'event_log.json'

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

# UPDATED: Bot initialization for slash commands
bot = commands.Bot(command_prefix=" ", intents=intents)

# REMOVED: The line "tree = app_commands.CommandTree(bot)" is no longer needed.
# The bot creates its own tree at "bot.tree".

# --- Globals ---
music_queues = {}
loop_states = {}             
current_song_info = {}
context_for_guild = {}
current_playing_messages = {}
executor = concurrent.futures.ThreadPoolExecutor()

# Spotify Client Manager
sp = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(
    client_id=SPOTIPY_CLIENT_ID,
    client_secret=SPOTIPY_CLIENT_SECRET
))

yt_dlp_options = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "auto",
    "extract_flat": False,
    "cookiefile": "cookies.txt"
}

async def extract_info_async(url: str):
    def blocking():
        with yt_dlp.YoutubeDL(yt_dlp_options) as ydl:
            return ydl.extract_info(url, download=False)
    return await asyncio.get_event_loop().run_in_executor(executor, blocking)

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

# --- Logging Helper Functions ---
def log_to_json(file_path, data):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            log_list = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        log_list = []
    log_list.append(data)
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(log_list, f, indent=4)

def log_song(song_data):
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'guild_name': song_data.get('guild_name'),
        'guild_id': song_data.get('guild_id'),
        'title': song_data.get('title'),
        'url': song_data.get('original_url'),
        'requester_name': song_data.get('requester_name'),
        'requester_id': song_data.get('requester_id'),
    }
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] SONG: '{log_entry['title']}' requested by {log_entry['requester_name']} in '{log_entry['guild_name']}'")
    log_to_json(SONG_LOG_FILE, log_entry)

def log_event(event_data):
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'guild_name': event_data.get('guild_name'),
        'guild_id': event_data.get('guild_id'),
        'event_type': event_data.get('event'),
        'user_name': event_data.get('user_name'),
        'user_id': event_data.get('user_id'),
    }
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] EVENT: {log_entry['user_name']} triggered {log_entry['event_type']} in '{log_entry['guild_name']}'")
    log_to_json(EVENT_LOG_FILE, log_entry)

# --- Helper Functions ---
def create_progress_bar(current_sec, total_sec, bar_length=20):
    if total_sec is None or total_sec == 0:
        return "LIVE"
    filled = int(bar_length * current_sec // total_sec)
    dot_position = min(max(0, filled), bar_length - 1)
    bar = "▬" * dot_position + "🔘" + "▬" * (bar_length - dot_position - 1)
    return bar

def format_time(seconds):
    if seconds is None or seconds == 0:
        return "LIVE"
    minutes, seconds = divmod(seconds, 60)
    return f"{int(minutes):02}:{int(seconds):02}"

async def extract_title(url: str):
    try:
        info = await extract_info_async(url)
        if 'entries' in info and info.get('_type') == 'playlist' and info.get('entries'):
            return info['entries'][0].get('title', url)
        return info.get('title', url)
    except Exception:
        return url

# --- Regex for URL detection ---
YOUTUBE_URL_REGEX = re.compile(r"(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+")
SOUNDCLOUD_URL_REGEX = re.compile(r"https?://(www\.)?soundcloud\.com/.+")
SPOTIFY_URL_REGEX = re.compile(r"https://open\.spotify\.com/(track|album|playlist)/[a-zA-Z0-9]+")

# --- API Search Functions ---
async def search_youtube_video(query):
    async with aiohttp.ClientSession() as session:
        params = {"part": "snippet", "q": query, "type": "video", "maxResults": 1, "key": YOUTUBE_API_KEY}
        async with session.get("https://www.googleapis.com/youtube/v3/search", params=params) as resp:
            data = await resp.json()
            if "items" in data and data["items"]:
                video_id = data["items"][0]["id"]["videoId"]
                return f"https://www.youtube.com/watch?v={video_id}"
            return None
        
        
async def get_spotify_track_info(spotify_url):
    try:
        if "track" in spotify_url:
            track_id = spotify_url.split('/')[-1].split('?')[0]
            track = sp.track(track_id)
            # Add "audio" to the search query for single tracks
            return f"{track['name']} {track['artists'][0]['name']} audio"
            
        elif "album" in spotify_url:
            album_id = spotify_url.split('/')[-1].split('?')[0]
            album_tracks = sp.album_tracks(album_id)
            # Add "audio" to the search query for album tracks
            return [f"{item['name']} {item['artists'][0]['name']} audio" for item in album_tracks['items']]

        elif "playlist" in spotify_url:
            playlist_id = spotify_url.split('/')[-1].split('?')[0]
            track_list = []
            
            response = sp.playlist_items(playlist_id)
            
            while True:
                for item in response['items']:
                    if item.get('track') and item['track'].get('name') and item['track'].get('artists'):
                        track = item['track']
                        # Add "audio" to the search query for playlist tracks
                        track_list.append(f"{track['name']} {track['artists'][0]['name']} audio")
                
                if response['next']:
                    response = sp.next(response)
                else:
                    break
                    
            return track_list
            
    except Exception as e:
        print(f"[ERROR] Could not get Spotify track info for {spotify_url}: {e}")
    return None


# --- Playback Functions ---
def play_next_callback(ctx, error):
    if error:
        print(f"[DEBUG] Player error: {error}")
        bot.loop.create_task(ctx.send(f"Playback error: {error}"))
    
    guild_id = ctx.guild.id
    
    if loop_states.get(guild_id, False):
        song_to_loop = current_song_info.get(guild_id)
        if song_to_loop:
            music_queues.setdefault(guild_id, []).insert(0, song_to_loop)

    bot.loop.create_task(play_next(ctx))


async def update_progress_task(ctx, now_playing_msg, title, duration, song_start_time, vc, embed_template, view):
    try:
        original_message_id = now_playing_msg.id
        while vc.is_playing() and vc.source and current_playing_messages.get(ctx.guild.id) and current_playing_messages[ctx.guild.id].id == original_message_id:
            elapsed = int(time.monotonic() - song_start_time)
            if duration is not None and duration > 0 and elapsed >= duration + 2:
                break
            
            progress = create_progress_bar(elapsed, duration)
            current_embed = embed_template.copy()
            
            original_description_lines = embed_template.description.split('\n')
            progress_line_start_index = -1
            for i, line in enumerate(original_description_lines):
                if '`' in line and '/' in line:
                    progress_line_start_index = i
                    break
            
            if progress_line_start_index != -1:
                static_info_part = "\n".join(original_description_lines[:progress_line_start_index])
                current_embed.description = (f"{static_info_part}\n\n" f"`{format_time(elapsed)} / {format_time(duration)}`\n{progress}")
            
            try:
                await now_playing_msg.edit(embed=current_embed, view=view)
            except discord.errors.NotFound:
                break
            await asyncio.sleep(1)

        if current_playing_messages.get(ctx.guild.id) and current_playing_messages[ctx.guild.id].id == original_message_id and not vc.is_playing() and not vc.is_paused():
             await now_playing_msg.edit(view=None)
    except Exception as e:
        print(f"[ERROR] Error in update_progress_task: {e}")

async def play_next(ctx):
    guild_id = ctx.guild.id
    if guild_id in current_playing_messages:
        try:
            old_msg = current_playing_messages.pop(guild_id)
            await old_msg.delete()
        except (discord.errors.NotFound, AttributeError):
            pass

    if guild_id in music_queues and music_queues[guild_id]:
        song_data = music_queues[guild_id].pop(0)
        current_song_info[guild_id] = song_data
        url = song_data['url']
        requester_id = song_data['requester']['id']
        try:
            info = await extract_info_async(url)
            if 'entries' in info and len(info['entries']) > 0:
                info = info['entries'][0]

            audio_url = info['url']
            title = info.get('title', 'Unknown Title')
            thumbnail = info.get('thumbnail', None)
            duration = info.get('duration')
            artist = info.get('artist') or info.get('uploader') or "Unknown Artist"

            source = discord.FFmpegPCMAudio(audio_url, **ffmpeg_options)
            vc = ctx.voice_client
            if vc.is_playing() or vc.is_paused():
                vc.stop()

            vc.play(source, after=lambda e: play_next_callback(ctx, e))

            log_song({
                'guild_name': ctx.guild.name, 'guild_id': ctx.guild.id, 'title': title,
                'original_url': url, 'requester_name': song_data['requester']['name'],
                'requester_id': requester_id
            })

            progress_bar = create_progress_bar(0, duration)

            duration_str = format_time(duration) if duration else "LIVE"

            description_text = (
                f"**{title}**\n\n"
                f"<:Orion_User:1389189744625188884> **Requested by:** <@{requester_id}>\n"
                f"<:Orion_Timer:1386211890774151219> **Music Duration:** {duration_str}\n"
                f"<:Orion_Partner:1386212658453151815> **Music Author:** {artist}\n\n"
                f"`00:00 / {duration_str}`\n{progress_bar}"
            )

            embed = discord.Embed(
                title="<a:Orion_VinylRecord:1386211619410804756>    Now Playing",
                description=description_text,
                color=discord.Color.green()
            )
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)

            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="⏸ Pause", style=discord.ButtonStyle.primary, custom_id="pause"))
            view.add_item(discord.ui.Button(label="▶ Resume", style=discord.ButtonStyle.success, custom_id="resume"))
            view.add_item(discord.ui.Button(label="⏭ Skip", style=discord.ButtonStyle.secondary, custom_id="skip"))
            view.add_item(discord.ui.Button(label="📜 Queue", style=discord.ButtonStyle.secondary, custom_id="queue"))
            view.add_item(discord.ui.Button(label="⏹ Disconnect", style=discord.ButtonStyle.danger, custom_id="disconnect"))

            now_playing_msg = await ctx.send(embed=embed, view=view)
            current_playing_messages[guild_id] = now_playing_msg
            
            start_time = time.monotonic()
            bot.loop.create_task(update_progress_task(ctx, now_playing_msg, title, duration, start_time, vc, embed, view))

        except Exception as e:
            print(f"[ERROR] Playback error for {url}: {e}")
            await ctx.send(f"Error playing track: {e}")
            await play_next(ctx)
    else:
        await ctx.send("The queue has finished. Add more songs or use `/disconnect`.")

async def queue_playlist_tracks_background(interaction, entries, guild_id, requester_info, playlist_title):
    """Asynchronously queues the remaining tracks from a YouTube/SoundCloud playlist."""
    urls_to_add = []
    for entry in entries:
        title = entry.get('title', 'Unknown Title')
        urls_to_add.append({'url': entry['url'], 'title': title, 'requester': requester_info})

    if urls_to_add:
        music_queues[guild_id].extend(urls_to_add)
        # Send a quiet followup message to confirm the background task is done.
        await interaction.followup.send(f"✅ Finished queuing {len(urls_to_add)} more tracks from **{playlist_title}**.", ephemeral=True)

async def queue_spotify_tracks_background(interaction, track_queries, guild_id, requester_info):
    """Asynchronously searches YouTube for Spotify tracks and queues them."""
    urls_to_add = []
    for track_query in track_queries:
        youtube_url = await search_youtube_video(track_query)
        if youtube_url:
            # Use the Spotify track name as the title for speed, which is good enough for the queue.
            urls_to_add.append({'url': youtube_url, 'title': track_query, 'requester': requester_info})

    if urls_to_add:
        music_queues[guild_id].extend(urls_to_add)
        await interaction.followup.send(f"✅ Finished queuing {len(urls_to_add)} more tracks from Spotify.", ephemeral=True)

# --- Events ---
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'[INFO] Logged in as {bot.user} (ID: {bot.user.id})')
    # CORRECTED: Use bot.tree to fetch commands
    print(f'[INFO] Synced {len(await bot.tree.fetch_commands())} slash commands.')

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id:
        return
    vc = member.guild.voice_client
    if not vc:
        return
    if len(vc.channel.members) == 1:
        print(f"[DEBUG] Bot is now alone in '{vc.channel.name}' but will remain connected.")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id")
    vc = interaction.guild.voice_client
    
    log_event({
        'guild_name': interaction.guild.name, 'guild_id': interaction.guild.id,
        'event': f"{custom_id}_button", 'user_name': interaction.user.display_name,
        'user_id': interaction.user.id
    })

    if not vc:
        await interaction.response.send_message("I'm not connected to a voice channel.", ephemeral=True)
        return


    if custom_id == "pause":
        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("Paused.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)


    elif custom_id == "resume":
        if vc.is_paused():
            vc.resume()
            await interaction.response.send_message("Resumed.", ephemeral=True)
        else:
            await interaction.response.send_message("Already playing.", ephemeral=True)


    elif custom_id == "skip":
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await interaction.response.send_message("Skipped.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to skip.", ephemeral=True)


    elif custom_id == "queue":
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        queue = music_queues.get(guild_id, [])
        description = ""
        if queue:
            # FIXED: Read the pre-fetched title directly from the item dictionary
            description = "\n".join(f"**{i+1}.** {item['title']}" for i, item in enumerate(queue[:10]))
            if len(queue) > 10:
                description += f"\n... and {len(queue) - 10} more."
        else:
            description = "The queue is empty."
        embed = discord.Embed(title="🎶 Current Queue", description=description, color=discord.Color.blue())
        await interaction.followup.send(embed=embed, ephemeral=True)


    elif custom_id == "disconnect":
        guild_id = interaction.guild.id
        music_queues.pop(guild_id, None)
        if guild_id in current_playing_messages:
             try:
                msg = current_playing_messages.pop(guild_id)
                await msg.delete()
             except: pass
        await vc.disconnect()
        await interaction.response.send_message("Disconnected and cleared the queue.", ephemeral=True)

# --- Slash Commands ---

@bot.tree.command(name="ping", description="Replies with pong!")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! Latency: {round(bot.latency * 1000)}ms")


 
@bot.tree.command(name="play", description="Plays a song or playlist from YouTube, Spotify, etc.")
@app_commands.describe(search_term="The URL or name of the song/playlist.")
async def play(interaction: discord.Interaction, search_term: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        await interaction.followup.send("You must be in a voice channel to use this command.", ephemeral=True)
        return

    vc = interaction.guild.voice_client
    if not vc:
        vc = await interaction.user.voice.channel.connect()
    elif vc.channel != interaction.user.voice.channel:
        await vc.move_to(interaction.user.voice.channel)

    class InteractionContext:
        def __init__(self, inter: discord.Interaction):
            self.guild = inter.guild
            self.voice_client = inter.guild.voice_client
            self.channel = inter.channel
            self.interaction = inter
        async def send(self, *args, **kwargs):
            return await self.channel.send(*args, **kwargs)

    ctx = InteractionContext(interaction)
    guild_id = ctx.guild.id
    if guild_id not in music_queues:
        music_queues[guild_id] = []

    context_for_guild[guild_id] = ctx
    requester_info = {'name': interaction.user.display_name, 'id': interaction.user.id, 'mention': interaction.user.mention}

    try:
        loop = asyncio.get_event_loop()

        # --- Spotify Playlist/Album Logic ---
        if SPOTIFY_URL_REGEX.match(search_term):
            spotify_info = await get_spotify_track_info(search_term)
            
            if isinstance(spotify_info, list):
                if not spotify_info:
                    await interaction.edit_original_response(content="This Spotify playlist/album appears to be empty or private.", ephemeral=True)
                    return

                # 1. Immediately process the first track
                first_track_query = spotify_info.pop(0)
                youtube_url = await search_youtube_video(first_track_query)
                
                if not youtube_url:
                    await interaction.edit_original_response(content=f"Couldn't find the first track '{first_track_query}' on YouTube. Aborting.")
                    return

                title = await extract_title(youtube_url)
                music_queues[guild_id].append({'url': youtube_url, 'title': title, 'requester': requester_info})
                
                await interaction.edit_original_response(content=f"▶️ Playing first song from Spotify. Queuing the rest in the background...")
                
                # 2. Start the background task for the rest
                if spotify_info:
                    bot.loop.create_task(queue_spotify_tracks_background(interaction, spotify_info, guild_id, requester_info))

                # 3. Start playback if the bot is idle
                if not vc.is_playing() and not vc.is_paused():
                    await play_next(ctx)
                return # End execution here for Spotify playlists

            elif isinstance(spotify_info, str):
                search_term = await search_youtube_video(spotify_info)
                if not search_term:
                    await interaction.edit_original_response(content=f"Could not find `{spotify_info}` on YouTube.")
                    return

        # --- Generic Search / YouTube / SoundCloud Logic ---
        if not YOUTUBE_URL_REGEX.match(search_term) and not SOUNDCLOUD_URL_REGEX.match(search_term):
            url = await search_youtube_video(search_term)
            if not url:
                await interaction.edit_original_response(content=f"Could not find anything for '{search_term}' on YouTube.")
                return
            search_term = url

        ydl_opts_playlist = { 'format': 'bestaudio/best', 'quiet': True, 'extract_flat': 'in_playlist', 'noplaylist': False, "cookiefile": "cookies.txt" }
        ydl = yt_dlp.YoutubeDL(ydl_opts_playlist)
        info = await loop.run_in_executor(executor, lambda: ydl.extract_info(search_term, download=False))
        
        if not info:
            await interaction.edit_original_response(content="Could not retrieve any information from the link.")
            return

        # --- YouTube/SoundCloud Playlist Logic ---
        if 'entries' in info:
            valid_entries = [entry for entry in info['entries'] if entry and entry.get('url')]
            if not valid_entries:
                await interaction.edit_original_response(content="Could not find any playable tracks in the playlist.")
                return

            playlist_title = info.get('title', 'playlist')
            
            # 1. Immediately process the first track
            first_entry = valid_entries.pop(0)
            music_queues[guild_id].append({
                'url': first_entry['url'],
                'title': first_entry.get('title', 'Unknown Title'),
                'requester': requester_info
            })
            
            await interaction.edit_original_response(content=f"▶️ Playing first song from **{playlist_title}**. Queuing the rest in the background...")
            
            # 2. Start the background task for the rest
            if valid_entries:
                bot.loop.create_task(queue_playlist_tracks_background(interaction, valid_entries, guild_id, requester_info, playlist_title))
            
            # 3. Start playback if the bot is idle
            if not vc.is_playing() and not vc.is_paused():
                await play_next(ctx)
        
        # --- Single Track Logic ---
        else:
            title = info.get('title', 'Unknown Title')
            music_queues[guild_id].append({
                'url': info['original_url'],
                'title': title,
                'requester': requester_info
            })
            await interaction.edit_original_response(content=f"✅ Added `{title}` to the queue.")

            if not vc.is_playing() and not vc.is_paused():
                await play_next(ctx)

    except Exception as e:
        print(f"[ERROR] Generic error in /play command: {e}")
        if not interaction.response.is_done():
            await interaction.edit_original_response(content=f"An unexpected error occurred: {e}")
        else:
            await interaction.followup.send(content=f"An unexpected error occurred: {e}", ephemeral=True)   


@bot.tree.command(name="loop", description="Loops the currently playing song.")
async def loop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if not vc or not (vc.is_playing() or vc.is_paused()):
        await interaction.response.send_message("I'm not playing anything right now!", ephemeral=True)
        return

    guild_id = interaction.guild.id
    # Toggle the loop state for the current guild
    current_state = loop_states.get(guild_id, False)
    loop_states[guild_id] = not current_state

    # When disabling loop, also clear the current song info to prevent accidental loops
    if not loop_states[guild_id]:
        current_song_info.pop(guild_id, None)

    if loop_states[guild_id]:
        await interaction.response.send_message("🔁 Looping is now **enabled** for the current song.", ephemeral=True)
    else:
        await interaction.response.send_message("🔁 Looping is now **disabled**.", ephemeral=True)


@bot.tree.command(name="disconnect", description="Disconnects the bot from the voice channel and clears the queue.")
async def disconnect(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        guild_id = interaction.guild.id
        music_queues.pop(guild_id, None)
        if guild_id in current_playing_messages:
             try:
                msg = current_playing_messages.pop(guild_id)
                await msg.delete()
             except: pass
        await vc.disconnect()
        await interaction.response.send_message("Disconnected and cleared the queue.")
    else:
        await interaction.response.send_message("I am not in a voice channel.", ephemeral=True)

@bot.tree.command(name="skip", description="Skips the current song.")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await interaction.response.send_message("Skipped.")
    else:
        await interaction.response.send_message("Nothing to skip.", ephemeral=True)

@bot.tree.command(name="queue", description="Displays the current song queue.")
async def queue(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild_id = interaction.guild.id
    queue_items = music_queues.get(guild_id, [])
    
    description = ""
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        description += "**__Now Playing:__**\n"
        if guild_id in current_playing_messages:
            try:
                msg = current_playing_messages[guild_id]
                title = msg.embeds[0].description.split('\n')[0].strip()
                description += f"🎵 {title}\n\n"
            except (IndexError, AttributeError):
                description += "🎵 *Currently playing a track.*\n\n"
        else:
            description += "🎵 *Currently playing a track.*\n\n"

    if queue_items:
        description += "**__Up Next:__**\n"
        # FIXED: Read the pre-fetched title directly from the item dictionary
        description += "\n".join(f"**{i+1}.** {item['title']} - *Requested by {item['requester']['mention']}*" for i, item in enumerate(queue_items[:10]))
        if len(queue_items) > 10:
            description += f"\n... and {len(queue_items) - 10} more."
    elif not vc or not vc.is_playing():
        description = "The queue is empty and nothing is playing."
        
    embed = discord.Embed(title="🎶 Music Queue", description=description, color=discord.Color.blue())
    await interaction.followup.send(embed=embed, ephemeral=True)

# --- Run Bot ---
if __name__ == '__main__':
    try:
        bot.run(TOKEN)
    except discord.errors.LoginFailure as e:
        print(f"[ERROR] Login failed: {e}. Check your DISCORD_BOT_TOKEN.")
    except Exception as e:
        print(f"[ERROR] An unexpected error occurred during startup: {e}")