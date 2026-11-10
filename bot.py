import asyncio
import json
import os
import random
import threading
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
from flask import Flask, request, jsonify

# Configuration des intensions Discord
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Configuration de Flask
basedir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)

import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

# Nouvel ID du salon de musique mis à jour
SALON_MUSIQUE_ID = 1565071498329194676
deja_connecte = False

queue_musiques = []
derniere_musique = None
historique_musiques = []
musique_en_cours = None

PLAYLISTS_FILE = os.path.join(basedir, "playlists.json")

def charger_playlists():
    if os.path.exists(PLAYLISTS_FILE):
        try:
            with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def sauvegarder_playlists():
    with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
        json.dump(playlists_globales, f, ensure_ascii=False, indent=4)

playlists_globales = charger_playlists()

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'ytsearch8',
    'source_address': '0.0.0.0'
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

def get_volume_bar(volume_level):
    blocks = int(volume_level / 10)
    bar = "#" * blocks + "-" * (10 - blocks)
    return f"Volume actuel : [{bar}] {int(volume_level)}%"

def format_duration(seconds):
    if not seconds:
        return "Inconnue"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

class MusicControlView(discord.ui.View):
    def __init__(self, voice_client):
        super().__init__(timeout=None)
        self.voice_client = voice_client
        self.current_volume = 100

    @discord.ui.button(label="Pause / Reprendre", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.voice_client or not self.voice_client.is_connected():
            return await interaction.response.send_message("Le bot n'est pas connecté à un salon vocal.", ephemeral=True)

        if self.voice_client.is_playing():
            self.voice_client.pause()
            await interaction.response.send_message("Musique mise en pause.", ephemeral=True)
        elif self.voice_client.is_paused():
            self.voice_client.resume()
            await interaction.response.send_message("Musique reprise !", ephemeral=True)
        else:
            await interaction.response.send_message("Aucune musique ne joue actuellement.", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()
            await interaction.response.send_message("Musique passée !", ephemeral=True)
        else:
            await interaction.response.send_message("Aucune musique à passer.", ephemeral=True)

    @discord.ui.button(label="Stopper", style=discord.ButtonStyle.danger, row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global musique_en_cours
        if self.voice_client and self.voice_client.is_connected():
            queue_musiques.clear()
            musique_en_cours = None
            await self.voice_client.disconnect()
            await interaction.response.send_message("Lecture arrêtée et bot déconnecté du vocal.", ephemeral=True)
            self.stop()
        else:
            await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)

@bot.event
async def on_ready():
    global deja_connecte
    print(f"Connecté en tant que {bot.user.name}")

    if not deja_connecte:
        deja_connecte = True
        channel = bot.get_channel(SALON_MUSIQUE_ID)
        if channel:
            try:
                await channel.send("Le bot FUZE III est connecté et prêt !")
            except Exception as e:
                print(f"Erreur envoi message activation : {e}")

        try:
            synced = await bot.tree.sync()
            print(f"Synchronisé {len(synced)} commande(s) slash.")
        except Exception as e:
            print(f"Erreur de synchronisation : {e}")

async def jouer_prochaine_musique(interaction_or_guild=None):
    global derniere_musique, musique_en_cours
    if not queue_musiques:
        musique_en_cours = None
        return

    item = queue_musiques.pop(0)
    prochain = item['recherche'] if isinstance(item, dict) else item
    nom_playlist = item.get('playlist', 'Recherche / Lien direct') if isinstance(item, dict) else 'Recherche / Lien direct'

    derniere_musique = prochain

    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(prochain, download=False))
        if 'entries' in data:
            data = data['entries'][0]
    except Exception as e:
        print(f"Erreur extraction audio : {e}")
        return

    url = data['url']
    title = data.get('title', 'Musique sans titre')
    thumbnail = data.get('thumbnail', None)
    uploader = data.get('uploader', 'Artiste Inconnu')
    duration = format_duration(data.get('duration', 0))

    musique_en_cours = {
        'titre': title,
        'uploader': uploader,
        'duree': duration,
        'url_web': data.get('webpage_url', ''),
        'thumbnail': thumbnail,
        'playlist': nom_playlist
    }

    historique_musiques.append(title)
    if len(historique_musiques) > 20:
        historique_musiques.pop(0)

    voice_client = None
    for guild in bot.guilds:
        if guild.voice_client and guild.voice_client.is_connected():
            voice_client = guild.voice_client
            break

    if not voice_client:
        return

    audio_source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTIONS))
    
    def apres_lecture(error):
        if queue_musiques and voice_client.is_connected():
            coro = jouer_prochaine_musique()
            fut = asyncio.run_coroutine_threadsafe(coro, bot.loop)
            try:
                fut.result()
            except Exception:
                pass
        else:
            global musique_en_cours
            musique_en_cours = None

    voice_client.play(audio_source, after=apres_lecture)

