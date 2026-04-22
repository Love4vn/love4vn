import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urljoin

# -------------------- CẤU HÌNH --------------------
SPECIAL_URL = "https://raw.githubusercontent.com/t23-02/bongda/refs/heads/main/bongda.m3u"

# Danh sách kênh VTV (chuẩn)
VTV_CHANNELS = [
    "VTV1", "VTV2", "VTV3", "VTV4", "VTV5", "VTV6", "VTV7", "VTV8", "VTV9",
    "VTV CẦN THƠ", "VTV5 TÂY NAM BỘ", "VTV5 TÂY NGUYÊN", "VIETNAM TODAY"
]

# Danh sách kênh Giải Trí (chuẩn)
ENTERTAINMENT_CHANNELS = [
    "AXN", "HBO", "HBO HITS", "HBO FAMILY", "HBO SIGNATURE", "CINEMAX", "Ninja Warrior",
    "CINEMA WORLD", "DREAMWORKS", "BOX MOVIE 1", "HOLLYWOOD CLASSICS", "Wipeout Xtra",
    "BOX HITS", "WARNER TV", "CINEMAWORLD", "FOX FAMILY MOVIES", "FailArmy", "The Pet Collective", 
    "Love Pets", "Mythbusters", "River Monsters", "INWILD", "just for laughs", "Adventure Earth",
    "DISCOVERY CHANNEL", "DISCOVERY ASIA", "NATIONAL GEOGRAPHIC", "Gardeners' World",
    "ANIMAL PLANET", "MAN", "WOMAN", "FASHION TV", "OUTDOOR CHANNEL", "always funny videos", 
    "gardening with monty don"
]

# Từ khóa nhận diện kênh Thể Thao
SPORTS_INCLUDE_KEYWORDS = [
    'arsenal', 'aston villa', 'bournemouth', 'brentford', 'brighton', 'chelsea', 'crystal palace', 
    'everton', 'fulham', 'leeds united', 'liverpool', 'manchester city', 'manchester united', 
    'newcastle', 'nottingham forest', 'sunderland', 'tottenham hotspur', 'west ham united', 
    'wolverhampton', 'bayern', 'borussia dortmund', 'bayer leverkusen', 'inter milan', 'ac milan', 
    'napoli', 'barcelona', 'real madrid', 'atlético', 'psg', 'olympique marseille', 'thể thao',
    'the thao', 'sport', 'bóng đá', 'bong da', 'dazn', 'sports', 'spor', 'hub premier', 'premier',
    'mono max', 'astro', 'spotv', 'epl', 'football', 'soccer', 'tsn', 'la liga', 'laliga', 'bundesliga',
    'seriea', 'serie a', 'uefa', 'premier league', 'golf', 'tennis', '4k uhd', 'dstv now', 'canal+',
    'disney+ premium', 'fotball', 'viaplay', 'now tv uk', 'sky go', 'vidio', 'espn', 'usa network',
    'telemundo', 'sooka', 'peacock', 'tv3 max', 'movistar', 'cazétv', 'cazetv', 'tv360'
]

