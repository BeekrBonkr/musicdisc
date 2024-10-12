import discord
from discord.ext import commands
import yt_dlp
import asyncio
import random
import yaml
import os
from discord import FFmpegPCMAudio
import re
from termcolor import colored
from datetime import datetime
import traceback
import aiofiles
import logging
import sys
from urllib.parse import urlparse, parse_qs

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.members = True
intents.reactions = True
intents.bans = True
intents.emojis_and_stickers = True
intents.voice_states = True
intents.message_content = True
intents.invites = True

bot = commands.Bot(command_prefix='?', intents=intents)

bot.remove_command('help')

@bot.event
async def on_ready():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="started music module"), status=discord.Status.online)
    print(colored('Music module started!', 'green'))
    bot.loop.create_task(cleanup_queues(bot))

@bot.event
async def on_message(message):
    # Ignore messages sent by bots or DMs
    if message.author.bot or message.guild is None:
        return

    # Process commands as usual
    await bot.process_commands(message)

ydl_opts = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'Opus',
        'preferredquality': '192',
    }],
    'retries': 20,
    'ffmpeg_options': {
        'options': '-vn',
        'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10'
    },
}

# Store the songs in a dictionary keyed by the voice channel ID
queues = {}

async def cleanup_queues(bot):
    while True:
        # Iterate over a copy of the keys to avoid RuntimeError
        for guild_id in list(queues.keys()):
            guild = bot.get_guild(guild_id)
            if not guild:
                del queues[guild_id]
                continue

            voice_client = guild.voice_client
            if not voice_client or not voice_client.is_connected():
                del queues[guild_id]

        # Wait before checking again
        await asyncio.sleep(60)

async def auto_disconnect(ctx):
    # Check if there is a voice client connected to the guild
    if ctx.voice_client is not None:
        # Check if the bot is still playing audio or if there are members (other than the bot) in the voice channel
        if not ctx.voice_client.is_playing() or len([member for member in ctx.voice_client.channel.members if not member.bot]) == 1:
            # Check if there are any songs in the queue
            if not queues.get(ctx.guild.id):
                # Disconnect from the voice channel if there are no songs in the queue
                await ctx.voice_client.disconnect()
    else:
        # Clear the queue for the server when the voice channel is None
        if ctx.guild.id in queues:
            del queues[ctx.guild.id]

@bot.event
async def on_voice_state_update(member, before, after):
    # Check if the bot is in the same voice channel as the member
    bot_voice_state = member.guild.me.voice
    if bot_voice_state:
        bot_voice_channel = bot_voice_state.channel
        if bot_voice_channel and bot_voice_channel == before.channel and not after.channel:
            # If there's only the bot left in the voice channel, disconnect
            if len(bot_voice_channel.members) == 1 and bot_voice_channel.members[0] == member.guild.me:
                await bot_voice_channel.guild.voice_client.disconnect()
                global queues
                if member.guild.id in queues:
                    del queues[member.guild.id]


