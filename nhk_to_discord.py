import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote

import pykakasi

# env = nama secret webhook channel khusus kategori itu.
# Kalau secret-nya ga ada/kosong, otomatis pakai DISCORD_WEBHOOK_URL.
FEEDS = [
    {"name": "主要", "cat": "cat0", "env": "DISCORD_WEBHOOK_URL", "color": 0xE60012},
    {"name": "科学・医療", "cat": "cat3", "env": "DISCORD_WEBHOOK_SCIENCE", "color": 0x1E88E5},
    {"name": "国際", "cat": "cat6", "env": "DISCORD_WEBHOOK_WORLD", "color": 0x43A047},
    {"name": "文化・エンタメ", "cat": "cat2", "env": "DISCORD_WEBHOOK_CULTURE", "color": 0x8E24AA},
]
FEED_URL_TEMPLATES = [
    "https://www.nhk.or.jp/rss/news/{cat}.xml",
    "https://news.web.nhk/n-data/conf/na/rss/{cat}.xml",  # cadangan, belum diverifikasi
]
STATE_FILE = Path("seen.json")
MAX_POST_PER_RUN = 3     # per kategori
FIRST_RUN_POST = 3       # kategori yang baru pertama kali jalan
MAX_STATE = 1000
MAX_JISHO_WORDS = 6

KANJI = re.compile(r"[\u4e00-\u9fff々]")
kks = pykakasi.kakasi()


def furigana(text):
    """Return (teks dengan furigana, daftar kata ber-kanji)."""
    out, words = [], []
    for seg in kks.convert(text):
        orig, hira = seg["orig"], seg["hira"]
        if KANJI.search(orig) and orig != hira:
            out.append(f"{orig}({hira})")
            if orig not in words:
                words.append(orig)
        else:
            out.append(orig)
    return "".join(out), words


def jisho_line(words):
    links = [f"[{w}](https://jisho.org/search/{quote(w)})" for w in words[:MAX_JISHO_WORDS]]
    return "📖 " + " ・ ".join(links) if links else ""


def fetch_feed(cat):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    last_err = None
    for tpl in FEED_URL_TEMPLATES:
        url = tpl.format(cat=cat)
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.read()
            except Exception as e:
                last_err = e
                print(f"Gagal {url} (percobaan {attempt + 1}): {e}", file=sys.stderr)
                time.sleep(3)
    raise last_err


def parse_items(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter("item"):
        items.append({
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "summary": re.sub(r"<[^>]+>", "", it.findtext("description") or "").strip(),
            "pub": (it.findtext("pubDate") or "").strip(),
        })
    return [i for i in items if i["link"]]


def load_seen():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return []


def post_to_discord(webhook, item, feed):
    fur_title, words = furigana(item["title"])
    desc = f"**{fur_title}**"
    if item["summary"]:
        fur_summary, _ = furigana(item["summary"][:200])
        desc += "\n\n" + fur_summary
    jl = jisho_line(words)
    if jl:
        desc += "\n\n" + jl

    embed = {
        "title": item["title"][:256],
        "url": item["link"],
        "description": desc[:4000],
        "color": feed["color"],
        "footer": {"text": f"NHK NEWS WEB • {feed['name']}"},
    }
    try:
        embed["timestamp"] = parsedate_to_datetime(item["pub"]).isoformat()
    except Exception:
        pass

    req = urllib.request.Request(
        webhook,
        data=json.dumps({"embeds": [embed]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "nhk-discord-bot"},
    )
    for _ in range(3):
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(json.loads(e.read()).get("retry_after", 2))
                time.sleep(wait + 0.5)
                continue
            print(f"Discord error {e.code}: {e.read()[:200]}", file=sys.stderr)
            return False
    return False


def main():
    default_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not default_webhook:
        sys.exit("DISCORD_WEBHOOK_URL belum di-set")

    seen = load_seen()
    seen_set = set(seen)
    added, failed_feeds = [], []

    for feed in FEEDS:
        webhook = os.environ.get(feed["env"]) or default_webhook
        try:
            items = parse_items(fetch_feed(feed["cat"]))
            if not items:
                raise RuntimeError("feed kosong")
        except Exception as e:
            print(f"[{feed['name']}] dilewati: {e}", file=sys.stderr)
            failed_feeds.append(feed["name"])
            continue

        # kategori baru (belum ada satupun link-nya di catatan) -> jangan banjir
        first = not any(i["link"] in seen_set for i in items)
        new = [i for i in items if i["link"] not in seen_set]
        to_post = new[:FIRST_RUN_POST] if first else new[:MAX_POST_PER_RUN]

        failed_posts = set()
        for i in reversed(to_post):  # lama -> baru
            if post_to_discord(webhook, i, feed):
                print(f"[{feed['name']}] terkirim: {i['title']}")
            else:
                failed_posts.add(i["link"])
            time.sleep(1)

        for i in new:
            if i["link"] not in failed_posts:  # yang gagal kirim dicoba lagi run berikutnya
                seen_set.add(i["link"])
                added.append(i["link"])
        print(f"[{feed['name']}] baru: {len(new)}, dikirim: {len(to_post) - len(failed_posts)}")

    STATE_FILE.write_text(
        json.dumps((added + seen)[:MAX_STATE], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if failed_feeds:
        sys.exit(f"Feed gagal: {', '.join(failed_feeds)}")


if __name__ == "__main__":
    main()