# Từ khóa loại trừ thể thao (giữ nguyên)
SPORTS_EXCLUDE_KEYWORDS = [
    'cricket', 'nhl', 'rugby', 'doku', 'tehlike', 'macer', 'orman', 'ada', 'dönüş', 'ejderha', 'elio',
    'brescia', 'dora', 'taş', 'sol dorado', 'the man who', 'bay', 'tüyü', 'pesinde', 'devi', '2 macera',
    'adasi', 'ormani', 'dönüs', 'ejderhan', 'eğitirsin', 'gulliver', 'alive', 'christmas', 'icetastrophe',
    'astronaut', 'olive', 'astroworld', 'abc news', 'golfinho', 'kaçık', 'gulliverin', 'gulliver', 'o dia',
    'pequenos', 'perde', 'untold', 'verônica', 'youre a good sport', 'winter', 'bloomberg', 'pierce',
    'eventura', 'entertainment', 'livecam', 'llbn', 'quran', 'music', 'livenow', 'wnbc', 'shop', 'wall street',
    'undefined', 'to live', 'eventy', 'happy event', 'serial', 'echo live', 'annie live', 'romanti', 'antenna',
    'bloodsport', 'drama', 'clive', 'eplica', 'deliver', 'tale', 'to the moon', 'karanlık', 'event 15',
    'movie', 'film', 'fantasy', 'fight to live', 'doküman', 'fletch', 'fluefa', 'death lives', 'hatayspor',
    'ulster', 'korku', 'horror', 'i live', 'pretty', 'replace', 'amelot', 'sporu', 'hikay', 'escape', 'is life',
    'kanunu', 'direction', 'spore', 'cheerleading', 'no one', 'passport', 'peepli', 'eplica', 'eplika',
    'secret live', 'shaolin', 'sleepless', 'slive', 'livet', 'strange event', 'spetsna', 'taking live',
    'burden', 'seventeen', 'victim', 'transfer', 'the main event', 'you live', 'seventh', 'their live',
    'transporter', 'fatty live', 'out live', 'can live', 'only live', 'lived', 'news', 'astro boy',
    'astro loco', 'çocuk', 'astronot', 'philippine', 'rastro', 'lastro', 'golfe', 'miicrosoft', 'eples',
    'golfinho', 'kaçık', 'gulliverin', 'gulliver', 'o dia', 'pequenos', 'perde', 'untold', 'verônica', 
    'UaH6R6YA', 'arsenal [', 'youre a good sport', 'winter', 'bloomberg', 'pierce', 'eventura', 
    'entertainment', 'livecam', 'llbn', 'quran', 'music', 'livenow', 'wnbc', 'shop', 'wall street', 
    'undefined', 'to live', 'eventy', 'happy event', 'serial', 'echo live', 'annie live', 'romanti', 
    'antenna', 'bloodsport', 'drama', 'clive', 'eplica', 'deliver', 'tale', 'to the moon', 'karanlik', 
    'event 15', 'movie', 'film', 'fantasy', 'fight to live', 'saldiri', 'fletch', 'fluefa', 'death lives', 
    'hatayspor', 'ulster', 'korku', 'horror', 'i live', 'pretty', 'replace', 'amelot', 'sporu', 'hikay', 
    'escape', 'is life', 'kanunu', 'direction', 'spore', 'cheerleading', 'no one', 'passport', 'peepli', 
    'eplica', 'eplika', 'secret live', 'shaolin', 'sleepless', 'slive', 'livet', 'strange event', 'spetsna', 
    'taking live', 'burden', 'seventeen', 'victim', 'transfer', 'the main event', 'you live', 'seventh', 
    'their live', 'transporter', 'fatty live', 'out live', 'can live', 'only live', 'lived', 'news', 'dram', 
    'vod', 'Neighbor', 'Tamil', 'bangla', 'Kâbusu', 'Engliah', 'hindi', 'cams', 'K+', 'astro tak', 'astro qj', 
    'astrocitra', 'Big Brother', 'astro ria', 'astro prima', 'astro citra', 'astro sensasi', 'astro warna', 
    'adultiptv', 'Married Meet', 'PD Presents', 'Astro Ceria', 'a melbourne', 'Annapolis', '6R6YA_big', 
    'FBmm6oXHjy', 'astro aec', 'astro aod', 'astro awani', 'astro blitar', 'astro happy', 'astro kid', 
    'barbie', 'bfl live', 'bird box', 'bleav', 'bn channel', 'body at', 'br event', 'brighton 4th', 'cinema', 
    'colimdot', 'colors', 'ceria', 'rainha', 'diaspora', 'ege live', 'MvdzZwM', 'garden of eden', 
    'golden premier', 'belive', 'grey garden', 'savage garden', 'daily live', 'camera', 'kbri', 'kiss', 
    'lemon tree', 'kamera', 'present', 'live99fm', 'sport 1 (drm)', 'sport 2 (drm)', 'premier 1tv', 'livee', 
    'married', 'mulan', 'matchstick', 'moon garden', 'mortal kombat', 'mr bean', 'ms. matched', 'mtv uutiset', 
    'in sırrı', 'the live', 'garoto', 'crossover', 'perfect match', 'kindred garden', 'Oe0hrS0', 'pilipinas', 
    'tv napoli', 'nexus tv', 'bangla', 'ekhon', 'jamuna', 'r+', 'radio', 'eplice', 'rbb event', 'nautical', 
    'Tamil', 'kanchi', 'swr event', 'gardener', 'hatton', 'the match', 'terror', 'luna napoli', 'evento', 
    'ovacion', 'overtime', 'insider', 'peacock [', 'poker go', 'moon', 'tvb jade', 'napolis', 'astrovi', 
    'allá', 'serbest', 'monstruos', 'sticks', 'termina', 'justicia', 'yakuza', 'inside', 'dioses', 'dinaria', 
    'astro (', 'hua hee', 'quan jia', 'tjk tv', 'consumer', 'teagarden', 'matchbox', 'courage', 'cristina', 
    'gaiden', 'bollywood', 'zee', 'post live', 'introuble', 'madein', 'meridiano', 'monterrico', 'wdr event', 
    'wolf garden', 'basco', 'livelihood', 'phuket', 'スlive', '▅ ▃ ▂', 'surpresa', 'spring', 'equidia', 
    'sangrento', 'só que', 'vtv (', 'iptvmate.net', 'kindred', 'uutiset', 'natal', 'divino', 'david', 
    'astro欢', 'dangal'
]

