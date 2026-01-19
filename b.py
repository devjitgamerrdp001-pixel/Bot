#!/usr/bin/env python3
import os
import asyncio
import re
import time
import glob
import sys
import shutil
from pathlib import Path
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import QueryIdInvalid
import yt_dlp

# ---------------- Config ----------------
load_dotenv()

API_ID = int(os.environ.get("API_ID") or 0)
API_HASH = os.environ.get("API_HASH", "").strip()
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

if not (API_ID and API_HASH and BOT_TOKEN):
    print("❌ Error: Credentials missing in .env")
    sys.exit()

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
SESSION_DIR = BASE_DIR / "session_data"

# ---------------- Client Setup (Workers 25 | Transmissions 25) ----------------
app = Client(
    "termux_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir=str(SESSION_DIR),
    workers=25, 
    max_concurrent_transmissions=25, 
    sleep_threshold=180, 
    ipv6=False 
)

QUALITY_OPTIONS = ["360", "480", "720"]
QUALITY_EMOJIS = {"360": "🎥", "480": "📺", "720": "💎"}
user_url = {}

# ---------------- Designing Helpers (Ditto Old Style) ----------------
def create_progress_bar(percent, width=12):
    percent = max(0.0, min(100.0, percent))
    filled = round(width * (percent / 100))
    empty = width - filled
    return f"{'▰'*filled}{'▱'*empty} {percent:.0f}%"

def clean_filename(s: str) -> str:
    return re.sub(r'[\\/:"*?<>|]+', "_", s)

def remove_ansi_codes(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

async def run_subprocess_with_progress(cmd, progress_callback):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    try:
        while True:
            line = await proc.stdout.readline()
            if not line: break
            raw_line = line.decode('utf-8', errors='ignore').strip()
            if progress_callback: await progress_callback(remove_ansi_codes(raw_line))
        await proc.wait()
    except: pass
    return proc.returncode

async def extract_thumbnail(video_path, thumb_path):
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-ss", "00:00:05", "-vframes", "1", str(thumb_path)]
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return thumb_path if thumb_path.exists() else None

# ---------------- Core Logic (Sequential per user) ----------------
async def start_download(chat_id, url, quality, user_name, user_id):
    unique_id = f"{user_id}_{int(time.time() * 1000)}"
    msg = await app.send_message(chat_id, "🔍 **Analyzing Link...**\n───────────────────\n*Processing your specific request...*")
    final_path = None
    
    try:
        ydl_opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 30}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            vid_width, vid_height = info.get("width") or 1280, info.get("height") or 720
            title, duration = info.get("title", "Video"), info.get("duration", 0)

        clean_title = f"{unique_id}_{clean_filename(title)}"
        base_path = DOWNLOADS_DIR / clean_title
        thumb_path = DOWNLOADS_DIR / f"{clean_title}.jpg"
        
        last_update = 0
        async def dl_progress(line):
            nonlocal last_update
            match = re.search(r"(\d+(?:\.\d+)?)%", line)
            if match and time.time() - last_update > 12: 
                last_update = time.time()
                pct = float(match.group(1))
                size_match = re.search(r"([\d\.]+[a-zA-Z/b]+)(?:\s+of\s+|/)([\d\.]+[a-zA-Z/b]+)", line)
                curr, total = size_match.groups() if size_match else ("...", "...")
                try: 
                    await msg.edit_text(
                        f"📥 **DOWNLOADING VIDEO**\n"
                        f"───────────────────\n"
                        f"🎬 **File:** `{title[:30]}...`\n"
                        f"📊 **Progress:** `{create_progress_bar(pct)}`\n"
                        f"📂 **Size:** `{curr} / {total}`\n"
                        f"───────────────────"
                    )
                except: pass

        fmt = f"bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"
        aria_exists = shutil.which("aria2c")
        ret_code = -1
        
        if aria_exists:
            cmd_turbo = ["yt-dlp", "--external-downloader", "aria2c", "--external-downloader-args", "-x 16 -s 16 -k 1M", "--force-ipv4", "-f", fmt, "--merge-output-format", "mp4", "-o", str(base_path) + ".%(ext)s", url]
            ret_code = await run_subprocess_with_progress(cmd_turbo, dl_progress)
        
        if ret_code != 0:
            cmd_safe = ["yt-dlp", "--extractor-args", "youtube:player_client=android", "--hls-prefer-native", "--force-ipv4", "-f", fmt, "--merge-output-format", "mp4", "-o", str(base_path) + ".%(ext)s", url]
            await run_subprocess_with_progress(cmd_safe, dl_progress)

        possible_files = [f for f in glob.glob(str(base_path) + ".*") if Path(f).suffix in {".mp4", ".mkv", ".webm"}]
        if not possible_files: raise Exception("Download failed.")
        
        final_path = Path(possible_files[0])
        temp_path = DOWNLOADS_DIR / f"temp_{final_path.name}"
        await (await asyncio.create_subprocess_exec("ffmpeg", "-y", "-i", str(final_path), "-map", "0", "-c", "copy", "-metadata:s:v:0", "rotate=0", str(temp_path))).wait()
        if temp_path.exists(): os.replace(temp_path, final_path)

        actual_thumb = await extract_thumbnail(final_path, thumb_path)
        upload_last_update = 0
        start_time = time.time()
        
        caption = (
            f"╭────✦ **VIDEO FILE** ✦────╮\n\n"
            f"🎬 **Title:** `{title}`\n"
            f"📚 **Batch:** `Vishwas`\n"
            f"💿 **Quality:** `{quality}p`\n\n"
            f"╰───────────────╯\n\n"
            f"📥 **Extracted By:** `{user_name}`\n"
            f"━━━✦ **DEVU ❣️** ✦━━━"
        )

        async def progress(current, total):
            nonlocal upload_last_update
            if total and time.time() - upload_last_update > 12:
                upload_last_update = time.time()
                pct = current/total*100
                speed = (current / (time.time() - start_time)) / (1024*1024)
                try: 
                    await msg.edit_text(
                        f"⬆️ **UPLOADING TO TELEGRAM**\n"
                        f"───────────────────\n"
                        f"📊 **Progress:** `{create_progress_bar(pct)}`\n"
                        f"🚀 **Speed:** `{speed:.2f} MB/s`\n"
                        f"📂 **Size:** `{current//1024//1024}MB / {total//1024//1024}MB`\n"
                        f"───────────────────"
                    )
                except: pass

        await app.send_video(chat_id, video=str(final_path), caption=caption, duration=duration, width=int(vid_width), height=int(vid_height), thumb=str(actual_thumb), supports_streaming=True, progress=progress)
        await msg.delete()

    except Exception as e:
        try: await msg.edit_text(f"❌ **Error:** `{str(e)[:100]}`")
        except: pass
    finally:
        for junk in glob.glob(str(DOWNLOADS_DIR / f"{unique_id}*")):
            try: os.remove(junk)
            except: pass

