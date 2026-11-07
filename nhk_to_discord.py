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

import pykakasi

# cat0 = berita utama. Lainnya: cat2 budaya/hiburan, cat3 sains/medis,
# cat6 internasional, cat7 olahraga
FEED_URLS = [
    "https://www.nhk.or.jp/rss/news/cat0.xml",
    "https://news.web.nhk/n-data/conf/na/rss/cat0.xml",  # cadangan
]
STATE_FILE = Path("seen.json")
MAX_POST_PER_RUN = 5
FIRST_RUN_POST = 3
MAX_STATE = 500

KANJI = re.compile(r"[\u4e00-\u9fff々]")
kks = pykakasi.kakasi()


def add_furigana(text):
    """漢字 -> 漢字(かんじ). Hasil otomatis, kadang ada bacaan yang meleset."""
    out = []
    for seg in kks.convert(text):
        orig, hira = seg["orig"], seg["hira"]
        if KANJI.search(orig) and orig != hira:
            out.append(f"{orig}({hira})")
        else:
            out.append(orig)
    return "".join(out)


def fetch_feed():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    last_err = None
    for url in FEED_URLS:
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


def post_to_discord(webhook, item):
    title = item["title"]
    desc = f"**{add_furigana(title)}**"
    if item["summary"]:
        desc += "\n\n" + add_furigana(item["summary"][:200])

    embed = {
        "title": title[:256],
        "url": item["link"],
        "description": desc[:4000],
        "color": 0xE60012,
        "footer": {"text": "NHK NEWS WEB"},
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
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        sys.exit("DISCORD_WEBHOOK_URL belum di-set")

    items = parse_items(fetch_feed())
    if not items:
        sys.exit("Feed kosong, format mungkin berubah")

    seen = load_seen()
    first_run = len(seen) == 0
    new = [i for i in items if i["link"] not in seen]
    to_post = new[:FIRST_RUN_POST] if first_run else new[:MAX_POST_PER_RUN]

    for i in reversed(to_post):  # lama -> baru
        if post_to_discord(webhook, i):
            print(f"Terkirim: {i['title']}")
        time.sleep(1)

    seen = [i["link"] for i in new] + seen
    STATE_FILE.write_text(
        json.dumps(seen[:MAX_STATE], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Selesai. Baru: {len(new)}, dikirim: {len(to_post)}")


if __name__ == "__main__":
    main()
