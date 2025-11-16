import discord
from discord.ext import commands
import yt_dlp
import asyncio
import json
import os

# ❌ Mac 専用 → Railway では不要なので削除
# discord.opus.load_opus("/opt/homebrew/opt/opus/lib/libopus.dylib")

TOKEN = os.getenv("BOT_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

bot.remove_command("help")

# ====================================
# 設定保存（音量・Bass）
# ====================================
SETTINGS_FILE = "settings.json"

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r") as f:
            return json.load(f)
    return {"volume": 1.0, "bass": 0}

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f)

settings = load_settings()
current_volume = settings.get("volume", 1.0)
bass_level = settings.get("bass", 0)

# ====================================
# 音楽変数
# ====================================
music_queue = []
current_url = None
current_title = "なし"
current_thumbnail = None
loop_enabled = False
search_results = []  # 検索結果保存

# ====================================
# チャット通知 ON/OFF
# ====================================
chat_enabled = True

# ====================================
# Chat ON/OFF に対応した送信関数
# ====================================
async def safe_send(ctx, msg=None, embed=None, view=None):
    if not chat_enabled:
        return
    await ctx.send(content=msg, embed=embed, view=view)

# ====================================
# Now Playing（サムネ付き）
# ====================================
async def send_nowplaying(ctx):
    embed = discord.Embed(
        title="🎶 Now Playing",
        description=f"**[{current_title}]({current_url})**",
        color=0x1DB954
    )
    if current_thumbnail:
        embed.set_thumbnail(url=current_thumbnail)
    await safe_send(ctx, embed=embed)

# ====================================
# Spotify → YouTube検索変換
# ====================================
def smart_extract(url):
    if "spotify.com" in url:
        return f"ytsearch:{url}"
    return url

# ====================================
# プレイリスト処理
# ====================================
def extract_playlist(url):
    return "list=" in url or "&list=" in url

# ====================================
# 再生処理 + Bassフィルタ
# ====================================
def get_bass_filter(level):
    if level <= 0:
        return ""
    gain = level * 4
    return f",bass=g={gain}"

async def play_music(ctx, url):
    global current_url, current_title, current_thumbnail

    url = smart_extract(url)
    current_url = url

    vc = ctx.voice_client
    if not vc:
        vc = await ctx.author.voice.channel.connect()

    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": True
    }

    # プレイリスト → 全曲キューに追加
    if extract_playlist(url):
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            info = ydl.extract_info(url, download=False)
            for entry in info["entries"]:
                music_queue.append(f"https://www.youtube.com/watch?v={entry['id']}")
            await safe_send(ctx, f"📚 プレイリストを **{len(info['entries'])}曲** 追加しました！")
            if not vc.is_playing():
                next_url = music_queue.pop(0)
                await play_music(ctx, next_url)
            return

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        audio_url = info["url"]
        current_title = info.get("title", "不明なタイトル")
        current_thumbnail = info.get("thumbnail")
        current_url = f"https://www.youtube.com/watch?v={info.get('id')}"

    def after_play(err):
        asyncio.run_coroutine_threadsafe(handle_after_play(ctx), bot.loop)

    if vc.is_playing():
        vc.stop()

    bass_filter = get_bass_filter(bass_level)

    source = discord.PCMVolumeTransformer(
        discord.FFmpegPCMAudio(
            audio_url,
            before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            options=f"-vn -af 'equalizer=f=40:t=h:width=200:gain={bass_level * 3}{bass_filter}'"
        ),
        volume=current_volume
    )

    vc.play(source, after=after_play)
    await send_nowplaying(ctx)

# ====================================
# 再生後（ループ or 次）
# ====================================
async def handle_after_play(ctx):
    if loop_enabled:
        await play_music(ctx, current_url)
        return

    if music_queue:
        next_url = music_queue.pop(0)
        await play_music(ctx, next_url)
    else:
        await safe_send(ctx, "📭 キューは空です！")

