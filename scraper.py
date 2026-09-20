import requests
import json
import hashlib
import re
import time
import os
import unicodedata
from datetime import datetime, timezone, timedelta
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO

try:
    from curl_cffi import requests as cffi_requests
    _HAVE_CURL_CFFI = True
except ImportError:
    _HAVE_CURL_CFFI = False

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

API_BASE     = "https://api.plapi202624081158.com"
SITE         = "https://phalang.live"
SITE_REFERER = "https://phalang.live/"
SITE_LOGO    = "https://phalang.live/favicon.png"

START_DATE_IS_UTC   = True   # start_date là UTC (đã kiểm chứng). Giờ lệch 7h -> đổi False
ONLY_BLV            = True   # True = chỉ lấy trận có bình luận viên
INCLUDE_MIRROR_HOST = False   # True = giữ link "(dự phòng)" pull1; False = mỗi BLV 1 link

LIMIT          = 50
PAGE_CAP       = 4
WINDOW_PAST_H  = 6      # drop trận đã kick off quá 6h (nếu không live)
FOOTBALL_FUT_H = 24     # football: tối đa +24h
MAX_LIVE_CALLS = 100

THUMBS_DIR    = "thumbs"
REPO_RAW      = os.environ.get("REPO_RAW", "")
THUMB_VERSION = "p4"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}
GRAPH_HEADERS = dict(HEADERS, Referer=SITE_REFERER, Origin=SITE)

CATE_MAP = {
    "football": "⚽ Bóng Đá", "basketball": "🏀 Bóng Rổ", "tennis": "🎾 Tennis",
    "bongchuyen": "🏐 Bóng Chuyền", "esport": "🎮 Esport", "caulong": "🏸 Cầu Lông",
    "vothuat": "🥊 Võ Thuật", "bongchay": "⚾ Bóng Chày", "duaxe": "🏎️ Đua Xe",
    "bongban": "🏓 Bóng Bàn", "billiards": "🎱 Billiards",
}
CATE_ORDER = ["football", "basketball", "tennis", "bongchuyen", "esport", "caulong",
              "vothuat", "bongchay", "duaxe", "bongban", "billiards"]

DESC_CATE_MAP = {
    "FOOTBALL": "football", "BASKETBALL": "basketball", "TENNIS": "tennis",
    "VOLLEYBALL": "bongchuyen", "BADMINTON": "caulong", "ESPORT": "esport",
    "ESPORTS": "esport", "TABLE TENNIS": "bongban", "BASEBALL": "bongchay",
    "AMERICAN FOOTBALL": "bongmy", "HANDBALL": "bantay", "ICE HOCKEY": "hockey",
    "HOCKEY": "hockey", "RUGBY": "rugby", "BOXING": "vothuat", "MMA": "vothuat",
    "UFC": "vothuat", "SNOOKER": "billiards", "BILLIARDS": "billiards",
    "FORMULA 1": "duaxe", "F1": "duaxe", "MOTOGP": "duaxe", "MOTORSPORT": "duaxe",
}

EXCLUDE_LEAGUES_AMERICA = [
    "mls", "major league soccer", "liga mx", "brasileirao", "brasileirão", "serie a brasil",
    "campeonato brasileiro", "copa do brasil", "argentine", "argentina", "liga profesional",
    "colombian", "colombia", "liga betplay", "chile", "ecuador", "peru", "venezuela",
    "paraguay", "uruguay", "bolivia", "inter miami", "la galaxy", "concacaf", "conmebol",
    "copa america", "copa sudamericana", "copa libertadores",
]

# ─────────────────────────────────────────────────────────────────────────────
# TIME & UTILS
# ─────────────────────────────────────────────────────────────────────────────

VN_TZ = timezone(timedelta(hours=7))

def now_vn() -> datetime:
    return datetime.now(tz=VN_TZ)

def parse_start(s: str):
    if not s: return None
    try:
        dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        if START_DATE_IS_UTC:
            dt = dt.replace(tzinfo=timezone.utc).astimezone(VN_TZ)
        else:
            dt = dt.replace(tzinfo=VN_TZ)
        return dt
    except Exception:
        return None

def http_get(url, headers=None, timeout=15, allow_redirects=True):
    h = dict(HEADERS); h.update(headers or {})
    if _HAVE_CURL_CFFI:
        return cffi_requests.get(url, headers=h, timeout=timeout,
                                 impersonate="chrome", allow_redirects=allow_redirects)
    return requests.get(url, headers=h, timeout=timeout, allow_redirects=allow_redirects)