class YTDLSource(FFmpegPCMAudio):
    def __init__(self, source, *args, **kwargs):
        super().__init__(source, *args, **kwargs)

    @classmethod
    async def create_source(cls, ctx, query, loop=None, retries=3):
        loop = loop or asyncio.get_event_loop()

        def get_info():
            ydl = yt_dlp.YoutubeDL(ydl_opts)
            if "https://www.youtube.com/" in query:
                info = ydl.extract_info(query, download=False)
            else:
                search_results = ydl.extract_info(f"ytsearch1:{query}", download=False)
                if not search_results['entries']:
                    return None
                info = search_results['entries'][0]
            return info

        for _ in range(retries):
            info = await loop.run_in_executor(None, get_info)

            if info is not None:
                return cls(info['url'], before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5')

            await asyncio.sleep(1)

        await ctx.send("❎ No videos found.")
        return None

async def update_song_url(song_info, max_retries=7, retry_delay=0.25):
    for _ in range(max_retries):
        try:
            with yt_dlp.YoutubeDL({'format': 'bestaudio/best'}) as ydl:
                video_info = ydl.extract_info(song_info['webpage_url'], download=False)
                return video_info['url']
        except Exception as e:
            print(f"Error updating the URL for the song '{song_info['title']}': {e}")
            print(traceback.format_exc())  # Add this line to log the traceback
            if _ < max_retries - 1:  # No need to sleep on the last iteration
                await asyncio.sleep(retry_delay)  # Add the sleep delay
        else:
            break
    return None


@bot.command(aliases=['p'], help='Play a song', usage='search/link')
@commands.cooldown(1, 5, commands.BucketType.user)
async def play(ctx, *, query):
    # Ensure the user is in a voice channel
    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    # Connect to the voice channel if not already connected
    channel = ctx.message.author.voice.channel
    if not ctx.voice_client:
        await channel.connect()
        await ctx.guild.change_voice_state(channel=channel, self_deaf=True)
    
    # Disconnect if the channel is empty
    if not channel.members:
        await ctx.voice_client.disconnect()
        return
    
    # Create a source for the audio
    source = await YTDLSource.create_source(ctx, query, loop=bot.loop)
    if source is None:
        return
    
    # Show typing indicator while processing
    async with ctx.typing():
        # Normalize and extract song information
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Regular expression pattern for YouTube URLs
            youtube_url_pattern = r"https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/)"
            if re.match(youtube_url_pattern, query):
                # Normalize the URL by stripping extraneous query parameters
                parsed_url = urlparse(query)
                video_id = parse_qs(parsed_url.query).get('v', [None])[0]
                # Rebuild a clean URL for 'youtu.be' links or use the regular YouTube link
                clean_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else query.split('?')[0]
                song = ydl.extract_info(clean_url, download=False)
            else:
                # Handle search queries
                search_results = ydl.extract_info(f"ytsearch1:{query}", download=False)
                if not search_results['entries']:
                    await ctx.send("❎ No videos found.")
                    return
                song = search_results['entries'][0]

        # Set the volume for the audio source
        volume = server_volumes.get(ctx.guild.id, 0.5)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(song['url'], before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'), volume=volume)

        # Play the song or add it to the queue
        if ctx.voice_client.is_playing():
            # Add to the queue
            queues.setdefault(ctx.guild.id, []).append(song)
            embed_title = f"✅ Added to the queue: {song['title']}"
        else:
            # Play now
            ctx.voice_client.play(source, after=lambda e: ctx.bot.loop.create_task(play_queue(ctx)))
            current_song_info.update({
                "title": song['title'],
                "thumbnail": song['thumbnail'],
                "uploader": song['uploader'],
                "webpage_url": song['webpage_url']
            })
            embed_title = f"Now Playing: {song['title']}"

        # Send an embed with song info
        embed = discord.Embed(title=embed_title, color=0x2ecc71)
        embed.set_thumbnail(url=song['thumbnail'])
        embed.add_field(name="Uploader", value=song['uploader'])
        await ctx.send(embed=embed)
        
#process queue and send messages on new songs added
async def play_queue(ctx):
    if not queues.get(ctx.guild.id) or not ctx.voice_client:
        await auto_disconnect(ctx)
        return

    song = queues[ctx.guild.id][0]

    # Check if song is a livestream
    if song.get('is_live'):
        pass

    # Check if song duration exceeds limit
    elif song.get('duration', 0) > 10800:
        await ctx.send("Error: The requested video is longer than 3 hours and cannot be played.")
        queues[ctx.guild.id].pop(0)
        await play_queue(ctx)  # Move this line here
        await auto_disconnect(ctx)
        return

    if not ctx.voice_client.is_playing():
        song = queues[ctx.guild.id].pop(0)

        # Update the song URL
        try:
            song['url'] = await update_song_url(song)
        except Exception as e:
            await ctx.send(f"Error updating the URL for the song '{song['title']}': {e}")
            await play_queue(ctx)  # Continue with the next song in the queue
            print(traceback.format_exc())  # Add this line to log the traceback
            return

        volume = server_volumes.get(ctx.guild.id, 0.5)
        source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(song['url'], before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'), volume=volume)

        ctx.voice_client.play(source, after=lambda e: ctx.bot.loop.create_task(play_queue(ctx)))

        # Create embed with song information
        embed = discord.Embed(title=f"Now Playing: {song['title']}", color=0xFF5733)
        embed.set_thumbnail(url=song['thumbnail'])
        embed.add_field(name="Uploader", value=song['uploader'], inline=False)

        # Send embed message
        embed = await ctx.send(embed=embed)

        # Clear current song info
        current_song_info["title"] = ""
        current_song_info["thumbnail"] = ""
        current_song_info["uploader"] = ""
        current_song_info["webpage_url"] = ""

        # Update current song info
        current_song_info["title"] = song['title']
        current_song_info["thumbnail"] = song['thumbnail']
        current_song_info["uploader"] = song['uploader']
        current_song_info["webpage_url"] = song['webpage_url']

        # Wait for the song to finish playing or for users to skip
        while ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
            await asyncio.sleep(1)

        # Check if there are more songs in the queue
        if queues.get(ctx.guild.id):
            await play_queue(ctx)
        else:
            await auto_disconnect(ctx)
    else:
        await ctx.send("Error: The bot is already playing a song.")


@bot.command(aliases=['rq'], help="Reset the music queue system and clean up music system.")
@commands.has_permissions(administrator=True)
async def resetq(ctx):
    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return
    global queues
    if ctx.guild.id in queues:
        del queues[ctx.guild.id]
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ctx.send("Music queue has been reset.")

async def check_voice_state():
    await bot.wait_until_ready()
    while not bot.is_closed():
        for vc in bot.voice_clients:
            if not vc.is_playing():
                await vc.disconnect()
                queues.pop(vc.guild.id, None)
        await asyncio.sleep(1) # checks every second
        bot.loop.create_task(check_voice_state())

current_song_info = {
    "title": "",
    "thumbnail": "",
    "uploader": "",
    "webpage_url": "",
}



@bot.command(aliases=['q', 'list', 'remote'], help='View the queue')
@commands.cooldown(1, 10, commands.BucketType.user)
async def queue(ctx):

    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    if ctx.guild.id not in queues or not queues[ctx.guild.id]:
        await ctx.send("There is no queue for this server.")
        return

    page = 0
    items_per_page = 5

    def create_queue_embed(page, queues, current_song_info):
        nonlocal ctx

        embed = discord.Embed(title='⏭️ Up Next - Queue', color=discord.Color.teal())
        current_song = current_song_info.get(ctx.guild.id)
        if current_song:
            embed.add_field(name='Now Playing', value=f"{current_song['title']}", inline=False)

        start_index = page * items_per_page
        end_index = start_index + items_per_page
        for i, song in enumerate(queues[ctx.guild.id][start_index:end_index]):
            embed.add_field(name=f"{i+1+start_index}. {song['title']}", value=f"\nURL: {song['webpage_url']}", inline=False)

        embed.set_footer(text=f"Page {page+1}")

        return embed

    queue_msg = await ctx.send(embed=create_queue_embed(page, queues, current_song_info))
    await queue_msg.add_reaction('❎')
    await queue_msg.add_reaction('⏸️')
    await queue_msg.add_reaction('▶️')
    await queue_msg.add_reaction('⏭️')
    await queue_msg.add_reaction('🔀')
    await queue_msg.add_reaction('❌')
    await queue_msg.add_reaction('💾')
    await queue_msg.add_reaction('⬅️')
    await queue_msg.add_reaction('➡️')
    
    def check(reaction, user):
        return (
            user == ctx.author 
            and str(reaction.emoji) in ['❎', '⏭️', '▶️', '⏸️', '🔀', '❌', '💾', '⬅️', '➡️']
            and reaction.message.id == queue_msg.id
            )
    
    while True:
        try:
            reaction, user = await bot.wait_for('reaction_add', timeout=10.0, check=check)
            await reaction.remove(user)
        except asyncio.TimeoutError:
            await queue_msg.clear_reactions()
            return
        if str(reaction.emoji) == '❎':
            await remove_song(ctx, queue_msg)
        elif str(reaction.emoji) == '⏭️':
            await skip_song(ctx)
        elif str(reaction.emoji) == '▶️':
            await resume(ctx)
        elif str(reaction.emoji) == '⏸️':
            await pause(ctx)
        elif str(reaction.emoji) == '🔀':
            await shuffle(ctx)
        elif str(reaction.emoji) == '❌':
            await stop(ctx)
        elif str(reaction.emoji) == '💾':
            await savenow(ctx)
        elif str(reaction.emoji) == '➡️':
            if (page + 1) * items_per_page < len(queues[ctx.guild.id]):
                page += 1
                await queue_msg.edit(embed=create_queue_embed(page, queues, current_song_info))

        elif str(reaction.emoji) == '⬅️':
            if page > 0:
                page -= 1
                await queue_msg.edit(embed=create_queue_embed(page, queues, current_song_info))


async def remove_song(ctx, queue_msg):
    await ctx.send(':fax: Which song would you like to remove from the queue?')
    try:
        msg = await bot.wait_for('message', timeout=20.0, check=lambda m: m.author == ctx.author)
        index = int(msg.content)
        if index < 1 or index > len(queues[ctx.guild.id]):
            await ctx.send('❎ I think that worked... do !q')
            return
        removed_song = queues[ctx.guild.id].pop(index-1)
        await ctx.send(f'✅ Removed "{removed_song["title"]}" from the queue.')
    except asyncio.TimeoutError:
        await ctx.send('❎ You took too long to respond.')
    except ValueError:
        await ctx.send('❎ Invalid input.')

@bot.command(name='savenow', help='💾 Save the currently playing song to a playlist', usage='playlist')
@commands.cooldown(1, 10, commands.BucketType.user)
async def savenow(ctx, *, playlist_name: str = None):

    if ctx.voice_client is None:
        await ctx.send("I'm not connected to a voice channel.")
        return

    if not ctx.voice_client.is_playing():
        await ctx.send("There's no song playing at the moment.")
        return

    user_id = ctx.author.id

    # Load playlists
    playlists = await load_playlists()
    str_user_id = str(user_id)
    
    if user_id not in playlists and str_user_id not in playlists:
        await ctx.send("You don't have any playlists.")
        return

    if user_id in playlists:
        user_playlists = playlists[user_id]
    else:
        user_playlists = playlists[str_user_id]

    if playlist_name is None:
        playlist_names = list(user_playlists.keys())
        await ctx.send("Please specify the playlist you want to save the current song to (enter the playlist name):\n" + '\n'.join(playlist_names))

        def check(m):
            return m.author == ctx.author and m.content in playlist_names

        try:
            msg = await bot.wait_for('message', timeout=30.0, check=check)
            playlist_name = msg.content
        except asyncio.TimeoutError:
            await ctx.send('You took too long to respond. Please try again.')
            return

    if playlist_name not in user_playlists:
        await ctx.send(f"Playlist '{playlist_name}' doesn't exist.")
        return

    user_playlists[playlist_name]['songs'].append(current_song_info)
    await ctx.send(f'Saved "{current_song_info["title"]}" to the playlist "{playlist_name}".')

    await save_playlists(playlists)


@bot.command(aliases=['np'], name='nowplaying', help='🎵 Shows current song info')
@commands.cooldown(1, 10, commands.BucketType.user)
async def nowplaying(ctx):


    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    if ctx.voice_client is None:
        await ctx.send("I'm not connected to a voice channel.")
        return

    if ctx.voice_client.is_playing():
        embed = discord.Embed(title=f"Now Playing: {current_song_info['title']}", color=0xFF5733)
        embed.set_thumbnail(url=current_song_info['thumbnail'])
        embed.add_field(name="Uploader", value=current_song_info['uploader'], inline=False)

        # Update current song info
        current_song_info["title"] = current_song_info['title']
        current_song_info["thumbnail"] = current_song_info['thumbnail']
        current_song_info["uploader"] = current_song_info['uploader']
        current_song_info["webpage_url"] = current_song_info['webpage_url']

        # Send embed message
        np_message = await ctx.send(embed=embed)

        await np_message.add_reaction('❎')
        await np_message.add_reaction('⏸️')
        await np_message.add_reaction('▶️')
        await np_message.add_reaction('⏭️')
        await np_message.add_reaction('🔀')
        await np_message.add_reaction('❌')
        await np_message.add_reaction('💾') 
        

        def check(reaction, user):
            return (
                user == ctx.author
                and str(reaction.emoji) in ['❎', '⏭️', '▶️', '⏸️', '🔀', '❌', '💾']
                and reaction.message.id == np_message.id
            )

        while True:
            try:
                reaction, user = await bot.wait_for('reaction_add', timeout=10.0, check=check)
                await reaction.remove(user)
            except asyncio.TimeoutError:
                await np_message.clear_reactions()
                return
            if str(reaction.emoji) == '❎':
                await remove_song(ctx, np_message)
            elif str(reaction.emoji) == '⏭️':
                await skip_song(ctx)
            elif str(reaction.emoji) == '▶️':
                await resume(ctx)
            elif str(reaction.emoji) == '⏸️':
                await pause(ctx)
            elif str(reaction.emoji) == '🔀':
                await shuffle(ctx)
            elif str(reaction.emoji) == '❌':
                await stop(ctx)
            elif str(reaction.emoji) == '💾':
                await savenow(ctx)

    else:
        await ctx.send("There's no song playing at the moment.")

server_volumes = {}

@bot.command(usage="number 1-10")
@commands.cooldown(1, 10, commands.BucketType.user)
async def volume(ctx, vol: int):
    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return
    if not ctx.voice_client or not ctx.voice_client.is_playing():
        await ctx.send("Error: The bot is not currently playing any audio.")
        return

    if vol < 0 or vol > 10:
        await ctx.send("Error: Please enter a volume value between 0 and 10.")
        return

    # Map the user input (0-10) to the volume scale (0-0.5)
    new_volume = vol / 20
    ctx.voice_client.source.volume = new_volume
    server_volumes[ctx.guild.id] = new_volume
    # Change the volume of the PCMVolumeTransformer

    await ctx.send(f"🔊 Volume has been set to {vol}/10.")

@bot.command(name='search', help='🔍 Search for a song on YouTube and add it to the queue', usage='song')
@commands.cooldown(1, 5, commands.BucketType.user)
async def search_youtube(ctx, *, query):

    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return
    # Inform the user that the bot is downloading YouTube song information
    info_msg = await ctx.send("⏳ Downloading YouTube song information, please wait...")
    async with ctx.typing():
        # Search YouTube for videos
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = ydl.extract_info(f"ytsearch5:{query}", download=False)
            videos = search_results.get('entries', [])

        # Delete the info message
        await info_msg.delete()

        # Check if any videos were found
        if not videos:
            await ctx.send("❎ No videos found.")
            return

        # Create embed with search results
        embed = discord.Embed(title=f"Search results for '{query}'", color=0xFF5733)
        for i, video in enumerate(videos):
            embed.add_field(name=f"{i + 1}. {video['title']}", value=f"[Link]({video['webpage_url']})", inline=False)

    # Send embed message with reaction buttons
    message = await ctx.send(embed=embed)
    for i in range(len(videos)):
        await message.add_reaction(f"{i + 1}\u20e3")

    # Function to check if the reaction is valid
    def check_reaction(reaction, user):
        return user == ctx.message.author and str(reaction.emoji) in [f"{i + 1}\u20e3" for i in range(len(videos))]

    try:
        # Wait for user to choose a video
        reaction, user = await bot.wait_for('reaction_add', check=check_reaction, timeout=30)

        # Add chosen video to the queue
        video_url = videos[int(str(reaction.emoji)[0]) - 1]['webpage_url']
        await ctx.invoke(play, query=video_url)

        # Remove reaction buttons
        await message.clear_reactions()

    except asyncio.TimeoutError:
        # Remove reaction buttons
        await message.clear_reactions()


@bot.command(aliases=['j'], help='Connect to a voice channel')
@commands.cooldown(1, 5, commands.BucketType.user)
async def join(ctx):

    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    if ctx.author.voice and ctx.author.voice.channel:
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
            await ctx.send(f"✅ Connected to {ctx.author.voice.channel}")
        else:
            await ctx.send("❎ I'm already in a voice channel.")
    else:
        await ctx.send("❎ You must be in a voice channel to use this command.")

@bot.command(aliases=['s', 'skip'], help='Skip the current song')
@commands.cooldown(1, 5, commands.BucketType.user)
async def skip_song(ctx):

    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    if ctx.voice_client:
        ctx.voice_client.stop()
        await ctx.send('⏭️ Skipped the song')
    else:
        await ctx.send('❎ Not currently playing any songs')

@bot.command(help='Shuffle the song queue')
async def shuffle(ctx):

    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return


    if ctx.guild.id not in queues:
        await ctx.send("❎ The queue is empty.")
        return

    random.shuffle(queues[ctx.guild.id])
    await ctx.send("✅ Queue shuffled.")

# Function to disconnect from the Voice Channel
@bot.command(aliases=['l'], help='Disconnect from a channel')
@commands.cooldown(1, 5, commands.BucketType.user)
async def leave(ctx):

    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    if not ctx.voice_client or not ctx.voice_client.is_connected():
        await ctx.send('❎ I am not currently connected to a voice channel.')
        return

    await ctx.voice_client.disconnect()
    queues.pop(ctx.guild.id, None)
    await ctx.message.channel.send('✅ Disconnected from the Voice Channel')

@bot.command(help='Stop the current song and clear the queue')
@commands.cooldown(1, 5, commands.BucketType.user)
async def stop(ctx):

    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    ctx.voice_client.stop()
    queues.pop(ctx.guild.id, None)
    await ctx.send('✅ Stopped the music and cleared the queue')

@bot.command(help='Pause the current song')
@commands.cooldown(1, 5, commands.BucketType.user)
async def pause(ctx):

    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    if ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send('⏸️ Paused the music.')
    else:
        await ctx.send('❎ Not currently playing any songs.')

@bot.command(aliases=['r'], help='Resume playback')
@commands.cooldown(1, 10, commands.BucketType.user)
async def resume(ctx):


    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    if not ctx.voice_client:
        await ctx.send('❎ I am not currently connected to a voice channel.')
        return
    if ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send('▶️ Resuming playback.')
    else:
        await ctx.send('❎ Playback is not currently paused.')
'''
 ____  _        _ __   ___     ___ ____ _____ 
|  _ \| |      / \\ \ / / |   |_ _/ ___|_   _|
| |_) | |     / _ \\ V /| |    | |\___ \ | |  
|  __/| |___ / ___ \| | | |___ | | ___) || |  
|_|   |_____/_/   \_\_| |_____|___|____/ |_|  
'''

async def load_playlists():
    playlists_file_path = os.path.join('storage', 'playlists.yml')
    if not os.path.exists(playlists_file_path):
        with open(playlists_file_path, 'w') as f:
            yaml.dump({}, f)

    with open(playlists_file_path, 'r') as f:
        playlists = yaml.safe_load(f) or {}
    return playlists

async def save_playlists(playlists):
    playlists_file_path = os.path.join('storage', 'playlists.yml')
    with open(playlists_file_path, 'w') as f:
        yaml.dump(playlists, f)

playlists_file_path = os.path.join('storage', 'playlists.yml')

@bot.group(name="playlist", aliases=['pl'], help='Create, list, or delete a playlist', usage='create, list, delete, add, play, view, viewother, profile, like, unlike, liked, setdesc', invoke_without_command=True)
@commands.cooldown(3, 20, commands.BucketType.user)
async def playlist_command(ctx):

    await ctx.send("Available playlist subcommands: create, list, delete, add, play, view, viewother, profile, like, unlike, liked, setdesc")

@playlist_command.command(name="create", usage='playlist name')
async def playlist_create(ctx, *args):

    user_id = str(ctx.author.id)

    playlists = await load_playlists()

    playlist_name = ' '.join(args)

    # Validation rules
    max_length = 20
    valid_pattern = re.compile(r'^[\w-]+$')  # Allows alphanumeric characters, underscores, and hyphens

    if len(playlist_name) > max_length:
        await ctx.send(f"Playlist name cannot be longer than {max_length} characters.")
        return

    if not valid_pattern.match(playlist_name):
        await ctx.send("Playlist name can only contain alphanumeric characters, underscores, and hyphens. Spaces and special characters are not allowed.")
        return

    if len(playlists.get(user_id, [])) >= 5:
        await ctx.send("You already have the maximum number of playlists.")
        return

    playlists.setdefault(user_id, {})[playlist_name] = {
        'description': 'No description.',
        'status': 'public',
        'likes': [],
        'songs': []
    }

    await save_playlists(playlists)

    embed = discord.Embed(title=f"Playlist '{playlist_name}' created.", color=0x2ecc71)
    await ctx.send(embed=embed)

@playlist_command.command(name="delete", usage='playlist name')
async def playlist_delete(ctx, *args):

    user_id = str(ctx.author.id)
    playlist_name = ' '.join(args)

    playlists = await load_playlists()

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send("You don't have a playlist with that name.")
        return

    del playlists[user_id][playlist_name]
    await save_playlists(playlists)

    embed = discord.Embed(title=f"Playlist '{playlist_name}' deleted.", color=0x2ecc71)
    await ctx.send(embed=embed)

@playlist_command.command(name="add", usage='playlist song name, song name, etc')
async def playlist_add(ctx, playlist_name: str, *song_queries):

    user_id = str(ctx.author.id)

    playlists = await load_playlists()

    # Join the song queries into one string, then split by comma
    song_queries = ' '.join(song_queries).split(',')

    if len(song_queries) > 10:
        await ctx.send("You can only add up to 10 songs at a time.")
        return

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"Playlist '{playlist_name}' doesn't exist.")
        return

    playlist_max_songs = 50
    available_slots = playlist_max_songs - len(playlists[user_id][playlist_name]['songs'])

    if len(song_queries) > available_slots:
        await ctx.send(f"You can only add {available_slots} more songs to this playlist.")
        return

    progress_msg = await ctx.send(f"Adding {len(song_queries)} songs to playlist '{playlist_name}'...")

    for i, song_query in enumerate(song_queries):
        song_query = song_query.strip()  # Remove leading/trailing whitespace from song query
        try:
            # Get video information
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if "https://www.youtube.com/" in song_query:
                    song_info = ydl.extract_info(song_query, download=False)
                else:
                    search_results = ydl.extract_info(f"ytsearch1:{song_query}", download=False)
                    if not search_results['entries']:
                        await ctx.send(f"❎ No videos found for '{song_query}'.")
                        continue
                    song_info = search_results['entries'][0]

            playlists[user_id][playlist_name]['songs'].append({
                'title': song_info['title'],
                'thumbnail': song_info['thumbnail'],
                'uploader': song_info['uploader'],
                'webpage_url': song_info['webpage_url']
            })

            embed = discord.Embed(title=f"Added '{song_info['title']}' to playlist '{playlist_name}'", color=0x2ecc71)
            embed.set_thumbnail(url=song_info['thumbnail'])
            embed.add_field(name="Uploader", value=song_info['uploader'])
            await ctx.send(embed=embed)

            await progress_msg.edit(content=f"Added {i + 1} out of {len(song_queries)} songs to playlist '{playlist_name}'...")

        except Exception as e:
            await ctx.send(f"An error occurred while adding '{song_query}' to the playlist: {e}")
            continue

    await progress_msg.edit(content=f"Finished adding {len(song_queries)} songs to playlist '{playlist_name}'.")

    # Save the updated playlist data to the file
    await save_playlists(playlists)

@playlist_command.command(name="play", usage='playlist name')
async def playlist_play(ctx, playlist_name: str):

    user_id = str(ctx.author.id)
    playlists = await load_playlists()

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"Playlist '{playlist_name}' doesn't exist.")
        return

    playlist_songs = playlists[user_id][playlist_name]['songs']

    if not playlist_songs:
        await ctx.send(f"Playlist '{playlist_name}' is empty.")
        return
    if not ctx.message.author.voice:
        await ctx.send("You must be connected to a voice channel to use this command.")
        return

    # Add all songs in the playlist to the music queue
    if ctx.guild.id not in queues:
        queues[ctx.guild.id] = []

    queues[ctx.guild.id].extend(playlist_songs)

    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await channel.connect()
        await ctx.guild.change_voice_state(channel=channel, self_deaf=True)

    # Notify about the added songs regardless of the playing state
    await ctx.send(f"Added all songs from playlist '{playlist_name}' to the music queue.")

    # Start playing the first song in the queue if the bot is not playing a song
    if not ctx.voice_client.is_playing():
        await play_queue(ctx)

@playlist_command.command(name="view", usage='playlist name')
async def playlist_view(ctx, playlist_name: str):

    user_id = str(ctx.author.id)
    playlists_file_path = os.path.join('storage', 'playlists.yml')

    playlists = await load_playlists()

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"Playlist '{playlist_name}' doesn't exist.")
        return

    playlist_songs = playlists[user_id][playlist_name]['songs']

    if not playlist_songs:
        await ctx.send(f"Playlist '{playlist_name}' is empty.")
        return

    songs_per_page = 10
    num_pages = (len(playlist_songs) - 1) // songs_per_page + 1
    current_page = 0

    def create_embed(page):
        start = page * songs_per_page
        end = min((page + 1) * songs_per_page, len(playlist_songs))
        num_likes = len(playlists[user_id][playlist_name]['likes'])  # Get the number of likes

        embed = discord.Embed(title=f"🎵 Playlist: {playlist_name} (Page {page+1}/{num_pages})", description=f"❤️ {num_likes} Likes", color=discord.Color.teal())

        for i, song in enumerate(playlist_songs[start:end]):
            embed.add_field(name=f"{start+i+1}. {song['title']}", value=f"Uploader: {song['uploader']}", inline=False)

        return embed

    
    playlist_msg = await ctx.send(embed=create_embed(current_page))
    await playlist_msg.add_reaction('🗑️')
    await playlist_msg.add_reaction('🔀')
    await playlist_msg.add_reaction('⬅️')
    await playlist_msg.add_reaction('➡️')
    await playlist_msg.add_reaction('▶️')
    await playlist_msg.add_reaction('📝')  # Add button for editing description
    await playlist_msg.add_reaction('🔓')  # Add button for changing status
    await playlist_msg.add_reaction('✏️') 


    def check(reaction, user):
        return (
            user == ctx.author
            and str(reaction.emoji) in ['❌', '🔀', '🗑️', '⬅️', '➡️', '▶️', '📝', '🔓', '✏️']
            and reaction.message.id == playlist_msg.id
        )

    while True:
        try:
            reaction, user = await bot.wait_for('reaction_add', timeout=20.0, check=check)
            await reaction.remove(user)

            if str(reaction.emoji) == '⬅️':
                current_page = (current_page - 1) % num_pages
            elif str(reaction.emoji) == '➡️':
                current_page = (current_page + 1) % num_pages

            elif str(reaction.emoji) == '🗑️':
                await ctx.send('Which song would you like to remove from the playlist? (Enter the number)')
                try:
                    msg = await bot.wait_for('message', timeout=20.0, check=lambda m: m.author == ctx.author)
                    index = int(msg.content)
                    if index < 1 or index > len(playlists[user_id][playlist_name]['songs']):
                        await ctx.send('Invalid song number.')
                        return
                    removed_song = playlists[user_id][playlist_name]['songs'].pop(index-1)
                    await ctx.send(f'Removed "{removed_song["title"]}" from the playlist.')

                    await save_playlists(playlists)

                    # Update the embed
                    embed = create_embed(current_page)
                    await playlist_msg.edit(embed=embed)

                except asyncio.TimeoutError:
                    await ctx.send('You took too long to respond.')
                except ValueError:
                    await ctx.send('Invalid input.')

            elif str(reaction.emoji) == '🔀':
                random.shuffle(playlists[user_id][playlist_name]['songs'])

                await save_playlists(playlists)

                # Update the embed
                embed = create_embed(current_page)
                await playlist_msg.edit(embed=embed)

                await ctx.send('Playlist shuffled!')

            elif str(reaction.emoji) == '▶️':
                if not ctx.message.author.voice:
                    await ctx.send("You must be connected to a voice channel to use this command.")
                    return
                if ctx.guild.id not in queues:
                    queues[ctx.guild.id] = []

                queues[ctx.guild.id].extend(playlist_songs)
                await ctx.send(f"Added to queue: {ctx.author.display_name}'s playlist '{playlist_name}'.")
                channel = ctx.author.voice.channel
                if ctx.voice_client is None:
                    await channel.connect()
                    await ctx.guild.change_voice_state(channel=channel, self_deaf=True)

                if not ctx.voice_client.is_playing():
                    await play_queue(ctx)
                await playlist_msg.delete()
                

                return
            
            elif str(reaction.emoji) == '📝':
                await ctx.send('Enter a new description for the playlist:')

                try:
                    msg = await bot.wait_for('message', timeout=20.0, check=lambda m: m.author == ctx.author)
                    new_description = msg.content

                    playlists[user_id][playlist_name]['description'] = new_description
                    await save_playlists(playlists)

                    await ctx.send(f"Playlist description updated to '{new_description}'.")

                except asyncio.TimeoutError:
                    await ctx.send('You took too long to respond.')

            # Update status
            elif str(reaction.emoji) == '🔓':
                new_status = 'public' if playlists[user_id][playlist_name]['status'] == 'private' else 'private'

                playlists[user_id][playlist_name]['status'] = new_status
                await save_playlists(playlists)

                await ctx.send(f"Playlist status updated to '{new_status}'.")

            # Rename playlist
            elif str(reaction.emoji) == '✏️':
                await ctx.send('Enter a new name for the playlist:')

                try:
                    msg = await bot.wait_for('message', timeout=20.0, check=lambda m: m.author == ctx.author)
                    new_name = msg.content

                    if new_name in playlists[user_id]:
                        await ctx.send("A playlist with that name already exists.")
                    else:
                        playlists[user_id][new_name] = playlists[user_id].pop(playlist_name)
                        playlist_name = new_name
                        await save_playlists(playlists)

                        await ctx.send(f"Playlist name updated to '{new_name}'.")

                except asyncio.TimeoutError:
                    await ctx.send('You took too long to respond.')

            await playlist_msg.edit(embed=create_embed(current_page))
        except asyncio.TimeoutError:
            await playlist_msg.clear_reactions()
            await playlist_msg.delete()
            return

