import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

LIST_URL = "https://www3.nhk.or.jp/news/easy/news-list.json"
ARTICLE_URL = "https://www3.nhk.or.jp/news/easy/{id}/{id}.html"
STATE_FILE = Path("seen.json")
MAX_POST_PER_RUN = 5   # batas kirim per run biar ga spam
FIRST_RUN_POST = 3     # run pertama: kirim 3 terbaru aja
MAX_STATE = 500        # simpan 500 id terakhir
JST = timezone(timedelta(hours=9))


def fetch_json(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (nhk-easy-discord-bot)"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        # utf-8-sig buang BOM kalau ada
        return json.loads(r.read().decode("utf-8-sig"))


def ruby_to_text(html):
    """<ruby>漢字<rt>かんじ</rt></ruby> -> 漢字(かんじ)"""
    html = re.sub(r"<rp>.*?</rp>", "", html)
    html = re.sub(r"<ruby>(.*?)<rt>(.*?)</rt></ruby>", r"\1(\2)", html)
    return re.sub(r"<[^>]+>", "", html).strip()


def get_articles():
    data = fetch_json(LIST_URL)
    articles = []
    for day in data:  # list berisi dict {tanggal: [artikel, ...]}
        for _date, items in day.items():
            articles.extend(items)
    articles.sort(key=lambda a: a.get("news_prepub_time", ""), reverse=True)
    return articles


def load_seen():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return []


def post_to_discord(webhook, article):
    news_id = article["news_id"]
    url = ARTICLE_URL.format(id=news_id)
    plain = article.get("title", "")
    with_furigana = ruby_to_text(article.get("title_with_ruby", plain))

    embed = {
        "title": plain[:256],
        "url": url,
        "description": f"**Furigana:** {with_furigana}"[:4000],
        "color": 0xE60012,
        "footer": {"text": "NHK NEWS WEB EASY"},
    }
    try:
        dt = datetime.strptime(article["news_prepub_time"], "%Y-%m-%d %H:%M:%S")
        embed["timestamp"] = dt.replace(tzinfo=JST).isoformat()
    except Exception:
        pass

    body = json.dumps({"embeds": [embed]}).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "nhk-easy-discord-bot"},
    )
    for attempt in range(3):
        try:
            urllib.request.urlopen(req, timeout=30).read()
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limit
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

    articles = get_articles()
    if not articles:
        sys.exit("List artikel kosong, format endpoint mungkin berubah")

    seen = load_seen()
    first_run = len(seen) == 0
    new = [a for a in articles if a["news_id"] not in seen]

    if first_run:
        to_post = new[:FIRST_RUN_POST]
    else:
        to_post = new[:MAX_POST_PER_RUN]

    # kirim dari yang paling lama biar urutan di channel benar
    for a in reversed(to_post):
        if post_to_discord(webhook, a):
            print(f"Terkirim: {a['news_id']}")
        time.sleep(1)

    # tandai semua yang baru sebagai sudah dilihat
    seen = [a["news_id"] for a in new] + seen
    STATE_FILE.write_text(
        json.dumps(seen[:MAX_STATE], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Selesai. Baru: {len(new)}, dikirim: {len(to_post)}")


if __name__ == "__main__":
    main()