def http_post_json(url, body, headers=None, timeout=15):
    h = dict(HEADERS); h["Content-Type"] = "application/json"; h.update(headers or {})
    if _HAVE_CURL_CFFI:
        return cffi_requests.post(url, headers=h, json=body, timeout=timeout, impersonate="chrome")
    return requests.post(url, headers=h, json=body, timeout=timeout)

def match_keywords(text, keywords):
    if not text: return False
    tl = text.lower()
    return any(re.search(rf"\b{re.escape(kw.lower())}\b", tl) for kw in keywords)

def is_america_league(name): return match_keywords(name, EXCLUDE_LEAGUES_AMERICA)

def make_id(text, prefix): return f"{prefix}-{hashlib.md5(text.encode()).hexdigest()[:10]}"

def norm_text(s): return re.sub(r"\s+", " ", (s or "").strip())

def slugify(t):
    s = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "tran-dau"

def get_stream_type(url):
    if not url: return "hls"
    clean = url.lower().split("?")[0]
    if clean.endswith(".flv"): return "httpflv"
    if clean.endswith(".mpd"): return "dash"
    if clean.endswith(".mp4"): return "mp4"
    return "hls"

# ─────────────────────────────────────────────────────────────────────────────
# FETCH /matches/graph — chỉ trận có BLV
# ─────────────────────────────────────────────────────────────────────────────

Q_HAS_BLV = {"field": "blv", "type": "not_equal", "value": ""}

def fetch_graph_page(body):
    for attempt in range(2):
        try:
            r = http_post_json(f"{API_BASE}/matches/graph", body, headers=GRAPH_HEADERS, timeout=15)
            if r.status_code == 200:
                return r.json()
            print(f"  [graph] HTTP {r.status_code} | body={json.dumps(body, ensure_ascii=False)[:120]}")
            return None
        except Exception as e:
            if attempt == 0:
                time.sleep(1)
            else:
                print(f"  [graph] FAIL {type(e).__name__}: {e}")
    return None

