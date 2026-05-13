#!/usr/bin/env python3
import os
import re
import subprocess
import time
from pathlib import Path
from dotenv import load_dotenv
import aiohttp
import aiofiles
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ========== কনফিগ লোড ==========
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN not set in .env file")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    if ADMIN_ID == 0:
        raise ValueError
except:
    raise ValueError("ADMIN_ID must be a valid integer in .env")

allowed_str = os.getenv("ALLOWED_USERS", str(ADMIN_ID))
ALLOWED_USERS = set()
for part in allowed_str.split(","):
    part = part.strip()
    if part.isdigit():
        ALLOWED_USERS.add(int(part))
if not ALLOWED_USERS:
    ALLOWED_USERS = {ADMIN_ID}

AUTO_DELETE_HOURS = int(os.getenv("AUTO_DELETE_HOURS", "24"))

# ========== পাথ ==========
CRED_DIR = Path("cred_files")
CRED_DIR.mkdir(exist_ok=True)

def timestamp_path(filepath: Path) -> Path:
    return CRED_DIR / f"{filepath.name}.timestamp"

# ========== ডাউনলোড (প্রোগ্রেস সহ) ==========
async def download_file(url: str, progress_callback=None):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    raw_name = Path(url.split("/")[-1]).name
                    safe_name = "".join(c for c in raw_name if c.isalnum() or c in "._-")
                    if not safe_name:
                        safe_name = "downloaded_file.txt"
                    filepath = CRED_DIR / safe_name
                    ts_file = timestamp_path(filepath)

                    if filepath.exists():
                        filepath.unlink()
                    if ts_file.exists():
                        ts_file.unlink()

                    total_size = int(resp.headers.get('content-length', 0))
                    downloaded = 0

                    async with aiofiles.open(filepath, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024*1024):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback and total_size > 0:
                                percent = (downloaded / total_size) * 100
                                await progress_callback(percent)

                    async with aiofiles.open(ts_file, "w") as tf:
                        await tf.write(str(time.time()))

                    return filepath, total_size
                return None, 0
    except Exception as e:
        print(f"Download error: {e}")
        return None, 0

# ========== অটো ডিলিট ==========
async def delete_old_files():
    now = time.time()
    deleted = 0
    for filepath in CRED_DIR.glob("*"):
        if filepath.suffix == ".timestamp":
            continue
        ts_file = timestamp_path(filepath)
        if not ts_file.exists():
            try:
                filepath.unlink()
                deleted += 1
            except:
                pass
            continue
        try:
            async with aiofiles.open(ts_file, "r") as tf:
                content = await tf.read()
                created = float(content.strip())
            if now - created > AUTO_DELETE_HOURS * 3600:
                filepath.unlink()
                ts_file.unlink()
                deleted += 1
        except:
            try:
                filepath.unlink()
                ts_file.unlink()
                deleted += 1
            except:
                pass
    if deleted:
        print(f"Auto-deleted {deleted} files")

# ========== ক্রেডেনশিয়াল এক্সট্র্যাক্ট (লাইনের শেষের user:pass) ==========
def extract_user_pass(line: str):
    """
    লাইন থেকে শেষের ':' এর ভিত্তিতে user:pass বের করে।
    যেমন: "anything:user:pass" -> ("user", "pass")
          "user:pass" -> ("user", "pass")
    রিটার্ন করে (user, pass) অথবা (None, None)
    """
    parts = line.strip().split(":")
    if len(parts) < 2:
        return None, None
    # শেষ অংশটি পাসওয়ার্ড, তার আগের অংশটি ইউজার
    password = parts[-1]
    user = parts[-2]
    return user, password

