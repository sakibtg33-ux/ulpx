# Telegram Cred Bot

একটি টেলিগ্রাম বট যা লিংক থেকে বড় ফাইল ডাউনলোড করে, নির্দিষ্ট ডোমেইনের ক্রেডেনশিয়াল সার্চ করে, এবং ২৪ ঘণ্টা পর স্বয়ংক্রিয়ভাবে ফাইল মুছে ফেলে।

## বৈশিষ্ট্য

- `/addfile <url>` – লিংক থেকে ফাইল ডাউনলোড করে (শুধু অ্যাডমিন)
- `/listfiles` – সংরক্ষিত ফাইলের তালিকা ও মেয়াদকাল দেখায়
- `/delfile <filename>` – ম্যানুয়ালি ফাইল ডিলিট করে
- `/url <domain>` – সব ফাইলের ভিতর সার্চ করে ইমেইল:পাসওয়ার্ড দেখায়
- অটো-ডিলিট – ডিফল্ট ২৪ ঘণ্টা পর ফাইল মুছে যায়

## ডেপ্লয় (ভিপিএসে)

```bash
sudo apt update && sudo apt install python3-pip git ripgrep -y
git clone https://github.com/yourusername/tg_cred_bot.git
cd tg_cred_bot
cp .env.example .env
nano .env   # টোকেন ও অ্যাডমিন আইডি বসান
pip3 install -r requirements.txt
python3 bot.py