def discover_range_type():
    lo = (now_vn() - timedelta(hours=WINDOW_PAST_H)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    hi = (now_vn() + timedelta(hours=FOOTBALL_FUT_H)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    cands = [("greater", lo), ("gte", lo), ("gt", lo), ("greater_than", lo), ("after", lo),
             ("range", [lo, hi]), ("between", [lo, hi])]
    for typ, val in cands:
        d = fetch_graph_page({"queries": [{"field": "start_date", "type": typ, "value": val}],
                              "order_asc": "start_date", "limit": 5, "page": 1})
        rows = (d or {}).get("data") or []
        if not rows: continue
        starts = [str(r.get("start_date") or "") for r in rows]
        if all(s >= lo for s in starts) and lo <= starts[0] <= hi and starts == sorted(starts):
            print(f"  ✅ Range query hoạt động: type='{typ}'")
            return typ, isinstance(val, list)
        time.sleep(0.2)
    print("  ⚠️ Không tìm ra range query — fallback: trận hot có BLV")
    return None, False

def collect_matches():
    seen = {}

    def add(rows):
        for m in rows or []:
            if ONLY_BLV and not norm_text(m.get("blv")):
                continue
            mid = m.get("id")
            if mid and mid not in seen:
                seen[mid] = m

    # 1) LIVE + có BLV
    page = 1
    while page <= PAGE_CAP:
        d = fetch_graph_page({"queries": [{"field": "is_live", "type": "equal", "value": True}, Q_HAS_BLV],
                              "query_and": True, "limit": LIMIT, "page": page})
        if not d: break
        rows = d.get("data") or []
        add(rows)
        total = d.get("total") or 0
        if not rows or page * LIMIT >= total: break
        page += 1; time.sleep(0.2)
    print(f"  LIVE có BLV: {len(seen)} trận")

    # 2) Sắp đấu có BLV: range start_date (nếu discover được), fallback is_hot
    rng_typ, rng_list = discover_range_type()
    if rng_typ:
        lo = (now_vn() - timedelta(hours=WINDOW_PAST_H)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        hi = (now_vn() + timedelta(hours=FOOTBALL_FUT_H + 6)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        val = [lo, hi] if rng_list else lo
        before = len(seen)
        page = 1
        while page <= PAGE_CAP:
            d = fetch_graph_page({"queries": [{"field": "start_date", "type": rng_typ, "value": val}, Q_HAS_BLV],
                                  "query_and": True, "order_asc": "start_date", "limit": LIMIT, "page": page})
            if not d: break
            rows = d.get("data") or []
            add(rows)
            if not rows or any(str(r.get("start_date") or "") > hi for r in rows): break
            page += 1; time.sleep(0.2)
        print(f"  Sắp đấu có BLV (range): +{len(seen)-before} trận")
    else:
        for page in (1, 2):
            d = fetch_graph_page({"queries": [{"field": "is_hot", "type": "equal", "value": True}, Q_HAS_BLV],
                                  "query_and": True, "order_asc": "start_date", "limit": LIMIT, "page": page})
            if not d: break
            add(d.get("data") or [])
            if len(d.get("data") or []) < LIMIT: break
            time.sleep(0.2)
        print(f"  Fallback (hot + BLV): tổng {len(seen)} trận")

    return list(seen.values())

# ─────────────────────────────────────────────────────────────────────────────
# LINK STREAM — chỉ feed BLV (digitalcdn), loại feed nhà đài (lilive 'source')
# ─────────────────────────────────────────────────────────────────────────────

_live_call_count = 0

def fetch_live_links(mid):
    global _live_call_count
    if _live_call_count >= MAX_LIVE_CALLS:
        return {}
    _live_call_count += 1
    for attempt in range(2):
        try:
            r = http_get(f"{API_BASE}/match/{mid}/live", headers=GRAPH_HEADERS, timeout=12)
            if r.status_code != 200:
                print(f"  [/live] HTTP {r.status_code} | id={mid}")
                return {}
            d = r.json()
            out = {}
            for k in ("source", "hd_1", "hd_2", "sd_1", "sd_2"):
                v = d.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    out[k] = v
            return out
        except Exception as e:
            if attempt == 0: time.sleep(1)
            else: print(f"  [/live] FAIL {type(e).__name__}: {e} | id={mid}")
    return {}

def prebuilt_digitalcdn(stream_key):
    return {
        "hd_1": f"https://pull.digitalcdn.net/live/{stream_key}/index.m3u8",
        "hd_2": f"https://pull1.digitalcdn.net/live/{stream_key}/index.m3u8",
    }

FIELD_ORDER    = ("hd_1", "hd_2", "sd_1", "sd_2", "source")
PRIMARY_FIELDS = ("hd_1", "sd_1", "source")   # mirror = hd_2/sd_2

def links_for_entry(entry):
    """
    Feed BLV = digitalcdn (hd_/sd_). Field 'source' (lilive) là feed NHÀ ĐÀI
    dùng chung cả trận -> BỎ (trừ khi lỡ là digitalcdn và không có hd/sd nào).
    BLV chưa có feed riêng -> trả [] (main sẽ SKIP + log lý do).
    """
    base = norm_text(entry.get("blv")) or "Trực Tiếp"

    fetched = fetch_live_links(entry["id"]) if entry["is_live"] else {}
    if not fetched and entry.get("live_integrated") and entry.get("stream_key"):
        fetched = prebuilt_digitalcdn(entry["stream_key"])

    feeds = {k: v for k, v in fetched.items() if k.startswith(("hd_", "sd_"))}
    if not feeds and "source" in fetched and "digitalcdn" in fetched["source"]:
        feeds["source"] = fetched["source"]
    if not feeds:
        return []

    fields = [f for f in FIELD_ORDER if f in feeds]
    if not INCLUDE_MIRROR_HOST:
        non_mirror = [f for f in fields if f in PRIMARY_FIELDS]
        fields = non_mirror if non_mirror else fields[:1]

    label_map = {
        "hd_1":   base,
        "hd_2":   f"{base} (dự phòng)",
        "sd_1":   f"{base} SD",
        "sd_2":   f"{base} SD (dự phòng)",
        "source": base,
    }

    out, seen_urls = [], set()
    for field in fields:
        url = feeds[field]
        if url and url not in seen_urls:
            seen_urls.add(url); out.append((label_map[field], url))
    return out

# ─────────────────────────────────────────────────────────────────────────────
# DIAGNOSTIC digitalcdn (nhiều biến thể Referer)
# ─────────────────────────────────────────────────────────────────────────────

def diag_digitalcdn(url, page_url=None):
    print("\n─── DIAG digitalcdn raw link ───")
    if not _HAVE_CURL_CFFI:
        print("   (bỏ qua — chưa cài curl_cffi; pip install curl_cffi rồi chạy lại)")
        return
    variants = [("Referer = root", {"Referer": SITE_REFERER})]
    if page_url:
        variants.append(("Referer = trang match", {"Referer": page_url}))
    variants.append(("Full browser headers", {
        "Referer": page_url or SITE_REFERER,
        "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin",
        "Accept": "*/*",
    }))
    for label, hdrs in variants:
        try:
            r = http_get(url, headers=hdrs, timeout=12, allow_redirects=False)
            loc = (r.headers.get("Location") or "")[:120]
            print(f"   [{label}] {r.status_code}" + (f" -> {loc}" if loc else ""))
            if r.status_code in (301, 302, 307, 308):
                print("   ✅✅ Raw tự 302 cấp token theo IP người xem -> kiến trúc CHUẨN")
                return
            if r.status_code == 200:
                print("   ✅ 200 public")
                return
        except Exception as e:
            print(f"   [{label}] ❌ {type(e).__name__}: {e}")
    print("   ⛔ Mọi biến thể đều bị chặn TỪ SERVER NÀY — không kết luận được cho người dùng thật.")
    print("      Bắt buộc test link digitalcdn bằng app/VLC trên mạng residential (4G/wifi nhà).")

# ─────────────────────────────────────────────────────────────────────────────
# THUMBNAIL
# ─────────────────────────────────────────────────────────────────────────────

def fetch_image(url):
    try:
        res = http_get(url, timeout=8)
        res.raise_for_status()
        return Image.open(BytesIO(res.content)).convert("RGBA")
    except Exception:
        return None

def make_thumbnail(match, match_id_safe):
    os.makedirs(THUMBS_DIR, exist_ok=True)
    cache_key = (match.get("logo_a") or "") + (match.get("logo_b") or "") + THUMB_VERSION
    logo_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]
    date_str = now_vn().strftime("%Y%m%d")
    out_path = f"{THUMBS_DIR}/{match_id_safe}_{logo_hash}_{date_str}.png"
    if os.path.exists(out_path):
        return out_path

    W, H = 1600, 1200
    HEADER_H, FOOTER_H = 180, 160
    bg = Image.new("RGB", (W, H), (245, 245, 248))
    draw = ImageDraw.Draw(bg)
    for y in range(HEADER_H, H - FOOTER_H):
        ratio = (y - HEADER_H) / (H - FOOTER_H - HEADER_H)
        gray = int(248 - ratio * 18)
        draw.line([(0, y), (W, y)], fill=(gray, gray, gray + 4))
    draw.rectangle([(0, 0), (W, HEADER_H)], fill=(13, 20, 40))
    draw.rectangle([(0, H - FOOTER_H), (W, H)], fill=(13, 20, 40))
    ACCENT = (78, 166, 72)
    draw.rectangle([(0, HEADER_H), (W, HEADER_H + 5)], fill=ACCENT)
    draw.rectangle([(0, H - FOOTER_H - 5), (W, H - FOOTER_H)], fill=ACCENT)

    FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    try:
        font_vs = ImageFont.truetype(FONT_BOLD, 160)
        font_time = ImageFont.truetype(FONT_BOLD, 100)
        font_team = ImageFont.truetype(FONT_BOLD, 58)
    except Exception:
        font_vs = font_time = font_team = ImageFont.load_default()

    content_top, content_bot = HEADER_H + 5, H - FOOTER_H - 5
    content_h = content_bot - content_top
    logo_size, name_h, time_h = 360, 120, 110
    gap_logo_name, gap_name_time = 40, 60
    block_top = content_top + (content_h - (logo_size + gap_logo_name + name_h + gap_name_time + time_h)) // 2
    logo_y = block_top
    name_center = block_top + logo_size + gap_logo_name + name_h // 2
    time_y = block_top + logo_size + gap_logo_name + name_h + gap_name_time + time_h // 2

    def draw_team_name(text, cx):
        max_width, font_size, f = W // 2 - 60, 58, font_team
        while font_size >= 28:
            try: f = ImageFont.truetype(FONT_BOLD, font_size)
            except Exception: f = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), text, font=f)
            if (bbox[2] - bbox[0]) <= max_width: break
            font_size -= 3
        draw.text((cx, name_center), text, fill=(20, 20, 20), font=f, anchor="mm")

    for key, cx in (("logo_a", W // 4), ("logo_b", W * 3 // 4)):
        if match.get(key):
            img = fetch_image(match[key])
            if img:
                try:
                    resized = img.resize((logo_size, logo_size), Image.LANCZOS)
                    bg.paste(resized, (cx - logo_size // 2, logo_y), resized)
                except Exception:
                    pass

    draw.text((W // 2, logo_y + logo_size // 2), "VS", fill=ACCENT, font=font_vs, anchor="mm")
    if match.get("team_a"): draw_team_name(match["team_a"], W // 4)
    if match.get("team_b"): draw_team_name(match["team_b"], W * 3 // 4)

    time_display = f"{match.get('time','')} {match.get('date','')}".strip()
    if time_display:
        font_size, f_time = 100, font_time
        while font_size >= 40:
            try: f_time = ImageFont.truetype(FONT_BOLD, font_size)
            except Exception: f_time = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), time_display, font=f_time)
            if (bbox[2] - bbox[0]) <= W - 100: break
            font_size -= 4
        draw.text((W // 2 + 4, time_y + 4), time_display, fill=ACCENT, font=f_time, anchor="mm")
        draw.text((W // 2, time_y), time_display, fill=(15, 15, 15), font=f_time, anchor="mm")

    if match.get("league"):
        league_text = match["league"].upper()
        font_size, f = 62, None
        while font_size >= 28:
            try: f = ImageFont.truetype(FONT_BOLD, font_size)
            except Exception: f = ImageFont.load_default()
            bbox = draw.textbbox((0, 0), league_text, font=f)
            if (bbox[2] - bbox[0]) <= W - 60: break
            font_size -= 3
        draw.text((W // 2, HEADER_H // 2), league_text, fill=(255, 255, 255), font=f, anchor="mm")

    draw.rectangle([(0, 0), (W - 1, H - 1)], outline=(180, 180, 180), width=3)
    bg.save(out_path, "PNG", optimize=True)
    return out_path

def cleanup_old_thumbs(days=3):
    if not os.path.exists(THUMBS_DIR): return
    cutoff = now_vn() - timedelta(days=days)
    for fname in os.listdir(THUMBS_DIR):
        if not fname.endswith(".png"): continue
        m = re.search(r'_(\d{8})\.png$', fname)
        if m:
            try:
                if datetime.strptime(m.group(1), "%Y%m%d").replace(tzinfo=VN_TZ) < cutoff:
                    os.remove(os.path.join(THUMBS_DIR, fname))
            except Exception:
                pass

# ─────────────────────────────────────────────────────────────────────────────
# ENTRIES / GROUPING / CHANNEL
# ─────────────────────────────────────────────────────────────────────────────

def in_window(entry):
    if entry["is_live"]: return True
    ko = entry["start_dt"]
    if ko is None: return True
    now = now_vn()
    if ko < now - timedelta(hours=WINDOW_PAST_H): return False
    if entry["cate_type"] == "football" and ko > now + timedelta(hours=FOOTBALL_FUT_H): return False
    return True

def build_entries(raw_matches):
    entries = []
    for m in raw_matches:
        blv = norm_text(m.get("blv"))
        if ONLY_BLV and not blv:
            continue
        league = norm_text(m.get("league"))
        desc = norm_text(m.get("desc")).upper()
        cate_type = DESC_CATE_MAP.get(desc, re.sub(r"[^a-z0-9]+", "_", desc.lower()).strip("_") or "football")
        if cate_type == "football" and is_america_league(league):
            continue
        t1, t2 = norm_text(m.get("team_1")), norm_text(m.get("team_2"))
        if not t1 and not t2:
            t1 = norm_text(m.get("title")) or league or "Đội A"
        mid = m.get("id") or ""
        entries.append({
            "id": mid,
            "blv": blv,
            "is_live": bool(m.get("is_live")),
            "live_integrated": bool(m.get("live_integrated")),
            "stream_key": m.get("stream_key") or "",
            "team_a": t1 or "Đội A",
            "team_b": t2 or "Đội B",
            "logo_a": m.get("team_1_logo") or "",
            "logo_b": m.get("team_2_logo") or "",
            "league": league,
            "start_dt": parse_start(m.get("start_date") or ""),
            "cate_type": cate_type,
            "page_url": f"{SITE}/truc-tiep/{slugify((t1 or 'doi-a') + '-vs-' + (t2 or 'doi-b'))}-{mid}",
        })
    return [e for e in entries if in_window(e)]

def group_entries(entries):
    groups = {}
    for e in entries:
        key = (norm_text(f'{e["team_a"]} - {e["team_b"]}'), str(e["start_dt"]), e["league"])
        groups.setdefault(key, []).append(e)
    return groups

def build_channel(gkey, gentries, streams):
    first = gentries[0]
    match_id_safe = first["id"].replace(":", "-").replace("/", "-") or make_id(str(gkey), "mid")
    uid, src_id = make_id(match_id_safe, "pl"), make_id(match_id_safe, "src")
    ct_id, st_id = make_id(match_id_safe, "ct"), make_id(match_id_safe, "st")

    is_live = any(e["is_live"] for e in gentries)
    blv_names = sorted({e["blv"] for e in gentries if e["blv"]})

    stream_links = []
    for name, url in streams:
        stream_links.append({
            "id": make_id(url + name, "lnk"),
            "name": name,
            "type": get_stream_type(url),
            "default": len(stream_links) == 0,
            "url": url,
            "request_headers": [
                {"key": "Referer", "value": SITE_REFERER},
                {"key": "User-Agent", "value": HEADERS["User-Agent"]},
            ],
        })

    ko = first["start_dt"]
    time_fmt = ko.strftime("%H:%M") if ko else ""
    date_fmt = ko.strftime("%d/%m") if ko else ""
    time_full = ko.strftime("%H:%M:%S") if ko else ""   # org_metadata time có giây (giovang)
    display_name = f'{first["team_a"]} vs {first["team_b"]}'
    if time_fmt: display_name += f" | {time_fmt} {date_fmt}"

    # ★ Schema giovang: đúng 1 label top-left
    labels = [{"text": "● LIVE" if is_live else "🕐 Sắp",
               "position": "top-left", "color": "#00000080",
               "text_color": "#ff4444" if is_live else "#aaaaaa"}]

    channel = {
        "id": uid,
        "name": display_name,
        "type": "single",
        "display": "thumbnail-only",
        "enable_detail": False,
        "labels": labels,
        "sources": [{
            "id": src_id,
            "name": "PhaLangTV",
            "contents": [{
                "id": ct_id,
                "name": f'{first["team_a"]} vs {first["team_b"]}',
                "streams": [{"id": st_id, "name": "PL", "stream_links": stream_links}],
            }],
        }],
        # ★ Schema giovang: đúng 10 key
        "org_metadata": {
            "league": first["league"],
            "team_a": first["team_a"], "team_b": first["team_b"],
            "logo_a": first["logo_a"], "logo_b": first["logo_b"],
            "time": time_full, "date": date_fmt,
            "blv": ", ".join(blv_names),
            "is_live": is_live,
            "cate_type": first["cate_type"],
        },
    }

    thumb_path = make_thumbnail({
        "team_a": first["team_a"], "team_b": first["team_b"],
        "logo_a": first["logo_a"], "logo_b": first["logo_b"],
        "league": first["league"], "time": time_fmt, "date": date_fmt,
    }, match_id_safe)
    cache_key = first["logo_a"] + first["logo_b"] + THUMB_VERSION
    logo_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]
    if REPO_RAW:
        channel["image"] = {"padding": 1, "background_color": "#ffffff", "display": "contain",
                            "url": f"{REPO_RAW}/{thumb_path}?v={logo_hash}", "width": 1600, "height": 1200}
    return channel

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    os.makedirs(THUMBS_DIR, exist_ok=True)
    cleanup_old_thumbs(days=3)
    print(f"Gio VN hien tai : {now_vn().strftime('%H:%M %d/%m/%Y')}")
    print(f"HTTP client     : {'curl_cffi (impersonate=chrome)' if _HAVE_CURL_CFFI else 'python-requests (khuyen nghi: pip install curl_cffi)'}")
    print(f"Che do          : CHI TRAN CO BLV | mirror host: {'BAT' if INCLUDE_MIRROR_HOST else 'TAT'}")

    print("\n1) Lay danh sach tran CO BLV tu /matches/graph ...")
    raw = collect_matches()
    entries = build_entries(raw)
    print(f"   Sau filter thoi gian/quoc gia: {len(entries)} entry BLV")

    print("\n2) Ghep BLV cung tran + lay/prebuild link ...")
    groups = group_entries(entries)
    built = []
    diag_done = False
    for gi, (gkey, gentries) in enumerate(sorted(groups.items(),
            key=lambda kv: (0 if any(e["is_live"] for e in kv[1]) else 1,
                            kv[1][0]["start_dt"] or datetime.max.replace(tzinfo=VN_TZ)))):

        streams = []
        for e in gentries:
            streams += links_for_entry(e)
        seen, uniq = set(), []
        for name, url in streams:
            if url not in seen:
                seen.add(url); uniq.append((name, url))
        streams = uniq

        if not diag_done and streams and any(e["is_live"] for e in gentries):
            for _, u in streams:
                if "digitalcdn.net" in u:
                    diag_digitalcdn(u, page_url=gentries[0]["page_url"])
                    diag_done = True
                    break

        if not streams:
            names = ", ".join(e["blv"] or e["id"] for e in gentries)
            print(f'[SKIP {gi+1}/{len(groups)}] {gentries[0]["team_a"]} vs {gentries[0]["team_b"]} '
                  f'| BLV {names}: chua co feed rieng (thieu stream_key / API chua cap)')
            continue

        name_disp = f'{gentries[0]["team_a"]} vs {gentries[0]["team_b"]}'
        blv_str = ", ".join(sorted({e["blv"] for e in gentries if e["blv"]})) or "-"
        status = "LIVE" if any(e["is_live"] for e in gentries) else "SAP"
        tdisp = gentries[0]["start_dt"].strftime("%H:%M %d/%m") if gentries[0]["start_dt"] else "?"
        print(f'[{status} {gi+1}/{len(groups)}] {name_disp} ({tdisp}) | BLV: {blv_str} | {len(streams)} link')
        built.append(build_channel(gkey, gentries, streams))
        time.sleep(0.1)

    cate_channels = {}
    for ch in built:
        cate_channels.setdefault(ch["org_metadata"]["cate_type"], []).append(ch)

    out_groups = []
    for cate in CATE_ORDER:
        chs = cate_channels.pop(cate, [])
        if not chs: continue
        label = CATE_MAP[cate]
        live_cnt = sum(1 for c in chs if c["org_metadata"]["is_live"])
        name = f"{label} ({live_cnt} LIVE)" if live_cnt else label
        out_groups.append({"id": f"cate_{cate}", "name": name, "display": "vertical",
                           "grid_number": 2, "enable_detail": False, "channels": chs})
    for cate, chs in cate_channels.items():
        live_cnt = sum(1 for c in chs if c["org_metadata"]["is_live"])
        label = "🏅 " + cate.replace("_", " ").title()
        name = f"{label} ({live_cnt} LIVE)" if live_cnt else label
        out_groups.append({"id": f"cate_{cate}", "name": name, "display": "vertical",
                           "grid_number": 2, "enable_detail": False, "channels": chs})

    # ★ Schema giovang: root image bắt buộc
    output = {"id": "phalang", "url": SITE, "name": "PhaLangTV", "color": "#4ea648",
              "grid_number": 3,
              "image": {"type": "cover", "url": SITE_LOGO},
              "groups": out_groups}

    staging = "output_staging.json"
    with open(staging, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    total = sum(len(g["channels"]) for g in out_groups)

    def normalize(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.dumps(json.load(f), sort_keys=True, ensure_ascii=False)
        except Exception:
            return ""

    if normalize("output.json") != normalize(staging):
        os.replace(staging, "output.json")
        print(f"\n✅ Xong! {total} kenh BLV, {len(out_groups)} mon the thao -> output.json (DA CAP NHAT)")
    else:
        os.remove(staging)
        print(f"\n✅ Xong! {total} kenh BLV, {len(out_groups)} mon the thao -> Khong co thay doi")

    print("""
─── LƯU Ý ───
- Schema output da khop 100% giovang (1 label/kênh, org_metadata 10 key, root image).
- INCLUDE_MIRROR_HOST = False neu muon moi BLV chi 1 link (bo link 'du phong' pull1).
- Viec con treo: test link digitalcdn trong app/VLC tu mang khac (4G/wifi nha).
  DIAG 403 tu server chua ket luan duoc gi cho nguoi dung that.""")

if __name__ == "__main__":
    main()