MOVIE_EXCLUDE_KEYWORDS = [
    'man [', 'man! (', 'woman [', 'wo man [',
]

SPORTS_RENAME_MAP = {
    "Sky Sports Action UK NOW": "Sky Sports Action UK (NOW)",
    "Sky Sports F1 UK NOW": "Sky Sports F1 UK (NOW)",
    "Sky Sports Football UK NOW": "Sky Sports Football UK (NOW)",
    "Sky Sports Golf UK NOW": "Sky Sports Golf UK (NOW)",
    "Sky Sports Main Event UK NOW": "Sky Sports Main Event UK (NOW)",
    "Sky Sports Mix UK NOW": "Sky Sports Mix UK (NOW)",
    "Sky Sports PL UK NOW": "Sky Sports PL UK (NOW)",
    "Sky Sports Racing UK NOW": "Sky Sports Racing UK (NOW)",
    "Sky Sports Tennis UK NOW": "Sky Sports Tennis UK (NOW)",
    "Sky Sports+ UK NOW": "Sky Sports+ UK (NOW)",
    "TNT Sport 1 NOW": "TNT Sport 1 (NOW)",
    "TNT Sport 2 NOW": "TNT Sport 2 (NOW)",
    "TNT Sport 3 NOW": "TNT Sport 3 (NOW)",
    "TNT Sport 4 NOW": "TNT Sport 4 (NOW)",
    ",TSN": "TSN",
    ",SPORTS TV": "SPORTS TV",
    ",FOOTBALL TV": "FOOTBALL TV",
    "ช่อง": " ",
}

VTV_ORDER = {name: i for i, name in enumerate(VTV_CHANNELS)}
ENT_ORDER = {name: i for i, name in enumerate(ENTERTAINMENT_CHANNELS)}

GROUP_ORDER = {
    "Kênh VTV": 1,
    "Giải Trí": 2,
    "Thể Thao": 3,
    "Trực tiếp": 4
}

# CHỈ GIỮ CÁC NGUỒN EPG ỔN ĐỊNH NHẤT
EPG_SOURCES = [
    "https://hnlive.dramahay.xyz/epg.xml",
    "https://raw.githubusercontent.com/karepech/Epgku/main/epg_wib_sports.xml",
    "https://raw.githubusercontent.com/bakulwifi/Epglive/refs/heads/main/epg.xml",
    "https://raw.githubusercontent.com/dbghelp/mewatch-EPG/refs/heads/main/mewatch.xml",
    "https://raw.githubusercontent.com/dbghelp/StarHub-TV-EPG/refs/heads/main/starhub.xml"
]

PLAYLIST_CACHE = {}