@playlist_command.command(name="viewother", usage='user playlist name')
async def playlist_viewother(ctx, target_user: discord.Member, playlist_name: str):

    user_id = str(ctx.author.id)
    target_user_id = str(target_user.id)

    playlists = await load_playlists()

    if target_user_id not in playlists or playlist_name not in playlists[target_user_id]:
        await ctx.send(f"{target_user.display_name} doesn't have a playlist with that name.")
        return

    playlist = playlists[target_user_id][playlist_name]
    playlist_songs = playlist['songs']

    if not playlist_songs:
        await ctx.send(f"Playlist '{playlist_name}' is empty.")
        return

    songs_per_page = 10
    num_pages = (len(playlist_songs) - 1) // songs_per_page + 1
    current_page = 0

    def create_embed(page):
        start = page * songs_per_page
        end = min((page + 1) * songs_per_page, len(playlist_songs))
        num_likes = len(playlists[target_user_id][playlist_name]['likes'])  # Get the number of likes

        embed = discord.Embed(title=f"{target_user.display_name}'s Playlist: {playlist_name} (Page {page+1}/{num_pages})", description=f"❤️ {num_likes} Likes", color=discord.Color.blue())

        for i, song in enumerate(playlist_songs[start:end]):
            embed.add_field(name=f"{start+i+1}. {song['title']}", value=song['uploader'], inline=False)

        # Add the description field to the embed
        embed.add_field(name="Description", value=playlist['description'], inline=False)

        return embed


    playlist_msg = await ctx.send(embed=create_embed(current_page))
    await playlist_msg.add_reaction('⬅️')
    await playlist_msg.add_reaction('➡️')
    await playlist_msg.add_reaction('▶️')
    await playlist_msg.add_reaction('❤️')

    def check(reaction, user):
        return (
            user == ctx.author
            and str(reaction.emoji) in ['⬅️', '➡️', '▶️', '❤️']
            and reaction.message.id == playlist_msg.id
        )

    while True:
        try:
            reaction, user = await bot.wait_for('reaction_add', timeout=20.0, check=check)
            await reaction.remove(user)

            if str(reaction.emoji) == '⬅️':
                current_page = (current_page - 1) % num_pages
            elif str(reaction.emoji) == '➡️':
                current_page = (current_page + 1) % num_pages
            elif str(reaction.emoji) == '▶️':
                if not ctx.message.author.voice:
                    await ctx.send("You must be connected to a voice channel to use this command.")
                    return
                if ctx.guild.id not in queues:
                    queues[ctx.guild.id] = []


                queues[ctx.guild.id].extend(playlist_songs)
                await ctx.send(f"Added to queue: {ctx.author.display_name}'s playlist '{playlist_name}'.")
                channel = ctx.author.voice.channel
                if ctx.voice_client is None:
                    await channel.connect()
                    await ctx.guild.change_voice_state(channel=channel, self_deaf=True)

                if not ctx.voice_client.is_playing():
                    await play_queue(ctx)
                await playlist_msg.delete()
                

                return
            elif str(reaction.emoji) == '❤️':
                user_id = str(ctx.author.id)
                if 'likes' not in playlist:
                    playlist['likes'] = []

                if user_id in playlist['likes']:
                    playlist['likes'].remove(user_id)  # Remove the user_id from the likes list
                    await ctx.send(f"You've unliked {target_user.display_name}'s playlist '{playlist_name}'.")
                else:
                    playlist['likes'].append(user_id)
                    await ctx.send(f"You've liked {target_user.display_name}'s playlist '{playlist_name}'.")
                    
                # Save the modified playlists data to storage/playlists.yml
                await save_playlists(playlists)


            await playlist_msg.edit(embed=create_embed(current_page))

        except asyncio.TimeoutError:
            await playlist_msg.clear_reactions()
            return

