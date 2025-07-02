# --- Import Libraries ---
import asyncio
import discord
from discord.ext import commands
import yt_dlp
import os
import functools
from dotenv import load_dotenv
import concurrent.futures
import aiohttp
import re
import time

# ADDED: Spotify imports
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
# ADDED: Spotify API Keys
SPOTIPY_CLIENT_ID = os.getenv('SPOTIPY_CLIENT_ID')
SPOTIPY_CLIENT_SECRET = os.getenv('SPOTIPY_CLIENT_SECRET')
SPOTIPY_REDIRECT_URI = os.getenv('SPOTIPY_REDIRECT_URI') # This is mostly a placeholder for ClientCredentials flow

PREFIX = '!'

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# --- Globals ---
# music_queues now stores dictionaries: {'url': '...', 'requester': {'name': '...', 'id': '...', 'mention': '...'}}
music_queues = {}
context_for_guild = {}
current_playing_messages = {} # NEW: To store the "Now Playing" message object for each guild
executor = concurrent.futures.ThreadPoolExecutor()

# Spotify Client Manager (for app-level authentication)
# Initialize once at global scope
sp_client_credentials_manager = SpotifyClientCredentials(
    client_id=SPOTIPY_CLIENT_ID,
    client_secret=SPOTIPY_CLIENT_SECRET
)
sp = spotipy.Spotify(client_credentials_manager=sp_client_credentials_manager)


