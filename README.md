# musicdisc

<div align="center">

<a href="https://ko-fi.com/bkrbnkr"><img alt="Support me on Ko-fi" src="https://img.shields.io/badge/Ko--fi-buy_me_a_coffee-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white"></a>

</div>

A Discord music bot written with [discord.py](https://github.com/Rapptz/discord.py). It started as the music module of my old Gigabot (the file still has that name) and runs on its own. It streams audio through `yt-dlp` and `ffmpeg`, keeps a queue per server, and lets people save playlists.

## Features

- Play by link or search term. `?search` shows the top results and you pick one with a reaction
- One queue per server with paging, shuffle, skip, pause, resume, stop and volume
- Saved playlists in `storage/playlists.yml`: up to 5 per user, public or private, with descriptions and likes
- Leaves the voice channel when everyone else has left
- Drops the queue of any server it is no longer connected to, checked once a minute
- Ignores DMs

## Commands

The prefix is `?`.

### Playback

| Command | Description |
|---|---|
| `?play <search/link>` (`?p`) | Play a song |
| `?search <song>` | Search for a song on YouTube and add it to the queue |
| `?queue` (`?q`, `?list`, `?remote`) | View the queue |
| `?nowplaying` (`?np`) | Shows current song info |
| `?join` (`?j`) | Connect to a voice channel |
| `?leave` (`?l`) | Disconnect from a channel |
| `?skip` (`?s`) | Skip the current song |
| `?pause` | Pause the current song |
| `?resume` (`?r`) | Resume playback |
| `?stop` | Stop the current song and clear the queue |
| `?shuffle` | Shuffle the song queue |
| `?volume <1-10>` | Set the volume |
| `?savenow <playlist>` | Save the currently playing song to a playlist |
| `?resetq` (`?rq`) | Reset the music queue system and clean up (Administrator) |

### Playlists

`?playlist` (or `?pl`) followed by a subcommand.

| Subcommand | Description |
|---|---|
| `create <name>`, `delete <name>` | Make or remove one of your playlists |
| `add <playlist> <song, song, ...>` | Add songs by name |
| `play <name>` | Queue the whole playlist |
| `view <name>` | Show your playlist |
| `viewother <user> <name>` | Show someone else's public playlist |
| `profile [user]` | List a user's playlists |
| `status <playlist> <public/private>` | Change visibility |
| `description <playlist> <text>` (`setdesc`, `desc`) | Set a description |
| `like <user> <playlist>`, `unlike <user> <playlist>`, `liked` | Like, unlike and list liked playlists |

## Requirements

- Python 3.8 or newer (`requirements.txt` pins discord.py 2.4.0 and yt_dlp)
- `ffmpeg` on `PATH`
- `PyNaCl` for voice, included in `requirements.txt`
- A Discord application with the Server Members and Message Content intents enabled

## Setup

1. Install the dependencies:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Put your bot token in `config.yml`. The only key is `discord_token`. The copy in the repo has a placeholder.

3. Create the storage folder. The playlist file is created inside it on first use, but the folder itself is not:

   ```bash
   mkdir storage
   ```

4. Run it:

   ```bash
   python3 gigamusic.py
   ```

---

## Support

<div align="center">

This bot is free and open source, and I work on it in my spare time.<br>
If it saved you some time, you can buy me a coffee. No pressure - the code stays free either way.

<a href="https://ko-fi.com/bkrbnkr"><img alt="Support me on Ko-fi" src="https://img.shields.io/badge/Ko--fi-bkrbnkr-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white"></a>

</div>

<!-- more ways to support go here -->
<!-- - [PayPal](...) -->
<!-- - [GitHub Sponsors](...) -->

## License

MIT, see [LICENSE](LICENSE).