@playlist_command.command(name="profile", usage='user')
async def playlist_profile(ctx, target_user: discord.Member = None):

    playlists_file_path = os.path.join('storage', 'playlists.yml')

    if not os.path.exists(playlists_file_path):
        await save_playlists(playlists)

    playlists = await load_playlists()

    if target_user is None:
        target_user = ctx.author

    target_user_id = str(target_user.id)

    if target_user_id not in playlists:
        await ctx.send(f"{target_user.display_name} doesn't have any playlists.")
        return

    def create_embed():
        embed = discord.Embed(title=f"{target_user.display_name}'s Playlists", color=discord.Color.blue())

        for i, playlist_name in enumerate(playlists[target_user_id].keys()):
            playlist = playlists[target_user_id][playlist_name]
            num_likes = len(playlist['likes'])  # Get the number of likes

            if playlist['status'] == 'private':
                embed.add_field(name=f"{i+1}. Hidden Playlist", value="Private", inline=False)
            else:
                description = playlist.get('description', 'No description.')
                embed.add_field(name=f"{i+1}. {playlist_name}", value=f"❤️ {num_likes} {description}", inline=False)

        return embed


    profile_msg = await ctx.send(embed=create_embed())
    number_emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣']

    for i, emoji in enumerate(number_emojis[:len(playlists[target_user_id])]):
        await profile_msg.add_reaction(emoji)

    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in number_emojis

    while True:
        try:
            reaction, user = await bot.wait_for('reaction_add', timeout=60.0, check=check)
            await reaction.remove(user)

            selected_index = number_emojis.index(str(reaction.emoji))
            if selected_index < len(playlists[target_user_id]):
                selected_playlist_name = list(playlists[target_user_id].keys())[selected_index]
                await profile_msg.delete()  # Delete the playlist list menu

                if target_user == ctx.author:
                    await playlist_view(ctx, selected_playlist_name)
                else:
                    await playlist_viewother(ctx, target_user, selected_playlist_name)

                return

        except asyncio.TimeoutError:
            await profile_msg.clear_reactions()
            return