# ---------------- Handlers (Ditto Same) ----------------
@app.on_message(filters.command("start"))
async def start(_, message):
    welcome_text = (
        f"╔════════════════════════╗\n"
        f"   ✨ **ADVANCED DOWNLOADER** ✨\n"
        f"╚════════════════════════╝\n\n"
        f"👋 **Hello, {message.from_user.first_name}!**\n\n"
        f"I can download YouTube videos in bulk\n"
        f"with high-speed processing.\n\n"
        f"💠 **Status:** `System Active 🟢` \n"
        f"💠 **Uptime:** `Optimized ✅` \n\n"
        f"╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼╼\n"
        f"📥 **Send me links to start...**"
    )
    await message.reply_text(welcome_text)

@app.on_message(filters.text)
async def handle_urls(_, message):
    urls = [u.strip() for u in message.text.split('\n') if "http" in u]
    if not urls: return
    user_url[message.from_user.id] = urls
    buttons = [InlineKeyboardButton(f"{QUALITY_EMOJIS[q]} {q}p", callback_data=q) for q in QUALITY_OPTIONS]
    await message.reply_text(f"📦 **LINKS DETECTED**\n💎 Select Quality:", reply_markup=InlineKeyboardMarkup([buttons]))

@app.on_callback_query()
async def cb_handler(_, query):
    try:
        await query.answer()
    except QueryIdInvalid:
        pass
        
    urls = user_url.get(query.from_user.id)
    if not urls: return await query.edit_message_text("❌ Session expired.")
    quality, user_name, user_id, chat_id = query.data, query.from_user.first_name, query.from_user.id, query.message.chat.id
    del user_url[query.from_user.id]
    
    async def process_user_queue():
        for url in urls:
            await start_download(chat_id, url, quality, user_name, user_id)
            
    asyncio.create_task(process_user_queue())

# ---------------- Main Block (System Cleanup) ----------------
if __name__ == "__main__":
    if SESSION_DIR.exists():
        print("🧹 Cleaning up old session data...")
        shutil.rmtree(str(SESSION_DIR))
    SESSION_DIR.mkdir(exist_ok=True)
    DOWNLOADS_DIR.mkdir(exist_ok=True)

    print("🧹 Cleaning up downloads folder...")
    for item in glob.glob(str(DOWNLOADS_DIR / "*")):
        try:
            if os.path.isfile(item): os.remove(item)
            else: shutil.rmtree(item)
        except: pass
    
    print("💎 DEVU FINAL STABLE BOT STARTING...")
    app.run()