# ====================================
# イベント
# ====================================
@bot.event
async def on_ready():
    print(f"ログインしました: {bot.user}")

# ====================================
# コマンド
# ====================================
@bot.command()
async def join(ctx):
    if ctx.author.voice:
        await ctx.author.voice.channel.connect()
        await safe_send(ctx, "VCに参加しました！")
    else:
        await safe_send(ctx, "先にVCに入ってください！")

@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        music_queue.clear()
        await ctx.voice_client.disconnect()
        await safe_send(ctx, "VCから切断しました！")

# === 再生 ===
@bot.command()
async def play(ctx, *, url_or_number):
    global search_results

    # 数字選択
    if url_or_number.isdigit() and search_results:
        index = int(url_or_number) - 1
        if 0 <= index < len(search_results):
            url = search_results[index]["url"]
            await safe_send(ctx, f"▶ **{search_results[index]['title']}** を再生します")
            search_results = []
            if ctx.voice_client and ctx.voice_client.is_playing():
                music_queue.append(url)
            else:
                await play_music(ctx, url)
            return

    # 普通のURL
    url = url_or_number

    if ctx.voice_client and ctx.voice_client.is_playing():
        music_queue.append(url)
        await safe_send(ctx, "➕ キューに追加しました！")
    else:
        await play_music(ctx, url)

# === 検索 ===
@bot.command()
async def search(ctx, *, keyword):
    global search_results
    search_results = []

    query = f"ytsearch10:{keyword}"
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info = ydl.extract_info(query, download=False)

    embed = discord.Embed(title=f"🔍 検索結果: {keyword}", color=0x00FFAA)

    for i, entry in enumerate(info["entries"]):
        title = entry.get("title")
        url = f"https://www.youtube.com/watch?v={entry['id']}"
        search_results.append({"title": title, "url": url})

        embed.add_field(
            name=f"{i+1}. {title}",
            value=url,
            inline=False
        )

    await safe_send(ctx, embed=embed)
    await safe_send(ctx, "➡ 再生する番号を `!play 番号` で選んでください。")

# === Now Playing ===
@bot.command()
async def now(ctx):
    global current_title, current_url

    if not current_url:
        await safe_send(ctx, "🎵 再生中の曲はありません！")
        return

    embed = discord.Embed(
        title="🎶 Now Playing",
        description=f"**{current_title}**\n{current_url}",
        color=0x1DB954
    )

    if "youtube" in current_url:
        vid = current_url.split("v=")[-1]
        embed.set_thumbnail(url=f"https://img.youtube.com/vi/{vid}/hqdefault.jpg")

    await safe_send(ctx, embed=embed)

# === Queue ===
@bot.command()
async def queue(ctx):
    if not music_queue:
        await safe_send(ctx, "📭 キューは空です！")
        return

    embed = discord.Embed(title="📜 再生キュー", color=0x5865F2)

    for i, item in enumerate(music_queue):
        embed.add_field(name=f"{i+1} 曲目", value=item, inline=False)

    await safe_send(ctx, embed=embed)

# === Skip ===
@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await safe_send(ctx, "⏭ スキップしました！")

# === Pause ===
@bot.command()
async def pause(ctx):
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await safe_send(ctx, "⏸ 一時停止しました！")

# === Resume ===
@bot.command()
async def resume(ctx):
    vc = ctx.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await safe_send(ctx, "▶ 再開しました！")

# === Loop ===
@bot.command()
async def loop(ctx):
    global loop_enabled
    loop_enabled = not loop_enabled
    await safe_send(ctx, f"🔁 ループ {'ON' if loop_enabled else 'OFF'}")

# === Volume ===
@bot.command()
async def volume(ctx, vol: int):
    global current_volume

    if not 0 <= vol <= 200:
        await safe_send(ctx, "音量は 0〜200% で指定してください")
        return

    current_volume = vol / 100
    settings["volume"] = current_volume
    save_settings(settings)

    if ctx.voice_client and ctx.voice_client.source:
        ctx.voice_client.source.volume = current_volume

    await safe_send(ctx, f"🔊 音量を {vol}% に設定しました！（保存）")