# -------------------- HÀM TIỆN ÍCH --------------------
def clean_channel_name(name):
    name = re.sub(r'group-title="[^"]*"', '', name)
    name = re.sub(r',+', ',', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def normalize_channel_name(name):
    name = re.sub(r'\[.*?\]', '', name)
    name = re.sub(r'\(.*?\)', '', name)
    name = re.sub(r'\b(hd|fhd|uhd|4k|sd|channel|tv|ch)\b', '', name, flags=re.IGNORECASE)
    name = re.sub(r'[^\w\s]', '', name)
    name = re.sub(r'\s+', ' ', name).strip().lower()
    return name

def build_normalized_set(channel_list):
    return {normalize_channel_name(name) for name in channel_list}

def is_sports_channel(name_lower):
    has_include = any(inc in name_lower for inc in SPORTS_INCLUDE_KEYWORDS)
    has_exclude = any(ex in name_lower for ex in SPORTS_EXCLUDE_KEYWORDS)
    return has_include and not has_exclude

def is_movie_excluded(name_lower):
    return any(ex in name_lower for ex in MOVIE_EXCLUDE_KEYWORDS)

def resolve_m3u8_url(url, max_depth=1, session=None):
    # Tạm thời vô hiệu hóa resolve để tăng tốc (ít khi dùng)
    return url

def check_channel_health(url, timeout=2):
    if url.startswith('udp://'):
        return True
    try:
        headers = {'User-Agent': 'VLC/3.0.18 LibVLC/3.0.18'}
        resp = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code < 400:
            return True
        if resp.status_code in (403, 452, 456, 405, 400) or resp.status_code >= 500:
            headers_range = headers.copy()
            headers_range['Range'] = 'bytes=0-1'
            resp2 = requests.get(url, headers=headers_range, timeout=timeout, allow_redirects=True)
            if resp2.status_code in (206, 200):
                return True
        return False
    except:
        return False

def is_low_resolution(resolution):
    if not resolution:
        return False
    resolution = resolution.lower()
    if 'sd' in resolution:
        return True
    low_patterns = [r'360p', r'480p', r'576p', r'360', r'480', r'576', r'low']
    for pattern in low_patterns:
        if re.search(pattern, resolution):
            return True
    numbers = re.findall(r'\d+', resolution)
    for num in numbers:
        if int(num) < 720:
            return True
    return False

def classify_channel(ch_name, ch_name_lower, normalized_name, vtv_set, ent_set):
    if is_sports_channel(ch_name_lower):
        return "Thể Thao"
    elif normalized_name in vtv_set:
        return "Kênh VTV"
    elif normalized_name in ent_set:
        return "Giải Trí"
    return None

def sort_key(ch, group):
    name_norm = normalize_channel_name(ch['name'])
    if group == "Kênh VTV":
        for orig in VTV_CHANNELS:
            if normalize_channel_name(orig) == name_norm:
                return VTV_ORDER[orig]
        return len(VTV_CHANNELS)
    elif group == "Giải Trí":
        for orig in ENTERTAINMENT_CHANNELS:
            if normalize_channel_name(orig) == name_norm:
                return ENT_ORDER[orig]
        return len(ENTERTAINMENT_CHANNELS)
    else:
        return ch['name'].lower()

# -------------------- HÀM XỬ LÝ M3U --------------------
def fetch_and_parse_m3u(url):
    try:
        response = requests.get(url, timeout=8)
        content = response.text
        return parse_m3u(content)
    except Exception as e:
        print(f"Lỗi khi xử lý {url}: {str(e)[:30]}")
        return []

def parse_m3u(content):
    channels = []
    current_ch = {}
    extra_lines = []
    lines = content.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXTINF'):
            if current_ch and 'name' in current_ch and 'url' in current_ch:
                if extra_lines:
                    current_ch['extra'] = extra_lines
                channels.append(current_ch)
            current_ch = {}
            extra_lines = []
            params = re.findall(r'([a-zA-Z-]+)="([^"]*)"', line)
            current_ch['params'] = {k.lower(): v for k, v in params}
            name_part = line.split(',', 1)
            if len(name_part) > 1:
                current_ch['name'] = unquote(name_part[1].strip())
            else:
                tvg_name = current_ch['params'].get('tvg-name', 'Unknown Channel')
                current_ch['name'] = unquote(tvg_name)
        elif line.startswith('http') or line.startswith('udp://'):
            if current_ch and 'name' in current_ch:
                current_ch['url'] = line
                if extra_lines:
                    current_ch['extra'] = extra_lines
                channels.append(current_ch)
                current_ch = {}
                extra_lines = []
        elif line.startswith('#'):
            extra_lines.append(line)
    if current_ch and 'name' in current_ch and 'url' in current_ch:
        if extra_lines:
            current_ch['extra'] = extra_lines
        channels.append(current_ch)
    return channels

def process_channel(ch, vtv_set, ent_set, epg_mapping):
    if 'name' not in ch:
        return None
    ch['name'] = clean_channel_name(ch['name'])
    ch_name = ch['name']
    ch_name_lower = ch_name.lower()
    
    if is_movie_excluded(ch_name_lower):
        return None
    
    res_match = re.search(r'(\d{3,4}[pP]|\d+K|HD|SD|FHD|UHD)', ch_name_lower)
    resolution = res_match.group(0).upper() if res_match else ""
    if is_low_resolution(resolution):
        return None
    
    normalized_name = normalize_channel_name(ch_name)
    group = classify_channel(ch_name, ch_name_lower, normalized_name, vtv_set, ent_set)
    if not group:
        return None
    
    ch['group'] = group
    normalized_for_epg = re.sub(r'\W+', '', ch_name_lower)
    ch['tvg-id'] = epg_mapping.get(normalized_for_epg, ch['params'].get('tvg-id', ''))
    ch['resolution'] = resolution
    return ch

def final_check_and_resolve(ch):
    url = ch['url']
    if url.startswith('udp://'):
        return ch
    # Bỏ resolve github để tăng tốc
    if check_channel_health(url):
        return ch
    return None

def get_epg_mapping(epg_url):
    mapping = {}
    try:
        response = requests.get(epg_url, timeout=3)
        if epg_url.endswith('.gz'):
            import gzip
            content = gzip.decompress(response.content)
            root = ET.fromstring(content)
        else:
            root = ET.fromstring(response.content)
        for channel in root.findall('.//channel'):
            tvg_id = channel.get('id')
            display_name = channel.find('display-name')
            if display_name is not None and display_name.text:
                display_name_text = display_name.text.strip()
                normalized = re.sub(r'\W+', '', display_name_text.lower())
                if tvg_id and normalized:
                    mapping[normalized] = tvg_id
    except Exception as e:
        # Bỏ qua lỗi
        pass
    return mapping

def get_m3u_links():
    with open('M3U_list.txt', 'r') as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    return [line for line in lines if not line.startswith('#')]

# -------------------- MAIN --------------------
def main():
    start_time = time.time()
    
    vtv_set = build_normalized_set(VTV_CHANNELS)
    ent_set = build_normalized_set(ENTERTAINMENT_CHANNELS)
    m3u_links = get_m3u_links()
    
    # Tải EPG với worker ít hơn
    epg_mapping = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(get_epg_mapping, url) for url in EPG_SOURCES]
        for future in as_completed(futures):
            epg_mapping.update(future.result())
    
    all_channels = []
    
    # Xử lý link đặc biệt
    try:
        response = requests.get(SPECIAL_URL, timeout=8)
        channels = parse_m3u(response.text)
        for ch in channels:
            if 'name' not in ch:
                continue
            ch_name_lower = ch['name'].lower()
            if 'highlight' in ch_name_lower or 'xem lại' in ch_name_lower:
                continue
            res_match = re.search(r'(\d{3,4}[pP]|\d+K|HD|SD|FHD|UHD)', ch_name_lower)
            resolution = res_match.group(0).upper() if res_match else ""
            if is_low_resolution(resolution):
                continue
            ch['group'] = "Trực tiếp"
            normalized_name = re.sub(r'\W+', '', ch_name_lower)
            ch['tvg-id'] = epg_mapping.get(normalized_name, ch['params'].get('tvg-id', ''))
            ch['resolution'] = resolution
            all_channels.append(ch)
    except Exception as e:
        print(f"Lỗi link đặc biệt: {e}")
    
    # Xử lý các link M3U còn lại
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_and_parse_m3u, url) for url in m3u_links if url != SPECIAL_URL]
        for future in as_completed(futures):
            all_channels.extend(future.result())
    
    # Lọc kênh
    filtered_channels = []
    for ch in all_channels:
        processed = process_channel(ch, vtv_set, ent_set, epg_mapping)
        if processed:
            filtered_channels.append(processed)
    
    # Loại bỏ trùng URL
    print("Đang loại bỏ kênh trùng lặp...")
    unique_urls = set()
    unique_channels = []
    for ch in filtered_channels:
        if ch['url'] not in unique_urls:
            unique_urls.add(ch['url'])
            unique_channels.append(ch)
    
    # GIỚI HẠN SỐ LƯỢNG KÊNH MỖI NHÓM (tránh quá tải)
    MAX_CH_PER_GROUP = 150
    limited_channels = []
    group_count = {}
    for ch in unique_channels:
        grp = ch['group']
        group_count[grp] = group_count.get(grp, 0) + 1
        if group_count[grp] <= MAX_CH_PER_GROUP:
            limited_channels.append(ch)
    
    # Kiểm tra health với worker vừa phải
    print("Đang kiểm tra kênh lỗi...")
    valid_channels = []
    with ThreadPoolExecutor(max_workers=30) as executor:
        future_to_ch = {executor.submit(final_check_and_resolve, ch): ch for ch in limited_channels}
        for future in as_completed(future_to_ch):
            result = future.result()
            if result:
                valid_channels.append(result)
    
    # Đổi tên kênh thể thao
    for ch in valid_channels:
        if ch['group'] == "Thể Thao":
            old_name = ch['name'].strip()
            if old_name in SPORTS_RENAME_MAP:
                ch['name'] = SPORTS_RENAME_MAP[old_name]
    
    # Nhóm và sắp xếp
    grouped = {}
    for ch in valid_channels:
        if ch['group'] not in GROUP_ORDER:
            continue
        grouped.setdefault(ch['group'], []).append(ch)
    
    for group in grouped:
        if group in ("Kênh VTV", "Giải Trí"):
            grouped[group].sort(key=lambda x: sort_key(x, group))
        else:
            grouped[group].sort(key=lambda x: x['name'].lower())
    
    sorted_groups = sorted(grouped.items(), key=lambda x: GROUP_ORDER.get(x[0], 99))
    
    # Ghi file output.m3u
    with open('output.m3u', 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        for group_name, channels in sorted_groups:
            for ch in channels:
                tvg_id = ch.get('tvg-id', '')
                tvg_logo = ch['params'].get('tvg-logo', '')
                resolution = ch.get('resolution', '')
                name_display = f"{ch['name']} - {resolution}" if resolution else ch['name']
                
                extinf = f'#EXTINF:-1 tvg-id="{tvg_id}" group-title="{group_name}"'
                if tvg_logo:
                    extinf += f' tvg-logo="{tvg_logo}"'
                extinf += f',{name_display}'
                f.write(extinf + '\n')
                
                if 'extra' in ch:
                    for extra_line in ch['extra']:
                        if not extra_line.startswith('#EXTINF'):
                            f.write(extra_line + '\n')
                f.write(ch['url'] + '\n')
    
    total_time = time.time() - start_time
    stats = "\n".join([f"{group}: {len(channels)} kênh" for group, channels in sorted_groups])
    print(f"Hoàn thành! Thời gian: {total_time:.2f}s")
    print(f"THỐNG KÊ:\n{stats}")
    print(f"Tổng số kênh hợp lệ: {len(valid_channels)}")
    print(f"Số kênh trùng đã loại: {len(filtered_channels) - len(unique_channels)}")

if __name__ == "__main__":
    main()
