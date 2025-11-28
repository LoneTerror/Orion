# Discord Music Bot — README

## Project Title

**Discord Music Bot** — a simple Discord bot that plays music from YouTube, SoundCloud and Spotify (by searching YouTube), with slash commands and interactive buttons.

---

## Introduction

This repository contains a Python Discord bot that can play single tracks and playlists, queue tracks, show a now-playing message with a progress bar, and supports looping (song / queue). Spotify playlists/albums are converted to YouTube searches in the background. The bot logs played songs and user events to JSON files.

---

## Table of Contents

* [Requirements](#requirements)
* [Installation](#installation)
* [Configuration](#configuration)
* [Run the bot](#run-the-bot)
* [Usage (Discord commands & buttons)](#usage-discord-commands--buttons)
* [Features](#features)
* [Files created at runtime](#files-created-at-runtime)
* [Troubleshooting](#troubleshooting)
* [Contributors](#contributors)
* [License](#license)

---

## Requirements

* Python **3.10+** (use the appropriate version you normally use)
* **FFmpeg** installed and available on PATH (required by discord.py to play audio)
* A Discord Bot token and the bot added to your server with voice & application command (slash) permissions
* YouTube Data API key (for searching)
* Spotify API credentials (Client ID & Secret) if you want Spotify support
* Optional: `cookies.txt` if you rely on certain yt-dlp features (the code references a `cookies.txt` file)

Python package dependencies (the README includes an install command; exact versions can be adjusted):

```text
discord.py>=2.0.0
yt-dlp
spotipy
python-dotenv
aiohttp
```

You can put the above into a `requirements.txt` file.

---

## Installation

1. Clone your repository (or place the bot file, for example `bot.py`) into a project folder.

2. Create & activate a Python virtual environment (recommended):

```bash
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

3. Install dependencies:

Create a `requirements.txt` with the following content (example):

```
discord.py>=2.0.0
yt-dlp
spotipy
python-dotenv
aiohttp
```

Then run:

```bash
pip install -r requirements.txt
```

4. Install FFmpeg on your system:

* **Ubuntu / Debian:** `sudo apt install ffmpeg`
* **macOS (Homebrew):** `brew install ffmpeg`
* **Windows:** download FFmpeg and add it to your PATH (or use a package manager)

---

## Configuration

Create a `.env` file in the project root with the required environment variables:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
YOUTUBE_API_KEY=your_google_youtube_api_key_here
SPOTIPY_CLIENT_ID=your_spotify_client_id_here
SPOTIPY_CLIENT_SECRET=your_spotify_client_secret_here
```

Notes:

* The code uses `python-dotenv` to load these variables.
* `cookies.txt` is referenced by yt-dlp options; create it if you rely on cookies for any videos (optional).
* Ensure your Discord bot has slash commands enabled and the following permissions in the server:

  * Connect, Speak (voice)
  * Send Messages, Embed Links, Read Message History
  * Use Slash Commands (application.commands)
  * Use External Emojis (optional; the bot embeds use custom emoji IDs)

---

## Run the bot

From the project directory (with the virtualenv active):

```bash
python bot.py
```

If everything goes well, you should see a console message like:

```
[INFO] Logged in as <BotName> (ID: 123456789012345678)
[INFO] Synced N slash commands.
```

If you encounter `discord.errors.LoginFailure`, check that `DISCORD_BOT_TOKEN` in `.env` is correct.

---

## Usage (Discord commands & buttons)

All commands are slash commands (registered under `bot.tree`):

### Slash commands

* `/ping` — replies with Pong and latency.
* `/play <search_term>` — plays a song or playlist. `search_term` can be:

  * A YouTube URL (single video or playlist)
  * A SoundCloud URL
  * A Spotify track/album/playlist URL (the bot will convert to YouTube searches)
  * A search string (the bot searches YouTube and uses the top video)

  Behaviour:

  * For playlists (YouTube/SoundCloud) or Spotify playlists/albums: the bot enqueues the first track immediately and queues the remainder in a background task.
  * You must be in a voice channel to use `/play`. The bot will join your voice channel.
* `/loop` — sets loop mode. Options:

  * `Song (On)`: loop the current song
  * `Song (Off)`: disable song loop
  * `Queue (On)`: loop the entire queue (when queue ends it resets from history)
  * `Queue (Off)`: disable queue loop
  * `Turn Off (All)`: disable both looping modes
* `/disconnect` — disconnect the bot from voice and clear the queue.
* `/skip` — skip the current song.
* `/queue` — show the current queue and now playing.

### Interactive buttons (shown in the "Now Playing" embed)

When a track is playing, the bot posts an embed with these buttons:

* **⏸ Pause** — pauses the current track
* **▶ Resume** — resumes playback
* **⏭ Skip** — skip current track
* **📜 Queue** — shows the queue
* **⏹ Disconnect** — disconnects and clears the queue

Button presses are handled as component interactions and are logged to `event_log.json`.

---

## Features

* Play single tracks or playlists (YouTube, SoundCloud).
* Spotify support: the bot converts Spotify items to YouTube searches and queues results (supports track, album, playlist).
* Background queueing of large playlists to avoid long response times.
* Now-playing embed with progress bar that updates every second.
* Loop modes for single songs or the full queue.
* Persistent JSON logging of songs and events:

  * `song_log.json` — each played track with timestamp, guild, requester.
  * `event_log.json` — user interactions (button presses, etc.).
* Graceful handling of playlist processing: first track plays immediately and remaining tracks are queued asynchronously.

---

## Files created at runtime

* `song_log.json` — appended with each playing song entry.
* `event_log.json` — appended when events (button presses etc.) occur.
* `cookies.txt` — optionally used by `yt-dlp` if you want to use cookies for age-restricted content (not created by the bot — supply it if needed).

---

## Troubleshooting

* **LoginFailure error on startup**
  `discord.errors.LoginFailure: ...` — Check that `DISCORD_BOT_TOKEN` is set correctly in `.env` and that the token has not been regenerated/expired.

* **Bot doesn't join voice channel / cannot play audio**

  * Ensure the bot has "Connect" and "Speak" permissions in the voice channel.
  * Confirm FFmpeg is installed and on your PATH.
  * Make sure intents are enabled in your bot settings if you rely on privileged intents (this bot uses `message_content=True` in the code — enable only if necessary & allowed).

* **`yt_dlp` errors / extraction issues**

  * If downloads/extraction fail, check network access, yt-dlp version, and whether `cookies.txt` is required for the target video.
  * The script sets a custom `bug_reports_message` override to suppress some yt-dlp bug prompts.

* **Spotify errors**

  * Ensure `SPOTIPY_CLIENT_ID` and `SPOTIPY_CLIENT_SECRET` are valid and that your Spotify app has the proper settings.
  * Private playlists/albums may not be readable — expect failures for private content.

* **Slash commands not appearing**

  * The bot syncs `bot.tree` on `on_ready`. If commands don’t show up immediately, wait a minute or re-invite the bot with `applications.commands` scope and the proper OAuth2 permissions.

* **Permissions related issues**

  * If the bot can’t send embeds or buttons, ensure it has **Send Messages** and **Embed Links** permissions in the target channel.

If you want, I can add a short script to auto-generate a `requirements.txt` with pinned versions or provide a Dockerfile.

---

## Examples

**Play a search string**

```
/play Never Gonna Give You Up
```

**Play a YouTube URL**

```
/play https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

**Play a Spotify playlist**

```
/play https://open.spotify.com/playlist/...
```

(First track plays immediately, remaining tracks are queued in the background.)

**Enable queue loop**

```
/loop Queue (On)
```

**Use buttons**

* While a song plays, press **⏸ Pause** to pause and **▶ Resume** to resume.

---