# ========== সার্চ (সব রেজাল্ট) + ফরম্যাটিং ==========
def search_and_format(domain: str):
    """
    ripgrep দিয়ে সব ম্যাচিং লাইন বের করে।
    প্রতিটি লাইন থেকে user:pass বের করে দুটি ফরম্যাট তৈরি করে:
    - only_list: ['user:pass', ...]
    - url_list: [f'{domain}:user:pass', ...]
    """
    pattern = rf"@?{re.escape(domain)}"
    cmd = ["rg", "-i", pattern, str(CRED_DIR), "--no-line-number", "--no-filename"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            only_list = []
            url_list = []
            for line in lines:
                if ":" not in line:
                    continue
                user, pwd = extract_user_pass(line)
                if user and pwd:
                    only_list.append(f"{user}:{pwd}")
                    url_list.append(f"{domain}:{user}:{pwd}")
                else:
                    # যদি extract না হয়, পুরো লাইনটাই ধরে নিচ্ছি (বেকআপ)
                    only_list.append(line)
                    url_list.append(f"{domain}:{line}")
            return only_list, url_list
        return [], []
    except subprocess.TimeoutExpired:
        print("Search timeout after 120 seconds")
        return [], []
    except Exception as e:
        print(f"Search error: {e}")
        return [], []

# ========== টেলিগ্রাম কমান্ড ==========
async def addfile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ শুধু অ্যাডমিন ফাইল যোগ করতে পারেন।")
        return
    if not ctx.args:
        await update.message.reply_text("❗ ব্যবহার: /addfile http://example.com/file.txt")
        return
    url = ctx.args[0].strip()
    msg = await update.message.reply_text(f"📥 ডাউনলোড শুরু: {url}\nপ্রগ্রেস: 0%")

    async def update_progress(percent):
        await msg.edit_text(f"📥 ডাউনলোড শুরু: {url}\nপ্রগ্রেস: {percent:.1f}%")

    path, total = await download_file(url, update_progress)
    if path:
        size_mb = total / (1024*1024)
        await msg.edit_text(
            f"✅ ডাউনলোড সম্পন্ন: `{path.name}`\n📌 সাইজ: {size_mb:.2f} MB\n📌 {AUTO_DELETE_HOURS} ঘণ্টা পর ডিলিট হবে।",
            parse_mode="Markdown"
        )
    else:
        await msg.edit_text("❌ ব্যর্থ। URL চেক করুন।")

async def listfiles(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ অননুমোদিত।")
        return
    files = [f for f in CRED_DIR.glob("*") if not f.name.endswith(".timestamp")]
    if not files:
        await update.message.reply_text("📁 কোনো ফাইল নেই।")
        return
    now = time.time()
    msg = "📁 *সংরক্ষিত ফাইল (অটো-ডিলিট {} ঘণ্টা):*\n".format(AUTO_DELETE_HOURS)
    for f in files:
        size_mb = f.stat().st_size / (1024*1024)
        ts_file = timestamp_path(f)
        remaining = "অজানা"
        if ts_file.exists():
            try:
                with open(ts_file, "r") as tf:
                    created = float(tf.read().strip())
                expires_in = (created + AUTO_DELETE_HOURS * 3600) - now
                if expires_in > 0:
                    h = int(expires_in // 3600)
                    m = int((expires_in % 3600) // 60)
                    remaining = f"{h}ঘ {m}মি"
                else:
                    remaining = "ডিলিট হবে"
            except:
                remaining = "ত্রুটি"
        msg += f"• `{f.name}` – {size_mb:.1f} MB (অবশিষ্ট: {remaining})\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def delfile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ শুধু অ্যাডমিন ডিলিট করতে পারেন।")
        return
    if not ctx.args:
        await update.message.reply_text("❗ ব্যবহার: /delfile filename.txt")
        return
    filename = ctx.args[0].strip()
    if ".." in filename or "/" in filename or "\\" in filename:
        await update.message.reply_text("❌ অবৈধ ফাইলনেম।")
        return
    filepath = CRED_DIR / filename
    if not filepath.exists():
        await update.message.reply_text(f"❌ `{filename}` ফাইলটি নেই।", parse_mode="Markdown")
        return
    try:
        filepath.unlink()
        ts_file = timestamp_path(filepath)
        if ts_file.exists():
            ts_file.unlink()
        await update.message.reply_text(f"✅ `{filename}` ডিলিট হয়েছে।", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ ডিলিট ব্যর্থ: {e}")

async def url_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ আপনি এই বট ব্যবহারের অনুমতি পাননি।")
        return
    if not ctx.args:
        await update.message.reply_text("❗ ব্যবহার: /url example.com\n(সব ফলাফল ফাইল হিসেবে আসবে)")
        return

    domain = ctx.args[0].lower()
    status_msg = await update.message.reply_text(f"🔍 `{domain}` এর জন্য সব রেজাল্ট খুঁজছি... (বড় ফাইলে সময় লাগতে পারে)")

    only_list, url_list = search_and_format(domain)
    if not only_list:
        await status_msg.edit_text("❌ কোনো ক্রেডেনশিয়াল পাওয়া যায়নি।")
        return

    total = len(only_list)
    await status_msg.edit_text(f"✅ মোট {total}টি ক্রেডেনশিয়াল পাওয়া গেছে। ফাইল তৈরি করা হচ্ছে...")

    timestamp = int(time.time())
    txt_file_only = CRED_DIR / f"search_{domain}_{timestamp}_only.txt"
    txt_file_url = CRED_DIR / f"search_{domain}_{timestamp}_url.txt"

    async with aiofiles.open(txt_file_only, "w") as f:
        await f.write("\n".join(only_list))
    async with aiofiles.open(txt_file_url, "w") as f:
        await f.write("\n".join(url_list))

    await status_msg.delete()
    await update.message.reply_document(
        document=open(txt_file_only, "rb"),
        filename=f"{domain}_only.txt",
        caption=f"📄 {domain} – শুধু user:pass (মোট {total}টি)"
    )
    await update.message.reply_document(
        document=open(txt_file_url, "rb"),
        filename=f"{domain}_url_user_pass.txt",
        caption=f"📄 {domain} – {domain}:user:pass (মোট {total}টি)"
    )

    txt_file_only.unlink()
    txt_file_url.unlink()

# ========== মেইন ==========
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("addfile", addfile))
    app.add_handler(CommandHandler("listfiles", listfiles))
    app.add_handler(CommandHandler("delfile", delfile))
    app.add_handler(CommandHandler("url", url_cmd))

    async def post_init(app: Application):
        scheduler = AsyncIOScheduler()
        scheduler.add_job(delete_old_files, "interval", hours=1)
        scheduler.start()
        print(f"🤖 বট চালু। অটো-ডিলিট প্রতি ঘণ্টায় চেক করবে। ফাইল {AUTO_DELETE_HOURS} ঘণ্টা পরে মুছে যাবে।")

    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__":
    main()