@playlist_command.command(name="status", usage='playlist <public/private>')
async def playlist_status(ctx, playlist_name: str, status: str = None):

    user_id = str(ctx.author.id)
    playlists_file_path = os.path.join('storage', 'playlists.yml')

    if not os.path.exists(playlists_file_path):
        await save_playlists(playlists)

    playlists = await load_playlists()

    if status is None:
        await ctx.send("Usage: `!pl status <playlistname> <public/private>`")
        return

    new_status = status.lower()

    if new_status not in ['public', 'private']:
        await ctx.send("Invalid status. Please enter either 'public' or 'private'.")
        return

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send(f"You don't have a playlist with that name.")
        return

    playlists[user_id][playlist_name]['status'] = new_status
    await ctx.send(f"Playlist '{playlist_name}' is now {new_status}.")

    # Save the modified playlists data to storage/playlists.yml
    await save_playlists(playlists)

@playlist_command.command(name="description", aliases=['setdesc', 'desc'], usage='playlist <description>')
async def playlist_description(ctx, playlist_name: str, *args):

    user_id = str(ctx.author.id)

    if not os.path.exists(playlists_file_path):
        await save_playlists(playlists)

    playlists = await load_playlists()

    if not args:
        await ctx.send("Usage: `!playlist setdesc <playlistname> <description>`")
        return

    description = ' '.join(args)

    if len(description) > 200:
        await ctx.send("The description is too long. Maximum 200 characters allowed.")
        return

    if user_id not in playlists or playlist_name not in playlists[user_id]:
        await ctx.send("You don't have a playlist with that name.")
        return

    playlists[user_id][playlist_name]['description'] = description

    await save_playlists(playlists)

    embed = discord.Embed(title=f"Playlist '{playlist_name}' description updated.", description=description, color=0x2ecc71)
    await ctx.send(embed=embed)

