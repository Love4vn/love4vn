import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urljoin

# -------------------- CẤU HÌNH --------------------
SPECIAL_URL = "https://raw.githubusercontent.com/t23-02/bongda/refs/heads/main/bongda.m3u"

# ... (các danh sách VTV_CHANNELS, ENTERTAINMENT_CHANNELS, SPORTS_INCLUDE_KEYWORDS,
# SPORTS_EXCLUDE_KEYWORDS, MOVIE_EXCLUDE_KEYWORDS, SPORTS_RENAME_MAP giữ nguyên như code gốc bạn đã có) ...
# Vì dài, tôi không copy lại toàn bộ, bạn giữ nguyên các list đó từ file hiện tại.

# Chỉ thay đổi các hàm và cấu hình bên dưới
# -------------------- CẤU HÌNH --------------------
VTV_ORDER = {name: i for i, name in enumerate(VTV_CHANNELS)}
ENT_ORDER = {name: i for i, name in enumerate(ENTERTAINMENT_CHANNELS)}
GROUP_ORDER = {"Kênh VTV": 1, "Giải Trí": 2, "Thể Thao": 3, "Trực tiếp": 4}

# Giữ lại EPG_SOURCES đầy đủ (hoặc bạn có thể rút gọn, nhưng đã test thì ok)
EPG_SOURCES = [
    "https://hnlive.dramahay.xyz/epg.xml",
    "https://raw.githubusercontent.com/mrprince/epg/refs/heads/main/epg.xml.gz",
    "https://raw.githubusercontent.com/karepech/Epgku/main/epg_wib_sports.xml",
    "https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_CA2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
    "https://raw.githubusercontent.com/bakulwifi/Epglive/refs/heads/main/epg.xml",
    "https://raw.githubusercontent.com/AndKen14/EPG/refs/heads/main/guidePPVb1g.xml",
    "https://raw.githubusercontent.com/AndKen14/EPG/refs/heads/main/guidePPVstrong8k.xml",
    "https://raw.githubusercontent.com/AndKen14/EPG/refs/heads/main/guideusa.xml",
    "https://raw.githubusercontent.com/dbghelp/mewatch-EPG/refs/heads/main/mewatch.xml",
    "https://raw.githubusercontent.com/dbghelp/StarHub-TV-EPG/refs/heads/main/starhub.xml"
]

PLAYLIST_CACHE = {}

# -------------------- CÁC HÀM (giữ nguyên logic cũ, chỉ tối ưu timeout và worker) --------------------
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
    """Chỉ resolve 1 cấp (lấy variant tốt nhất) để tăng tốc"""
    if max_depth <= 0 or url in PLAYLIST_CACHE:
        return PLAYLIST_CACHE.get(url, url)
    if not url.lower().endswith(('.m3u8', '.m3u')):
        return url
    try:
        if session is None:
            session = requests.Session()
        headers = {'User-Agent': 'VLC/3.0.18 LibVLC/3.0.18'}
        resp = session.get(url, headers=headers, timeout=5)
        if resp.status_code != 200:
            return url
        content = resp.text
        if '#EXTM3U' not in content:
            return url
        lines = content.splitlines()
        best_url = None
        best_bandwidth = -1
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('#EXT-X-STREAM-INF'):
                bw_match = re.search(r'BANDWIDTH=(\d+)', line)
                bandwidth = int(bw_match.group(1)) if bw_match else 0
                if i + 1 < len(lines):
                    stream_url = lines[i+1].strip()
                    if stream_url and not stream_url.startswith('#'):
                        full_url = urljoin(url, stream_url)
                        if bandwidth > best_bandwidth:
                            best_bandwidth = bandwidth
                            best_url = full_url
                i += 2
            else:
                i += 1
        if best_url:
            PLAYLIST_CACHE[url] = best_url
            return best_url
        else:
            PLAYLIST_CACHE[url] = url
            return url
    except Exception:
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
    if url.lower().endswith(('.m3u8', '.m3u')):
        resolved = resolve_m3u8_url(url)
        if resolved != url:
            ch['url'] = resolved
    if check_channel_health(ch['url']):
        return ch
    return None

def get_epg_mapping(epg_url):
    mapping = {}
    try:
        response = requests.get(epg_url, timeout=5)
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
    except Exception:
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

    # Tải EPG (giảm worker xuống 5 để không quá tải)
    epg_mapping = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
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

    # Xử lý các link M3U còn lại (worker 10)
    with ThreadPoolExecutor(max_workers=10) as executor:
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

    # Kiểm tra health và resolve playlist (worker 50)
    print("Đang kiểm tra kênh lỗi...")
    valid_channels = []
    with ThreadPoolExecutor(max_workers=50) as executor:
        future_to_ch = {executor.submit(final_check_and_resolve, ch): ch for ch in unique_channels}
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
