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

# ========== ডাউনলোড ==========
async def download_file(url: str):
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

                    async with aiofiles.open(filepath, "wb") as f:
                        while True:
                            chunk = await resp.content.read(1024 * 1024)
                            if not chunk:
                                break
                            await f.write(chunk)

                    async with aiofiles.open(ts_file, "w") as tf:
                        await tf.write(str(time.time()))

                    return filepath
                return None
    except Exception as e:
        print(f"Download error: {e}")
        return None

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

# ========== সার্চ (ripgrep) ==========
def search_credentials(domain: str) -> list:
    pattern = rf"@?{re.escape(domain)}"
    cmd = ["rg", "-i", pattern, str(CRED_DIR), "--no-line-number", "--max-count", "200"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            valid = [line for line in lines if ":" in line and len(line.split(":")) >= 2]
            return valid[:50]
        return []
    except Exception as e:
        print(f"Search error: {e}")
        return []

# ========== টেলিগ্রাম কমান্ড ==========
async def addfile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ শুধু অ্যাডমিন ফাইল যোগ করতে পারেন।")
        return
    if not ctx.args:
        await update.message.reply_text("❗ ব্যবহার: /addfile http://example.com/file.txt")
        return
    url = ctx.args[0].strip()
    await update.message.reply_text(f"📥 ডাউনলোড শুরু: {url}\nবড় ফাইল হলে সময় লাগতে পারে...")
    path = await download_file(url)
    if path:
        await update.message.reply_text(
            f"✅ ডাউনলোড সম্পন্ন: `{path.name}`\n📌 {AUTO_DELETE_HOURS} ঘণ্টা পর ডিলিট হবে।",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ ব্যর্থ। URL চেক করুন।")

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
        await update.message.reply_text("❗ ব্যবহার: /url example.com")
        return
    domain = ctx.args[0].lower()
    await update.message.reply_text(f"🔍 `{domain}` খুঁজছি... দয়া করে অপেক্ষা করুন।")
    results = search_credentials(domain)
    if not results:
        await update.message.reply_text("❌ কোনো ক্রেডেনশিয়াল পাওয়া যায়নি।")
        return
    msg = f"📄 *{domain}* এর ফলাফল (সর্বোচ্চ ৫০টি):\n\n"
    for i, cred in enumerate(results, 1):
        short_cred = cred[:200]
        msg += f"{i}. `{short_cred}`\n"
        if len(msg) > 3800:
            msg += "\n... মেসেজ সীমা অতিক্রম করেছে।"
            break
    await update.message.reply_text(msg, parse_mode="Markdown")

# ========== মেইন ==========
def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("addfile", addfile))
    app.add_handler(CommandHandler("listfiles", listfiles))
    app.add_handler(CommandHandler("delfile", delfile))
    app.add_handler(CommandHandler("url", url_cmd))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(delete_old_files, "interval", hours=1)
    scheduler.start()

    print(f"🤖 বট চালু। অটো-ডিলিট প্রতি ঘণ্টায় চেক করবে। ফাইল {AUTO_DELETE_HOURS} ঘণ্টা পরে মুছে যাবে।")
    app.run_polling()

if __name__ == "__main__":
    main()