# === Bass ===
@bot.command()
async def bass(ctx, level: int):
    global bass_level

    if not 0 <= level <= 10:
        await safe_send(ctx, "🎚 Bass は 0〜10 で指定してください")
        return

    bass_level = level
    settings["bass"] = level
    save_settings(settings)

    await safe_send(ctx, f"🎧 Bass レベルを **{level}** に設定しました！（保存）")

# ====================================
# 🎛 Chat ON/OFF
# ====================================
@bot.command()
async def chat(ctx, mode: str):
    global chat_enabled

    if mode.lower() == "on":
        chat_enabled = True
        await ctx.send("💬 チャット通知 **ON**")
    elif mode.lower() == "off":
        chat_enabled = False
        await ctx.send("🔇 チャット通知 **OFF**（静かモード）")
    else:
        await ctx.send("使い方: `!chat on` / `!chat off`")

# ====================================
# HelpView
# ====================================
from discord.ui import View, button

class HelpView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @staticmethod
    def main_page():
        embed = discord.Embed(
            title="📘 メインヘルプ",
            description="ボットの基本コマンド一覧です！",
            color=0x3498db
        )
        embed.add_field(name="!help", value="ヘルプを表示", inline=False)
        embed.add_field(name="!chat on/off", value="ボットの発言を制御", inline=False)
        embed.add_field(name="!join / !leave", value="VC参加 / 退出", inline=False)
        return embed

    @staticmethod
    def music_page():
        embed = discord.Embed(
            title="🎵 音楽コマンド",
            description="ミュージック関連コマンドです！",
            color=0x1abc9c
        )
        embed.add_field(name="!play [URL/番号]", value="音楽を再生", inline=False)
        embed.add_field(name="!search", value="YouTube検索", inline=False)
        embed.add_field(name="!skip / !pause / !resume", value="操作", inline=False)
        embed.add_field(name="!queue / !now", value="情報表示", inline=False)
        embed.add_field(name="!loop", value="ループON/OFF", inline=False)
        embed.add_field(name="!volume / !bass", value="音質調整", inline=False)
        return embed

    @staticmethod
    def admin_page():
        embed = discord.Embed(
            title="🛠 管理者コマンド",
            description="管理者専用",
            color=0xe74c3c
        )
        embed.add_field(name="!shutdown", value="BOT停止", inline=False)
        embed.add_field(name="!reload", value="設定リロード", inline=False)
        embed.add_field(name="!clear", value="キュー全削除", inline=False)
        return embed

    @button(label="メイン", style=discord.ButtonStyle.primary)
    async def main_button(self, interaction, btn):
        await interaction.response.edit_message(embed=self.main_page(), view=self)

    @button(label="音楽", style=discord.ButtonStyle.success)
    async def music_button(self, interaction, btn):
        await interaction.response.edit_message(embed=self.music_page(), view=self)

    @button(label="管理", style=discord.ButtonStyle.danger)
    async def admin_button(self, interaction, btn):
        await interaction.response.edit_message(embed=self.admin_page(), view=self)

@bot.command()
async def help(ctx):
    view = HelpView()
    await safe_send(ctx, embed=HelpView.main_page(), view=view)

@bot.event
async def on_ready():
    global chat_enabled
    chat_enabled = True  # ← 起動時に絶対ONに戻す
    print(f"ログインしました: {bot.user}")

# ====================================
# 🎧 自動切断（VCに誰もいなくなったら10秒後切る）
# ====================================
@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    voice = member.guild.voice_client
    if not voice or not voice.channel:
        return

    if len(voice.channel.members) == 1:
        await asyncio.sleep(10)
        if len(voice.channel.members) == 1:
            await voice.disconnect()
            print("🔌 自動切断しました（VCに誰もいないため）")

bot.run(TOKEN)