@playlist_command.command(name="like", usage='user playlist')
async def playlist_like(ctx, target_user: discord.Member, *args):

    user_id = str(ctx.author.id)
    playlists_file_path = os.path.join('storage', 'playlists.yml')

    if not os.path.exists(playlists_file_path):
        await save_playlists(playlists)

    playlists = await load_playlists()

    if not args or not ctx.message.mentions:
        await ctx.send("Usage: `!pl like <user_mention> <playlistname>`")
        return

    target_user = ctx.message.mentions[0]
    target_user_id = str(target_user.id)
    playlist_name = ' '.join(args)  # Changed from args[1:] to args

    if target_user_id not in playlists or playlist_name not in playlists[target_user_id]:
        await ctx.send(f"{target_user.display_name} doesn't have a playlist with that name.")
        return

    user_id = str(ctx.author.id)
    
    if 'likes' not in playlists[target_user_id][playlist_name]:
        playlists[target_user_id][playlist_name]['likes'] = []

    if user_id in playlists[target_user_id][playlist_name]['likes']:
        await ctx.send("You've already liked this playlist.")
    else:
        playlists[target_user_id][playlist_name]['likes'].append(user_id)
        await ctx.send(f"You've liked {target_user.display_name}'s playlist '{playlist_name}'.")
    
    # Save the modified playlists data to storage/playlists.yml
    await save_playlists(playlists)