# Commandes Discord Slash
@bot.tree.command(name="site", description="Affiche le lien du tableau de bord web FUZE III")
async def site(interaction: discord.Interaction):
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="Ouvrir le Site Web", style=discord.ButtonStyle.link, url="http://127.0.0.1:5001"))
    await interaction.response.send_message("🌐 Clique ci-dessous pour ouvrir le Dashboard Web :", view=view, ephemeral=True)

@bot.tree.command(name="volume", description="Règle le volume sonore du bot (0 à 100)")
@app_commands.describe(niveau="Pourcentage du volume")
async def volume(interaction: discord.Interaction, niveau: int):
    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_playing():
        return await interaction.response.send_message("Aucune musique en cours.", ephemeral=True)
    if niveau < 0 or niveau > 100:
        return await interaction.response.send_message("Merci de préciser un chiffre entre 0 et 100.", ephemeral=True)
    voice_client.source.volume = niveau / 100
    await interaction.response.send_message(get_volume_bar(niveau))

@bot.tree.command(name="queue", description="Affiche la file d'attente actuelle")
async def queue(interaction: discord.Interaction):
    if not queue_musiques:
        return await interaction.response.send_message("File d'attente vide.", ephemeral=True)
    embed = discord.Embed(title="File d'attente", color=discord.Color.orange())
    liste = [f"**{i+1}.** {item['recherche'] if isinstance(item, dict) else item}" for i, item in enumerate(queue_musiques[:15])]
    embed.description = "\n".join(liste)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="info", description="Affiche les détails du morceau en cours")
async def info(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client or not musique_en_cours:
        return await interaction.response.send_message("Aucune musique en cours.", ephemeral=True)

    embed = discord.Embed(title="En cours de lecture", description=f"**[{musique_en_cours['titre']}]({musique_en_cours['url_web']})**", color=discord.Color.purple())
    embed.add_field(name="Artiste", value=f"`{musique_en_cours['uploader']}`", inline=True)
    embed.add_field(name="Durée", value=f"`{musique_en_cours['duree']}`", inline=True)
    if musique_en_cours.get('thumbnail'):
        embed.set_image(url=musique_en_cours['thumbnail'])
    view = MusicControlView(voice_client)
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="history", description="Affiche l'historique des dernières écoutes")
async def history(interaction: discord.Interaction):
    if not historique_musiques:
        return await interaction.response.send_message("L'historique est vide.", ephemeral=True)
    embed = discord.Embed(title="Historique des écoutes", color=discord.Color.purple())
    desc = "\n".join([f"**{i}.** {son}" for i, son in enumerate(reversed(historique_musiques), 1)])
    embed.description = desc
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="replay", description="Relance la dernière musique jouée")
async def replay(interaction: discord.Interaction):
    global derniere_musique
    if not derniere_musique:
        return await interaction.response.send_message("Aucune musique précédente à relancer.", ephemeral=True)
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("Tu dois être dans un salon vocal !", ephemeral=True)
    
    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client

    if not voice_client:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    queue_musiques.insert(0, {'recherche': derniere_musique, 'playlist': 'Replay'})
    if voice_client.is_playing():
        voice_client.stop()
        await interaction.response.send_message(f"🔄 Relancement : **{derniere_musique}**")
    else:
        await interaction.response.send_message(f"🔄 Lancement : **{derniere_musique}**")
        await jouer_prochaine_musique(interaction)

@bot.tree.command(name="skip", description="Passe la musique actuelle")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop()
        await interaction.response.send_message("Musique passée.")
    else:
        await interaction.response.send_message("Aucune musique à passer.", ephemeral=True)