yt_dlp_options = {
    "format": "bestaudio/best",
    "quiet": True,
    "noplaylist": True,
    "default_search": "auto",
    "extract_flat": False,
    "cookiefile": "cookies.txt" # Ensure this path is correct on Pterodactyl relative to your bot script
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

# --- Helper Functions ---
def create_progress_bar(current_sec, total_sec, bar_length=20):
    if total_sec is None or total_sec == 0: # Handle division by zero for live streams or unknown duration
        return "LIVE"
    # Ensure the '🔘' is always within the bar boundaries
    filled = int(bar_length * current_sec // total_sec)
    dot_position = min(max(0, filled), bar_length - 1)
    bar = "▬" * dot_position + "🔘" + "▬" * (bar_length - dot_position - 1)
    return bar

def format_time(seconds):
    if seconds is None or seconds == 0: # Handle unknown duration for live streams
        return "LIVE"
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{int(minutes):02}:{int(seconds):02}"

async def extract_title(url: str):
    try:
        info = await extract_info_async(url)
        # Handle cases where extract_info might return a playlist-like structure
        if 'entries' in info and info.get('_type') == 'playlist' and info.get('entries'):
            return info['entries'][0].get('title', url) # Get title of first track in playlist
        elif 'entries' in info and len(info['entries']) > 0 and info.get('_type') == 'url':
            return info['entries'][0].get('title', url) # For a single URL that yt_dlp treats as a list
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
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 1,
            "key": YOUTUBE_API_KEY
        }
        async with session.get("https://www.googleapis.com/youtube/v3/search", params=params) as resp:
            data = await resp.json()
            if "items" in data and len(data["items"]) > 0:
                video_id = data["items"][0]["id"]["videoId"]
                return f"https://www.youtube.com/watch?v={video_id}" # Corrected YouTube URL format
            return None

async def search_spotify(query):
    # This search is primarily for single tracks when a plain text query is given
    results = sp.search(q=query, type='track', limit=1)
    if results['tracks']['items']:
        track = results['tracks']['items'][0]
        return track['external_urls']['spotify']
    return None

async def get_spotify_track_info(spotify_url):
    try:
        if "track" in spotify_url:
            track_id = spotify_url.split('/')[-1].split('?')[0]
            track = sp.track(track_id)
            return f"{track['name']} {track['artists'][0]['name']}"
        elif "album" in spotify_url:
            album_id = spotify_url.split('/')[-1].split('?')[0]
            album_tracks = sp.album_tracks(album_id)
            track_list = []
            for item in album_tracks['items']:
                track_list.append(f"{item['name']} {item['artists'][0]['name']}")
            return track_list
        elif "playlist" in spotify_url:
            playlist_id = spotify_url.split('/')[-1].split('?')[0]
            playlist_tracks = sp.playlist_items(playlist_id)
            track_list = []
            for item in playlist_tracks['items']:
                # Ensure 'track' key exists and is not None before accessing its properties
                if 'track' in item and item['track']:
                    track = item['track']
                    # Ensure artists and name are available
                    if track.get('name') and track.get('artists') and track['artists'][0].get('name'):
                        track_list.append(f"{track['name']} {track['artists'][0]['name']}")
            return track_list
    except Exception as e:
        print(f"[ERROR] Could not get Spotify track info for {spotify_url}: {e}")
        return None
    return None

# --- Playback Functions ---
def play_next_callback(ctx, error):
    if error:
        print(f"[DEBUG] Player error: {error}")
        bot.loop.create_task(ctx.send(f"Playback error: {error}"))
    bot.loop.create_task(play_next(ctx))

async def update_progress_task(ctx, now_playing_msg, title, duration, song_start_time, vc, embed_template, view):
    try:
        # Keep track of the message ID to ensure we're updating the correct one
        original_message_id = now_playing_msg.id
        
        while vc.is_playing() and vc.source and current_playing_messages.get(ctx.guild.id) and current_playing_messages[ctx.guild.id].id == original_message_id:
            elapsed = int(time.monotonic() - song_start_time)
            
            # Break if the song is expected to be over (with a small buffer)
            if duration is not None and duration > 0 and elapsed >= duration + 2: 
                break
            
            progress = create_progress_bar(elapsed, duration)
            
            current_embed = embed_template.copy() 
            
            # Reconstruct the description, preserving static info
            original_description_lines = embed_template.description.split('\n')
            
            progress_line_start_index = -1
            for i, line in enumerate(original_description_lines):
                if '`' in line and '/' in line: # Assumes time format is in backticks
                    progress_line_start_index = i
                    break
            
            if progress_line_start_index != -1:
                static_info_part = "\n".join(original_description_lines[:progress_line_start_index])
                current_embed.description = (
                    f"{static_info_part}\n\n"
                    f"`{format_time(elapsed)} / {format_time(duration)}`\n{progress}"
                )
            else:
                current_embed.description = f"**{title}**\n\n`{format_time(elapsed)} / {format_time(duration)}`\n{progress}"
            
            try:
                await now_playing_msg.edit(embed=current_embed, view=view)
            except discord.errors.NotFound:
                print(f"[DEBUG] 'Now Playing' message {original_message_id} not found, stopping update task.")
                break # Message was deleted
            except discord.errors.HTTPException as e:
                if e.code == 50001:  # Missing Access (e.g., bot lost permissions or channel deleted)
                    print(f"[WARNING] Bot missing access to update message {original_message_id}: {e}")
                    break
                raise # Re-raise other HTTP errors
            await asyncio.sleep(1) # Update less frequently, changed from 0.1 to 1 second

        # Final update when song finishes or stops naturally (not skipped/stopped by command)
        if current_playing_messages.get(ctx.guild.id) and current_playing_messages[ctx.guild.id].id == original_message_id and not now_playing_msg.is_deleted():
            final_progress = create_progress_bar(duration, duration) if duration is not None and duration > 0 else "LIVE"
            final_embed = embed_template.copy()

            original_description_lines = embed_template.description.split('\n')
            progress_line_start_index = -1
            for i, line in enumerate(original_description_lines):
                if '`' in line and '/' in line:
                    progress_line_start_index = i
                    break
            
            if progress_line_start_index != -1:
                static_info_part = "\n".join(original_description_lines[:progress_line_start_index])
                final_embed.description = (
                    f"{static_info_part}\n\n"
                    f"`{format_time(duration)} / {format_time(duration)}`\n{final_progress}"
                )
            else:
                final_embed.description = f"**{title}**\n\n`{format_time(duration)} / {format_time(duration)}`\n{final_progress}"

            try:
                # Remove buttons only if the song genuinely finished and not skipped/stopped
                if not vc.is_playing() and not vc.is_paused(): # Check if bot is truly not playing
                    await now_playing_msg.edit(embed=final_embed, view=None) 
            except discord.errors.NotFound:
                pass # Message already deleted
            except discord.errors.HTTPException as e:
                print(f"[WARNING] Bot missing access to final update of message {original_message_id}: {e}")

    except Exception as e:
        print(f"[ERROR] Error in update_progress_task for guild {ctx.guild.id} (message {original_message_id}): {e}")

async def play_next(ctx):
    guild_id = ctx.guild.id
    context_for_guild[guild_id] = ctx

    # Clean up previous "Now Playing" message if it exists and is not the same as the current one
    if guild_id in current_playing_messages:
        old_msg = current_playing_messages.pop(guild_id) # Remove from global first
        try:
            if not old_msg.is_deleted():
                # Edit the old message to show it ended, removing buttons
                await old_msg.edit(embed=discord.Embed(
                    title="🎶 Previous Song Ended",
                    description="The previous track has finished.",
                    color=discord.Color.dark_grey()
                ), view=None)
        except Exception as e:
            print(f"[DEBUG] Could not clean up old 'Now Playing' message {old_msg.id}: {e}")

    if guild_id in music_queues and music_queues[guild_id]:
        song_data = music_queues[guild_id].pop(0) # Get the next dictionary from the queue
        url = song_data['url']
        requester_id = song_data['requester']['id'] # Get requester ID for mention
        try:
            print(f"[DEBUG] Now processing URL for playback: {url}")
            info = await extract_info_async(url)
            
            # Handle potential playlists or flat entries if yt_dlp gives them
            if 'entries' in info and info.get('_type') == 'playlist' and info.get('entries'):
                info = info['entries'][0] # Take the first entry if it's a playlist
            elif 'entries' in info and len(info['entries']) > 0: # For a single URL that yt_dlp treats as a list
                 info = info['entries'][0]

            audio_url = info['url']
            title = info.get('title', 'Unknown Title')
            thumbnail = info.get('thumbnail', None)
            duration = info.get('duration', 0) if info.get('duration') else None # Handle 0 or None for live streams

            artist = info.get('artist') or info.get('uploader') or "Unknown Artist" # Extract artist/uploader

            source = discord.FFmpegPCMAudio(audio_url, **ffmpeg_options)
            vc = ctx.voice_client
            
            # If already playing or paused, stop the current song first
            if vc.is_playing() or vc.is_paused():
                vc.stop()

            vc.play(source, after=lambda e: play_next_callback(ctx, e))

            progress_bar = create_progress_bar(0, duration)
            
            # Construct the new detailed description for the embed
            duration_str = format_time(duration) if duration else "LIVE"

            description_text = (
                f"**{title}**\n\n"
                f"<:Orion_User:1389189744625188884> **Requested by:** <@{requester_id}>\n"
                f"<:Orion_Timer:1386211890774151219> **Music Duration:** {duration_str}\n"
                f"<:Orion_Partner:1386212658453151815> **Music Author:** {artist}\n\n"
                f"`00:00 / {duration_str}`\n{progress_bar}"
            )

            embed = discord.Embed(
                title="<a:Orion_VinylRecord:1386211619410804756>   Now Playing",
                description=description_text,
                color=discord.Color.green()
            )
            if thumbnail:
                embed.set_thumbnail(url=thumbnail)

            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="⏸ Pause", style=discord.ButtonStyle.primary, custom_id="pause"))
            view.add_item(discord.ui.Button(label="▶ Resume", style=discord.ButtonStyle.success, custom_id="resume"))
            view.add_item(discord.ui.Button(label="⏭ Skip", style=discord.ButtonStyle.secondary, custom_id="skip"))
            view.add_item(discord.ui.Button(label="📜 Queue", style=discord.ButtonStyle.secondary, custom_id="queue"))
            view.add_item(discord.ui.Button(label="⏹ Disconnect", style=discord.ButtonStyle.danger, custom_id="disconnect"))

            now_playing_msg = await ctx.send(embed=embed, view=view)
            current_playing_messages[guild_id] = now_playing_msg # Store the new message

            start_time = time.monotonic() # Set start time for the new song

            # Start the progress bar update task
            bot.loop.create_task(update_progress_task(ctx, now_playing_msg, title, duration, start_time, vc, embed, view))

        except Exception as e:
            print(f"[DEBUG] Playback error for {url}: {e}")
            await ctx.send(f"Error playing track: {e}")
            # Try playing the next song if there's an error with the current one
            if guild_id in music_queues and music_queues[guild_id]:
                await play_next(ctx)
            else:
                await ctx.send("Queue is empty or playback failed for all items.")
    else:
        # If queue is empty, clean up the now playing message and potentially disconnect
        if guild_id in current_playing_messages:
            try:
                old_msg = current_playing_messages.pop(guild_id)
                if not old_msg.is_deleted():
                    await old_msg.edit(embed=discord.Embed(
                        title="Queue Ended",
                        description="Add more songs to continue.",
                        color=discord.Color.orange()
                    ), view=None) # Remove buttons when queue ends
            except Exception as e:
                print(f"[DEBUG] Error cleaning up message on queue end: {e}")
        
        # Consider disconnecting if bot is idle after queue ends
        if ctx.voice_client and not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
            # await ctx.voice_client.disconnect() # Optional: auto-disconnect on queue end
            await ctx.send("The queue has finished. I'll stay in the voice channel for more songs, or you can use `!disconnect`.")