@playlist_command.command(name="unlike", usage='user playlist')
async def playlist_unlike(ctx, target_user: discord.Member, *args):

    user_id = str(ctx.author.id)

    if not os.path.exists(playlists_file_path):
        await save_playlists(playlists)

    playlists = await load_playlists()

    if not args or not ctx.message.mentions:
        await ctx.send("Usage: `!pl unlike <user_mention> <playlistname>`")
        return

    target_user = ctx.message.mentions[0]
    target_user_id = str(target_user.id)
    playlist_name = ' '.join(args)

    if target_user_id not in playlists or playlist_name not in playlists[target_user_id]:
        await ctx.send(f"{target_user.display_name} doesn't have a playlist with that name.")
        return

    user_id = str(ctx.author.id)
    
    if 'likes' not in playlists[target_user_id][playlist_name]:
        playlists[target_user_id][playlist_name]['likes'] = []

    if user_id not in playlists[target_user_id][playlist_name]['likes']:
        await ctx.send("You haven't liked this playlist.")
    else:
        playlists[target_user_id][playlist_name]['likes'].remove(user_id)
        await ctx.send(f"You've unliked {target_user.display_name}'s playlist '{playlist_name}'.")
    
    # Save the modified playlists data to storage/playlists.yml
    await save_playlists(playlists)