@bot.tree.command(name="join", description="Fait rejoindre le bot dans ton salon vocal")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.response.send_message("Tu dois être dans un salon vocal !", ephemeral=True)
    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client
    if not voice_client:
        await voice_channel.connect()
        await interaction.response.send_message(f"Rejoint {voice_channel.name} !")
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)
        await interaction.response.send_message(f"Déplacé vers {voice_channel.name} !")
    else:
        await interaction.response.send_message("Je suis déjà dans ton salon !", ephemeral=True)

@bot.tree.command(name="leave", description="Fait quitter le salon vocal au bot")
@app_commands.checks.has_permissions(administrator=True)
async def leave(interaction: discord.Interaction):
    global musique_en_cours
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        queue_musiques.clear()
        musique_en_cours = None
        await voice_client.disconnect()
        await interaction.response.send_message("Le bot a quitté le vocal.")
    else:
        await interaction.response.send_message("Le bot n'est connecté à aucun salon.", ephemeral=True)

@bot.tree.command(name="stop", description="Arrête la musique et déconnecte le bot")
@app_commands.checks.has_permissions(administrator=True)
async def stop(interaction: discord.Interaction):
    global musique_en_cours
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.is_connected():
        queue_musiques.clear()
        musique_en_cours = None
        await voice_client.disconnect()
        await interaction.response.send_message("Lecture arrêtée et déconnecté.")
    else:
        await interaction.response.send_message("Le bot n'est pas connecté.", ephemeral=True)