# --- Events ---
@bot.event
async def on_ready():
    print(f'[DEBUG] Logged in as {bot.user} (ID: {bot.user.id})')


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument. Usage: `{PREFIX}{ctx.command.name} {ctx.command.signature}`")
    elif isinstance(error, commands.CommandNotFound):
        pass # Ignore command not found errors to avoid spamming chat
    elif isinstance(error, commands.NotOwner):
        await ctx.send("You don't have permission to use this command.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("I don't have the necessary permissions to do that.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"Bad argument provided: {error}")
    else:
        await ctx.send(f"An unexpected error occurred: `{error}`")
        print(f"[ERROR] Unhandled command error: {error}")

@bot.event
async def on_interaction(interaction):
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id")
    ctx = context_for_guild.get(interaction.guild.id)

    # Reconstruct a basic context if not found, to at least send an ephemeral message
    if not ctx:
        # Create a dummy context object just enough to send a message
        class DummyContext:
            def __init__(self, bot, interaction):
                self.bot = bot
                self.author = interaction.user
                self.guild = interaction.guild
                self.channel = interaction.channel
                self.voice_client = interaction.guild.voice_client if interaction.guild else None
            
            async def send(self, *args, **kwargs):
                await interaction.response.send_message(*args, **kwargs, ephemeral=True)
            
            async def invoke(self, command, *args, **kwargs):
                # This part is tricky. If the original context is truly gone, 
                # invoking a command might fail without the full context.
                # For button presses, we usually want to just call the function directly.
                pass 
        ctx = DummyContext(bot, interaction)
        # If still no voice client, and command needs it, send a message
        if not ctx.voice_client and custom_id in ["pause", "resume", "skip", "disconnect"]:
            try:
                await interaction.response.send_message("I'm not connected to a voice channel or there's no active playback.", ephemeral=True)
            except discord.errors.NotFound:
                pass # Interaction already responded to or expired
            return


    command_map = {
        "pause": pause,
        "resume": resume,
        "skip": skip,
        "queue": queue,
        "disconnect": disconnect,
    }

    command_func = command_map.get(custom_id)

    if command_func:
        try:
            # Defer the interaction to avoid "This interaction failed" if command takes time
            if not interaction.response.is_done():
                await interaction.response.defer(thinking=False)
            
            # For `queue` command, we want to respond directly with the queue message,
            # not just defer, so `ctx.send` can be used by the command.
            if custom_id == "queue":
                await command_func(ctx) # `queue` sends its own message
                # If the queue command sends its own message, we don't need a follow-up
                if not interaction.response.is_done():
                    await interaction.followup.send("Queue displayed.", ephemeral=True)
            else:
                # For other commands like pause, resume, skip, disconnect,
                # the deferred response is sufficient, and the command itself
                # will usually send a follow-up message.
                await command_func(ctx)
                if not interaction.response.is_done():
                    await interaction.followup.send("Command executed.", ephemeral=True)

        except discord.errors.HTTPException as e:
            print(f"[DEBUG] HTTPException during interaction '{custom_id}': {e}")
            if not interaction.response.is_done():
                await interaction.followup.send(f"Failed to execute command: {e}", ephemeral=True)
        except Exception as e:
            print(f"[ERROR] Error handling interaction '{custom_id}': {e}")
            if not interaction.response.is_done():
                await interaction.followup.send(f"An unexpected error occurred: `{e}`", ephemeral=True)
    else:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("Unknown button clicked.", ephemeral=True)
        except discord.errors.NotFound:
            pass # Interaction expired or already responded to

# --- Commands ---
@bot.command(name='join', aliases=['j'])
async def join(ctx):
    if not ctx.author.voice:
        await ctx.send("You're not in a voice channel.")
        return
    channel = ctx.author.voice.channel
    if ctx.voice_client:
        if ctx.voice_client.channel == channel:
            await ctx.send(f"I'm already in **{channel.name}**.")
            return
        await ctx.voice_client.move_to(channel)
    else:
        try:
            await channel.connect()
        except asyncio.TimeoutError:
            await ctx.send("Could not connect to the voice channel. Please try again.")
            return
        except discord.errors.Forbidden:
            await ctx.send("I don't have permissions to connect to that voice channel. Please check my role permissions.")
            return
    await ctx.send(f"Joined **{channel.name}**")
    context_for_guild[ctx.guild.id] = ctx

@bot.command(name='leave', aliases=['l'])
async def leave(ctx):
    if ctx.voice_client:
        guild_id = ctx.guild.id
        music_queues.get(guild_id, []).clear() # Clear queue
        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop() # Stop current playback
        await ctx.voice_client.disconnect()
        await ctx.send("Disconnected and queue cleared.")
        context_for_guild.pop(guild_id, None)
        if guild_id in current_playing_messages:
            try:
                # Edit the message to reflect disconnection or delete it
                msg = current_playing_messages.pop(guild_id)
                if not msg.is_deleted():
                    await msg.edit(embed=discord.Embed(
                        title="Disconnected",
                        description="I have left the voice channel.",
                        color=discord.Color.red()
                    ), view=None)
            except Exception as e:
                print(f"[DEBUG] Error cleaning up message on leave: {e}")
    else:
        await ctx.send("I'm not in a voice channel.")

@bot.command(name='disconnect', aliases=['dc'])
async def disconnect(ctx):
    # This command is often synonymous with 'leave' for a music bot
    await leave(ctx) 

@bot.command(name='play', aliases=['p'])
async def play(ctx, *, search_term: str):
    async with ctx.typing():
        if not ctx.author.voice:
            await ctx.send("You need to be in a voice channel for me to join.")
            return

        if not ctx.voice_client:
            await ctx.invoke(bot.get_command('join'))
            if not ctx.voice_client: # Double check if join failed
                await ctx.send("Failed to join your voice channel. Please ensure I have permissions.")
                return

        guild_id = ctx.guild.id
        if guild_id not in music_queues:
            music_queues[guild_id] = []

        context_for_guild[guild_id] = ctx

        urls_to_add = []
        requester_info = {'name': ctx.author.display_name, 'id': ctx.author.id, 'mention': ctx.author.mention} # Store requester info
        
        try:
            # Direct URL handling (YouTube, SoundCloud)
            if YOUTUBE_URL_REGEX.match(search_term) or SOUNDCLOUD_URL_REGEX.match(search_term):
                urls_to_add.append({'url': search_term, 'requester': requester_info})
            
            # Spotify URL handling
            elif SPOTIFY_URL_REGEX.match(search_term):
                await ctx.send(f"Processing Spotify link...")
                track_info = await get_spotify_track_info(search_term)
                
                if isinstance(track_info, list): # Album or Playlist
                    if not track_info:
                        await ctx.send("No playable tracks found in that Spotify album/playlist.")
                        return

                    Youtubees = [search_youtube_video(f"{tn} official audio") for tn in track_info]
                    resolved_youtube_urls = await asyncio.gather(*Youtubees)

                    added_count = 0
                    for original_track_name, youtube_url in zip(track_info, resolved_youtube_urls):
                        if youtube_url:
                            urls_to_add.append({'url': youtube_url, 'requester': requester_info})
                            added_count += 1
                        else:
                            print(f"[DEBUG] Could not find YouTube equivalent for Spotify track: {original_track_name}")
                    
                    if added_count > 0:
                        await ctx.send(f"Added {added_count} tracks from Spotify to the queue.")
                    else:
                        await ctx.send("Could not find any of those Spotify tracks on YouTube.")
                        return # No songs to play, so return

                elif track_info: # Single Spotify track
                    search_query_for_yt_dlp = track_info + " official audio"
                    youtube_url = await search_youtube_video(search_query_for_yt_dlp)
                    if youtube_url:
                        urls_to_add.append({'url': youtube_url, 'requester': requester_info})
                    else:
                        await ctx.send(f"Found Spotify track, but couldn't find it on YouTube: `{track_info}`.")
                        return
                else:
                    await ctx.send("Could not extract track info from Spotify URL.")
                    return
            
            # General search query handling (YouTube & then Spotify fallback)
            else:
                print(f"[DEBUG] Searching YouTube for: {search_term}")
                youtube_url = await search_youtube_video(search_term)
                if youtube_url:
                    urls_to_add.append({'url': youtube_url, 'requester': requester_info})
                else:
                    print(f"[DEBUG] Not found on YouTube. Searching Spotify for: {search_term}")
                    spotify_url_found = await search_spotify(search_term)
                    if spotify_url_found:
                        print(f"[DEBUG] Found on Spotify: {spotify_url_found}. Now searching YouTube for it.")
                        track_search_term = await get_spotify_track_info(spotify_url_found)
                        if track_search_term and isinstance(track_search_term, str):
                            search_query_for_yt_dlp = track_search_term + " official audio"
                            youtube_url = await search_youtube_video(search_query_for_yt_dlp)
                            if youtube_url:
                                urls_to_add.append({'url': youtube_url, 'requester': requester_info})
                            else:
                                await ctx.send(f"Found Spotify track, but couldn't find it on YouTube: `{track_search_term}`.")
                                return
                        else:
                            await ctx.send("Could not extract single track info from Spotify search result. Please use a direct Spotify URL for albums/playlists.")
                            return
                    else:
                        await ctx.send("No results found on YouTube or Spotify for your query.")
                        return

            if not urls_to_add:
                await ctx.send("Could not find any playable content for your request.")
                return

            # Add all resolved URLs (now dicts) to the queue
            music_queues[guild_id].extend(urls_to_add)

            # Send confirmation for single adds
            if len(urls_to_add) == 1 and not SPOTIFY_URL_REGEX.match(search_term):
                await ctx.send(f"Added `{await extract_title(urls_to_add[0]['url'])}` to the queue.")

            # If the bot is not playing, start playback of the first item in the queue
            if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                await play_next(ctx)
            # If already playing, and multiple songs were added, confirm
            elif len(urls_to_add) > 1 and SPOTIFY_URL_REGEX.match(search_term):
                pass # Message already sent for spotify multi-add

        except Exception as e:
            print(f"[DEBUG] Error processing {search_term}: {e}")
            await ctx.send(f"❌ Could not process `{search_term}`.\nError: {e}")
            return


@bot.command(name='pause', aliases=['ps'])
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("Paused.")
    elif ctx.voice_client and ctx.voice_client.is_paused():
        await ctx.send("Already paused. Use `!resume` to continue.")
    else:
        await ctx.send("Nothing is playing to pause.")

@bot.command(name='resume', aliases=['res'])
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("Resumed.")
    elif ctx.voice_client and ctx.voice_client.is_playing():
        await ctx.send("Already playing.")
    else:
        await ctx.send("Nothing is paused to resume.")

@bot.command(name='skip', aliases=['s'])
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop() # This will trigger play_next_callback
        await ctx.send("Skipped.")
    elif ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.stop() # This will trigger play_next_callback
        await ctx.send("Skipped (resumed and then skipped).")
    else:
        await ctx.send("Nothing to skip.")

@bot.command(name='stop', aliases=['st'])
async def stop(ctx):
    guild_id = ctx.guild.id
    music_queues.get(guild_id, []).clear()
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        await ctx.send("Stopped and queue cleared.")
        # Clean up the now playing message
        if guild_id in current_playing_messages:
            try:
                # Pop and edit the message to show it was stopped
                msg_to_edit = current_playing_messages.pop(guild_id)
                if not msg_to_edit.is_deleted():
                    await msg_to_edit.edit(embed=discord.Embed(
                        title="Playback Stopped",
                        description="The current song was stopped and the queue cleared.",
                        color=discord.Color.red()
                    ), view=None)
            except Exception:
                pass
    else:
        await ctx.send("Nothing is playing to stop.")

@bot.command(name='queue', aliases=['q'])
async def queue(ctx):
    guild_id = ctx.guild.id
    queue = music_queues.get(guild_id, [])
    if queue:
        # Get titles and requesters for the first 10 items
        items_for_title_extraction = [(item['url'], item['requester']['mention']) for item in queue[:10]]
        
        titles_futures = [extract_title(url) for url, _ in items_for_title_extraction]
        titles = await asyncio.gather(*titles_futures)
        
        message_lines = []
        for i, title in enumerate(titles):
            original_item_index = i
            requester_mention = items_for_title_extraction[original_item_index][1]
            message_lines.append(f"{i+1}. {title} (Requested by: {requester_mention})")
        
        description_parts = []
        # Add current playing song to the queue display if available
        if ctx.voice_client and ctx.voice_client.is_playing() and guild_id in current_playing_messages:
            try:
                current_embed = current_playing_messages[guild_id].embeds[0]
                if current_embed.title == "🎶 Now Playing" and current_embed.description:
                    description_parts.append("**__Currently Playing__**")
                    # Append relevant lines from the 'Now Playing' embed (e.g., first 8 lines which cover song title, requester, duration, author)
                    description_parts.extend(current_embed.description.split('\n')[:8])
                    description_parts.append("---")
            except Exception:
                pass
        
        if message_lines:
            description_parts.append("**__Up Next__**")
            description_parts.append("\n".join(message_lines))
            if len(queue) > 10:
                description_parts.append(f"\n... and {len(queue) - 10} more.")
        elif not description_parts: # If no current song info either
            description_parts.append("The queue is empty.")
        
        description = "\n".join(description_parts)

        embed = discord.Embed(
            title="Current Queue",
            description=description,
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("Queue is empty.")
        # If there's a "Now Playing" message, it might be the only active song
        if ctx.voice_client and ctx.voice_client.is_playing() and guild_id in current_playing_messages:
             try:
                current_embed = current_playing_messages[guild_id].embeds[0]
                if current_embed.title == "🎶 Now Playing" and current_embed.description:
                    # Extract the title from the description (first line)
                    current_song_title = current_embed.description.split('\n')[0].replace('**', '').strip()
                    await ctx.send(f"Currently playing: **{current_song_title}** (Queue is otherwise empty).")
             except Exception:
                 pass # Couldn't get info, just sent "Queue is empty."

# --- Run Bot ---
if __name__ == '__main__':
    try:
        bot.run(TOKEN)
    except discord.errors.LoginFailure as e:
        print(f"[ERROR] Login failed: {e}. Check your DISCORD_BOT_TOKEN environment variable.")
    except Exception as e:
        print(f"[ERROR] An unexpected error occurred during bot startup: {e}")