@playlist_command.command(name="liked")
async def playlist_liked(ctx):

    user_id = str(ctx.author.id)
    playlists_file_path = os.path.join('storage', 'playlists.yml')
    if not os.path.exists(playlists_file_path):
        await save_playlists(playlists)

    playlists = await load_playlists()
    user_id = str(ctx.author.id)

    # Find all liked playlists
    liked_playlists = []
    for target_user_id, user_playlists in playlists.items():
        for playlist_name, playlist_data in user_playlists.items():
            if 'likes' in playlist_data and user_id in playlist_data['likes']:
                liked_playlists.append((target_user_id, playlist_name))

    if not liked_playlists:
        await ctx.send("You haven't liked any playlists.")
        return

    async def create_embed():
        embed = discord.Embed(title=f"{ctx.author.display_name}'s Liked Playlists", color=discord.Color.blue())

        for i, (target_user_id, playlist_name) in enumerate(liked_playlists):
            playlist = playlists[target_user_id][playlist_name]
            target_user = await bot.fetch_user(int(target_user_id))
            num_likes = len(playlist['likes'])  # Get the number of likes
            description = playlist.get('description', 'No description.')

            embed.add_field(name=f"{i+1}. {playlist_name} by {target_user.display_name}", value=f"❤️ {num_likes} {description}", inline=False)

        return embed

    liked_msg = await ctx.send(embed=await create_embed())
    number_emojis = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣']

    for i, emoji in enumerate(number_emojis[:len(liked_playlists)]):
        await liked_msg.add_reaction(emoji)

    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in number_emojis

    while True:
        try:
            reaction, user = await bot.wait_for('reaction_add', timeout=10.0, check=check)
            await reaction.remove(user)

            selected_index = number_emojis.index(str(reaction.emoji))
            if selected_index < len(liked_playlists):
                target_user_id, selected_playlist_name = liked_playlists[selected_index]
                await liked_msg.delete()  # Delete the playlist list menu
                target_user = await bot.fetch_user(int(target_user_id))
                await playlist_viewother(ctx, target_user, selected_playlist_name)  # Display the selected playlist
                return

        except asyncio.TimeoutError:
            await liked_msg.clear_reactions()
            return


@bot.event
async def on_command(ctx):
    command = ctx.command.name
    user = ctx.author
    server = ctx.guild.name
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Format and color the message
    message = f"{colored(timestamp, 'cyan')} {colored(user, 'yellow')} executed command: {colored(command, 'green')} in server {colored(server, 'magenta')}"
    print(message)


logger = logging.getLogger('discord')
logger.setLevel(logging.ERROR)
handler = logging.FileHandler(filename='error.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

@bot.event
async def on_command_error(ctx, error):
    ignored_errors = (commands.CommandNotFound, )
    if isinstance(error, ignored_errors):
        return
    elif isinstance(error, commands.CommandOnCooldown):
        seconds = error.retry_after
        await ctx.send(f"Stop spamming! :rage: wait {seconds:.0f} seconds before using this command again.")
    elif isinstance(error, commands.MissingPermissions):
        missing_permissions = ', '.join(error.missing_permissions)
        await ctx.send(f"You don't have the required permissions for this command: {missing_permissions}")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing required argument. Usage: `{ctx.prefix}{ctx.command} {ctx.command.usage}`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"Bad argument. Usage: `{ctx.prefix}{ctx.command} {ctx.command.usage}`")
    elif isinstance(error, commands.BotMissingPermissions):
        missing_permissions = ', '.join(error.missing_permissions)
        await ctx.send(f"I don't have the required permissions for this command: {missing_permissions}")
    elif isinstance(error, discord.Forbidden):
        await ctx.send(f"I don't have the necessary permissions to do that in this server or channel.")
    elif isinstance(error, commands.DisabledCommand):
        await ctx.send("This command is currently disabled.")
    elif isinstance(error, commands.CheckFailure):
        await ctx.send("You do not meet the requirements to use this command.")
    elif isinstance(error, commands.CommandInvokeError):
        if isinstance(error.original, discord.Forbidden):
            await ctx.send(f"I don't have the necessary permissions to do that in this server or channel.")
            return
        elif isinstance(error.original, commands.BadArgument):
            await ctx.send(f"Bad argument. Usage: `{ctx.prefix}{ctx.command} {ctx.command.usage}`")
            return
        elif isinstance(error.original, commands.MissingRequiredArgument):
            await ctx.send(f"Missing required argument. Usage: `{ctx.prefix}{ctx.command} {ctx.command.usage}`")
            return
        else:
            error_message = f"{colored('ERROR', 'red')} {colored(ctx.command, 'yellow')}: {type(error).__name__} - {error}"
            print(error_message)
            await ctx.send("An error occurred while processing your command.")
            traceback.print_exception(type(error), error, error.__traceback__)
            log_message = f"Server: {ctx.guild.name}, User: {ctx.author.name}, Command: {ctx.command}, Error: {type(error).__name__} - {error}"
            logger.error(log_message)
            with open('error.log', 'a') as f:
                traceback.print_exception(type(error), error, error.__traceback__, file=f)
    print(f"{colored('ERROR', 'red')} {colored(ctx.command, 'yellow')}: {type(error).__name__} - {error}")
    if hasattr(error, 'original') and isinstance(error.original, Exception):
        traceback.print_exception(type(error), error, error.__traceback__)

@bot.event
async def on_error(event, *args, **kwargs):
    exc_type, exc_value, exc_traceback = sys.exc_info()
    print(f"{colored('ERROR', 'red')} {colored(event, 'yellow')}: {exc_type.__name__} - {exc_value}")
    traceback.print_exception(exc_type, exc_value, exc_traceback)
    log_message = f"Event: {event}, Error: {exc_type.__name__} - {exc_value}"
    logger.error(log_message)
    with open('error.log', 'a') as f:
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=f)

# Read the config.yml file
with open("config.yml", "r") as config_file:
    config_data = yaml.safe_load(config_file)

# Get the discord_token from the config_data
bot_token = config_data["discord_token"]

# Run the bot using the token
bot.run(bot_token)