@bot.tree.command(name="clear_all", description="Réinitialise entièrement le salon textuel")
async def clear_all(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("Permissions insuffisantes !", ephemeral=True)
    await interaction.response.send_message("Réinitialisation complète du salon...", ephemeral=True)
    channel = interaction.channel
    position = channel.position
    new_channel = await channel.clone(reason="Purge complète")
    await new_channel.edit(position=position)
    await channel.delete(reason="Purge complète")
    await new_channel.send("Salon réinitialisé à zéro !")

@bot.tree.command(name="eteindre", description="Éteint le bot à distance")
async def eteindre(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("Seuls les administrateurs peuvent éteindre le bot !", ephemeral=True)
    await interaction.response.send_message("Le bot FUZE III s'éteint. À la prochaine !")
    await bot.close()

# Groupe /playlist
playlist_group = app_commands.Group(name="playlist", description="Gestion des playlists")

class CreatePlaylistModal(discord.ui.Modal, title="Créer une nouvelle playlist"):
    nom = discord.ui.TextInput(label="Nom de la playlist", placeholder="ex: banger only", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        nom_pl = self.nom.value.strip()
        if nom_pl not in playlists_globales:
            playlists_globales[nom_pl] = []
            sauvegarder_playlists()
            await interaction.response.send_message(f"Playlist **{nom_pl}** créée avec succès !", ephemeral=True)
        else:
            await interaction.response.send_message(f"La playlist **{nom_pl}** existe déjà.", ephemeral=True)

class AddMultipleSongsModal(discord.ui.Modal, title="Ajouter des morceaux"):
    nom_playlist = discord.ui.TextInput(label="Nom de la playlist", placeholder="ex: banger only", required=True)
    morceaux = discord.ui.TextInput(label="Morceaux (un par ligne)", style=discord.TextStyle.paragraph, placeholder="titre 1\ntitre 2", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        pl = self.nom_playlist.value.strip()
        raw_text = self.morceaux.value.strip()
        if pl not in playlists_globales:
            playlists_globales[pl] = []
        liste = [m.strip() for m in raw_text.split('\n') if m.strip()]
        playlists_globales[pl].extend(liste)
        sauvegarder_playlists()
        await interaction.response.send_message(f"{len(liste)} morceau(x) ajouté(s) à **{pl}** !", ephemeral=True)

class PlaylistSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Créer une playlist", description="Crée une nouvelle playlist vide"),
            discord.SelectOption(label="Voir les playlists", description="Affiche toutes les playlists"),
            discord.SelectOption(label="Lancer une playlist", description="Joue une playlist entière"),
            discord.SelectOption(label="Ajouter des morceaux", description="Ajoute des musiques"),
            discord.SelectOption(label="Supprimer un morceau", description="Retire une musique"),
            discord.SelectOption(label="Supprimer une playlist", description="Efface une playlist entière")
        ]
        super().__init__(placeholder="Choisis une action...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        choix = self.values[0]
        if "Créer" in choix:
            await interaction.response.send_modal(CreatePlaylistModal())
        elif "Voir" in choix:
            if not playlists_globales:
                return await interaction.response.send_message("Aucune playlist.", ephemeral=True)
            embed = discord.Embed(title="Playlists", color=discord.Color.green())
            for nom, morceaux in playlists_globales.items():
                txt = "\n".join([f"• {m}" for m in morceaux]) if morceaux else "(Vide)"
                embed.add_field(name=nom, value=txt, inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)
        elif "Lancer" in choix:
            if not playlists_globales:
                return await interaction.response.send_message("Aucune playlist.", ephemeral=True)
            view_pl = discord.ui.View()
            select_pl = discord.ui.Select(placeholder="Sélectionne...")
            for nom in playlists_globales.keys():
                select_pl.add_option(label=nom, value=nom)
            async def cb(i: discord.Interaction):
                nom_pl = select_pl.values[0]
                await i.response.defer()
                morceaux = playlists_globales[nom_pl]
                if not morceaux:
                    return await i.followup.send("Playlist vide !", ephemeral=True)
                if not i.user.voice or not i.user.voice.channel:
                    return await i.followup.send("Rejoins un salon vocal !")
                vc = i.guild.voice_client
                if not vc:
                    vc = await i.user.voice.channel.connect()
                for m in morceaux:
                    queue_musiques.append({'recherche': m, 'playlist': nom_pl})
                await i.followup.send(f"Playlist **{nom_pl}** ajoutée !")
                if not vc.is_playing() and not vc.is_paused():
                    await jouer_prochaine_musique()
            select_pl.callback = cb
            view_pl.add_item(select_pl)
            await interaction.response.send_message("Choisis la playlist :", view=view_pl, ephemeral=True)
        elif "Ajouter" in choix:
            await interaction.response.send_modal(AddMultipleSongsModal())
        elif "Supprimer un morceau" in choix:
            if not playlists_globales:
                return await interaction.response.send_message("Aucune playlist.", ephemeral=True)
            view_d = discord.ui.View()
            sel_d = discord.ui.Select(placeholder="Choisis la playlist...")
            for nom in playlists_globales.keys():
                sel_d.add_option(label=nom, value=nom)
            async def cb_d(i: discord.Interaction):
                n = sel_d.values[0]
                m_list = playlists_globales.get(n, [])
                if not m_list:
                    return await i.response.send_message("Playlist vide.", ephemeral=True)
                v_s = discord.ui.View()
                s_s = discord.ui.Select(placeholder="Morceau à enlever...")
                for m in m_list[:25]:
                    s_s.add_option(label=m[:100], value=m)
                async def cb_s(si: discord.Interaction):
                    to_rem = s_s.values[0]
                    playlists_globales[n].remove(to_rem)
                    sauvegarder_playlists()
                    await si.response.send_message(f"Supprimé **{to_rem}** de **{n}** !", ephemeral=True)
                s_s.callback = cb_s
                v_s.add_item(s_s)
                await i.response.send_message("Sélectionne le son :", view=v_s, ephemeral=True)
            sel_d.callback = cb_d
            view_d.add_item(sel_d)
            await interaction.response.send_message("Choisis la playlist :", view=view_d, ephemeral=True)
        elif "Supprimer une playlist" in choix:
            if not playlists_globales:
                return await interaction.response.send_message("Aucune playlist.", ephemeral=True)
            v_del = discord.ui.View()
            s_del = discord.ui.Select(placeholder="Playlist à effacer...")
            for nom in playlists_globales.keys():
                s_del.add_option(label=nom, value=nom)
            async def cb_del(i: discord.Interaction):
                target = s_del.values[0]
                del playlists_globales[target]
                sauvegarder_playlists()
                await i.response.send_message(f"Playlist **{target}** supprimée !", ephemeral=True)
            s_del.callback = cb_del
            v_del.add_item(s_del)
            await interaction.response.send_message("Sélectionne :", view=v_del, ephemeral=True)

class PlaylistView(discord.ui.View):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_item(PlaylistSelect())

@playlist_group.command(name="menu", description="Ouvre le menu interactif complet des playlists")
async def playlist_menu(interaction: discord.Interaction):
    view = PlaylistView(timeout=None)
    await interaction.response.send_message("Menu des Playlists Partagées :", view=view, ephemeral=True)

async def playlist_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=nom, value=nom)
        for nom in playlists_globales.keys() if current.lower() in nom.lower()
    ][:25]

@playlist_group.command(name="shuffle", description="Mélange et lance une playlist entière")
@app_commands.describe(nom="Nom de la playlist à lancer en mode aléatoire")
@app_commands.autocomplete(nom=playlist_autocomplete)
async def playlist_shuffle(interaction: discord.Interaction, nom: str):
    await interaction.response.defer()
    if nom not in playlists_globales:
        return await interaction.followup.send("Cette playlist n'existe pas.", ephemeral=True)

    morceaux = playlists_globales[nom].copy()
    if not morceaux:
        return await interaction.followup.send(f"La playlist **{nom}** est vide !", ephemeral=True)

    if not interaction.user.voice or not interaction.user.voice.channel:
        return await interaction.followup.send("Tu dois être dans un salon vocal !", ephemeral=True)

    voice_channel = interaction.user.voice.channel
    voice_client = interaction.guild.voice_client

    if not voice_client:
        voice_client = await voice_channel.connect()
    elif voice_client.channel != voice_channel:
        await voice_client.move_to(voice_channel)

    random.shuffle(morceaux)
    for m in morceaux:
        queue_musiques.append({'recherche': m, 'playlist': f"{nom} (Mélangée)"})

    await interaction.followup.send(f"🔀 Playlist **{nom}** mélangée et ajoutée ({len(morceaux)} sons) !")

    if not voice_client.is_playing() and not voice_client.is_paused():
        await jouer_prochaine_musique()

bot.tree.add_command(playlist_group)

# Routes API Flask
@app.route('/')
def index():
    html_path = os.path.join(basedir, 'index.html')
    if os.path.exists(html_path):
        with open(html_path, 'r', encoding='utf-8') as f:
            return f.read()
    return f"Erreur : Introuvable -> {html_path}"

@app.route('/api/search')
def api_search():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])
    try:
        search_options = {'format': 'bestaudio/best', 'noplaylist': True, 'quiet': True, 'default_search': 'ytsearch8'}
        with yt_dlp.YoutubeDL(search_options) as ydl_search:
            data = ydl_search.extract_info(f"ytsearch8:{query}", download=False)
            if not data or 'entries' not in data: return jsonify([])
            results = []
            for entry in data.get('entries', []):
                if entry:
                    results.append({
                        'title': entry.get('title', 'Inconnu'),
                        'uploader': entry.get('uploader', 'Artiste Inconnu'),
                        'duration': format_duration(entry.get('duration', 0)),
                        'thumbnail': entry.get('thumbnail', ''),
                        'url': entry.get('webpage_url', '')
                    })
            return jsonify(results)
    except Exception as e:
        return jsonify([])

@app.route('/api/playlists')
def api_playlists():
    return jsonify(playlists_globales)

@app.route('/api/playlist-details')
def api_playlist_details():
    nom = request.args.get('nom', '')
    return jsonify(playlists_globales.get(nom, []))

@app.route('/api/playlists/create', methods=['POST'])
def api_create_playlist():
    req = request.json or {}
    nom = req.get('nom', '').strip()
    if not nom:
        return jsonify({'status': 'error', 'message': 'Nom vide'}), 400
    if nom in playlists_globales:
        return jsonify({'status': 'error', 'message': 'Cette playlist existe déjà'}), 400
    
    playlists_globales[nom] = []
    sauvegarder_playlists()
    return jsonify({'status': 'success', 'message': f'Playlist {nom} créée'})

@app.route('/api/playlists/add-song', methods=['POST'])
def api_add_song_to_playlist():
    req = request.json or {}
    nom = req.get('nom', '')
    morceau = req.get('morceau', '').strip()
    if nom not in playlists_globales or not morceau:
        return jsonify({'status': 'error', 'message': 'Données invalides'}), 400
    
    playlists_globales[nom].append(morceau)
    sauvegarder_playlists()
    return jsonify({'status': 'success', 'message': 'Morceau ajouté !'})

@app.route('/api/playlists/remove-song', methods=['POST'])
def api_remove_song_from_playlist():
    req = request.json or {}
    nom = req.get('nom', '')
    index = req.get('index', None)
    if nom in playlists_globales and index is not None and 0 <= index < len(playlists_globales[nom]):
        retire = playlists_globales[nom].pop(index)
        sauvegarder_playlists()
        return jsonify({'status': 'success', 'message': f'Retiré : {retire}'})
    return jsonify({'status': 'error', 'message': 'Impossible de retirer le morceau'}), 400

@app.route('/api/playlists/delete', methods=['POST'])
def api_delete_playlist():
    req = request.json or {}
    nom = req.get('nom', '')
    if nom in playlists_globales:
        del playlists_globales[nom]
        sauvegarder_playlists()
        return jsonify({'status': 'success', 'message': 'Playlist supprimée'})
    return jsonify({'status': 'error', 'message': 'Introuvable'}), 404

@app.route('/api/queue')
def api_queue():
    return jsonify([{'title': item['recherche'] if isinstance(item, dict) else item} for item in queue_musiques])

@app.route('/api/current')
def api_current():
    return jsonify(musique_en_cours)

@app.route('/api/play', methods=['POST'])
def api_play():
    req = request.json or {}
    url, title = req.get('url'), req.get('title', 'Musique')
    if not url: return jsonify({'status': 'error'}), 400
    queue_musiques.append({'recherche': url, 'playlist': 'Web'})
    async def run_play():
        vc = next((g.voice_client for g in bot.guilds if g.voice_client and g.voice_client.is_connected()), None)
        if not vc:
            for g in bot.guilds:
                for vc_chan in g.voice_channels:
                    if vc_chan.members:
                        vc = await vc_chan.connect()
                        break
                if vc: break
        if vc and not vc.is_playing() and not vc.is_paused():
            await jouer_prochaine_musique()
    asyncio.run_coroutine_threadsafe(run_play(), bot.loop)
    return jsonify({'status': 'success'})

@app.route('/api/bot-command', methods=['POST'])
def api_bot_command():
    req = request.json or {}
    cmd = req.get('command')
    
    async def trigger_command():
        channel = bot.get_channel(SALON_MUSIQUE_ID)
        guild = bot.get_guild(channel.guild.id) if channel else bot.guilds[0]
        vc = guild.voice_client

        if cmd == 'join':
            for vc_chan in guild.voice_channels:
                if vc_chan.members:
                    if not vc: await vc_chan.connect()
                    elif vc.channel != vc_chan: await vc.move_to(vc_chan)
                    break
        elif cmd == 'leave' or cmd == 'stop':
            global musique_en_cours
            queue_musiques.clear()
            musique_en_cours = None
            if vc and vc.is_connected(): await vc.disconnect()
        elif cmd == 'skip':
            if vc and (vc.is_playing() or vc.is_paused()): vc.stop()
        elif cmd == 'clear_all':
            if channel:
                pos = channel.position
                new_c = await channel.clone(reason="Web Clear All")
                await new_c.edit(position=pos)
                await channel.delete(reason="Web Clear All")
                await new_c.send("Salon réinitialisé depuis le Web Dashboard !")
        elif cmd == 'replay':
            global derniere_musique
            if derniere_musique:
                queue_musiques.insert(0, {'recherche': derniere_musique, 'playlist': 'Replay Web'})
                if vc and vc.is_playing(): vc.stop()
                elif vc: await jouer_prochaine_musique()

    asyncio.run_coroutine_threadsafe(trigger_command(), bot.loop)
    return jsonify({'status': 'success', 'message': f'Commande {cmd} exécutée !'})

def run_flask():
    app.run(host='127.0.0.1', port=5001, debug=False, use_reloader=False)

if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    bot.run(os.getenv('DISCORD_TOKEN'))
