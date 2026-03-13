import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import xml.etree.ElementTree as ET
from urllib.parse import unquote

def get_m3u_links():
    with open('M3U_list.txt', 'r') as f:
        return [line.strip() for line in f.readlines() if line.strip()]

def parse_m3u(content):
    channels = []
    current_ch = {}
    extra_lines = []  # To store EXTVLCOPT and other non-URL lines
    lines = content.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if line.startswith('#EXTINF'):
            # Save previous channel if exists
            if current_ch and 'name' in current_ch and 'url' in current_ch:
                if extra_lines:
                    current_ch['extra'] = extra_lines
                channels.append(current_ch)
            
            # Start new channel
            current_ch = {}
            extra_lines = []
            
            params = re.findall(r'([a-zA-Z-]+)="([^"]*)"', line)
            current_ch['params'] = {k.lower(): v for k, v in params}
            
            # Extract channel name more robustly
            name_part = line.split(',', 1)
            if len(name_part) > 1:
                current_ch['name'] = unquote(name_part[1].strip())
            else:
                # If no name found, try to get it from tvg-name parameter
                tvg_name = current_ch['params'].get('tvg-name', 'Unknown Channel')
                current_ch['name'] = unquote(tvg_name)
        elif line.startswith('http'):
            if current_ch and 'name' in current_ch:
                current_ch['url'] = line
                if extra_lines:
                    current_ch['extra'] = extra_lines
                channels.append(current_ch)
                current_ch = {}
                extra_lines = []
        elif line.startswith('#EXTVLCOPT') or line.startswith('#EXTGRP'):
            # Collect these special lines
            extra_lines.append(line)
    
    # Handle the last channel if any
    if current_ch and 'name' in current_ch and 'url' in current_ch:
        if extra_lines:
            current_ch['extra'] = extra_lines
        channels.append(current_ch)
    
    return channels

def check_channel_health(url, timeout=6):
    try:
        headers = {'User-Agent': 'VLC/3.0.18 LibVLC/3.0.18'}
        response = requests.head(url, headers=headers, timeout=timeout, allow_redirects=True)
        return response.status_code < 400
    except:
        return False

def get_epg_mapping(epg_url):
    mapping = {}
    try:
        response = requests.get(epg_url, timeout=10)
        root = ET.fromstring(response.content)
        
        for channel in root.findall('.//channel'):
            tvg_id = channel.get('id')
            display_name = channel.find('display-name')
            
            if display_name is not None and display_name.text:
                display_name_text = display_name.text.strip()
                # Tạo bản đồ chuẩn hóa
                normalized = re.sub(r'\W+', '', display_name_text.lower())
                if tvg_id and normalized:
                    mapping[normalized] = tvg_id
    except Exception as e:
        print(f"Lỗi khi xử lý EPG {epg_url}: {str(e)}")
    
    return mapping

def should_exclude_channel(ch_name, group):
    ch_name_lower = ch_name.lower()
    
    # Danh sách từ khóa loại trừ cho từng nhóm
    exclude_patterns = {
        "Kênh VTV": ['sfgovtv', 'mvtv'],
        "Phim truyện": ['ajman tv', 'adsiz', 'doku', 'tehlike', 'macer', 'orman', 'ada', 'dönüş', 'ejderha', 'elio', 'brescia', 'dora', 'taş', 'sol dorado', 'the man who', 'bay', 'tüyü', 'pesinde', 'devi', '2 macera', 'adasi', 'ormani', 'dönüs', 'ejderhan', 'eğitirsin', 'gulliver', 'astro', 'christmas', 'icetastrophe', 'astronaut', 'astroworld', 'golf', 'golfinho', 'kaçık', 'gulliverin', 'gulliver', 'o dia', 'pequenos', 'perde', 'untold', 'verônica', 'youre a good sport', 'winter', 'bloomberg', 'pierce', 'eventura', 'entertainment', 'livecam', 'llbn', 'quran', 'music', 'livenow', 'wnbc', 'shop', 'wall street', 'undefined', 'to live', 'eventy', 'happy event', 'serial', 'echo live', 'annie live', 'romanti', 'antenna', 'bloodsport', 'drama', 'clive', 'eplica', 'deliver', 'tale', 'to the moon', 'karanlik', 'event 15', 'movie', 'film', 'fantasy', 'fight to live', 'saldiri', 'fletch', 'fluefa', 'death lives', 'hatayspor', 'ulster', 'korku', 'horror', 'i live', 'pretty', 'replace', 'amelot', 'sporu', 'hikay', 'escape', 'is life', 'kanunu', 'direction', 'spore', 'cheerleading', 'no one', 'passport', 'peepli', 'eplica', 'eplika', 'secret live', 'shaolin', 'sleepless', 'slive', 'livet', 'strange event', 'spetsna', 'taking live', 'burden', 'seventeen', 'victim', 'transfer', 'the main event', 'you live', 'seventh', 'their live', 'transporter', 'fatty live', 'out live', 'can live', 'only live', 'lived', 'news'],
        "Thể Thao": ['cricket', 'nhl', 'rugby', 'doku', 'tehlike', 'macer', 'orman', 'ada', 'dönüş', 'ejderha', 'elio', 'brescia', 'dora', 'taş', 'sol dorado', 'the man who', 'bay', 'tüyü', 'pesinde', 'devi', '2 macera', 'adasi', 'ormani', 'dönüs', 'ejderhan', 'eğitirsin', 'gulliver', 'alive', 'christmas', 'icetastrophe', 'astronaut', 'olive', 'astroworld', 'abc news', 'golfinho', 'kaçık', 'gulliverin', 'gulliver', 'o dia', 'pequenos', 'perde', 'untold', 'verônica', 'youre a good sport', 'winter', 'bloomberg', 'pierce', 'eventura', 'entertainment', 'livecam', 'llbn', 'quran', 'music', 'livenow', 'wnbc', 'shop', 'wall street', 'undefined', 'to live', 'eventy', 'happy event', 'serial', 'echo live', 'annie live', 'romanti', 'antenna', 'bloodsport', 'drama', 'clive', 'eplica', 'deliver', 'tale', 'to the moon', 'karanlık', 'event 15', 'movie', 'film', 'fantasy', 'fight to live', 'doküman', 'fletch', 'fluefa', 'death lives', 'hatayspor', 'ulster', 'korku', 'horror', 'i live', 'pretty', 'replace', 'amelot', 'sporu', 'hikay', 'escape', 'is life', 'kanunu', 'direction', 'spore', 'cheerleading', 'no one', 'passport', 'peepli', 'eplica', 'eplika', 'secret live', 'shaolin', 'sleepless', 'slive', 'livet', 'strange event', 'spetsna', 'taking live', 'burden', 'seventeen', 'victim', 'transfer', 'the main event', 'you live', 'seventh', 'their live', 'transporter', 'fatty live', 'out live', 'can live', 'only live', 'lived', 'news', 'astro boy', 'astro loco', 'çocuk', 'astronot', 'philippine', 'rastro', 'lastro', 'golfe'],
        "Live Events": ['cricket', 'nhl', 'rugby', 'doku', 'tehlike', 'macer', 'orman', 'ada', 'dönüş', 'ejderha', 'elio', 'brescia', 'dora', 'taş', 'sol dorado', 'the man who', 'bay', 'tüyü', 'pesinde', 'devi', '2 macera', 'adasi', 'ormani', 'dönüs', 'ejderhan', 'eğitirsin', 'gulliver', 'alive', 'christmas', 'icetastrophe', 'astronaut', 'olive', 'astroworld', 'abc news', 'golfinho', 'kaçık', 'gulliverin', 'gulliver', 'o dia', 'pequenos', 'perde', 'untold', 'verônica', 'youre a good sport', 'winter', 'bloomberg', 'pierce', 'eventura', 'entertainment', 'livecam', 'llbn', 'quran', 'music', 'livenow', 'wnbc', 'shop', 'wall street', 'undefined', 'to live', 'eventy', 'happy event', 'serial', 'echo live', 'annie live', 'romanti', 'antenna', 'bloodsport', 'drama', 'clive', 'eplica', 'deliver', 'tale', 'to the moon', 'karanlık', 'event 15', 'movie', 'film', 'fantasy', 'fight to live', 'doküman', 'fletch', 'fluefa', 'death lives', 'hatayspor', 'ulster', 'korku', 'horror', 'i live', 'pretty', 'replace', 'amelot', 'sporu', 'hikay', 'escape', 'is life', 'kanunu', 'direction', 'spore', 'cheerleading', 'no one', 'passport', 'peepli', 'eplica', 'eplika', 'secret live', 'shaolin', 'sleepless', 'slive', 'livet', 'strange event', 'spetsna', 'taking live', 'burden', 'seventeen', 'victim', 'transfer', 'the main event', 'you live', 'seventh', 'their live', 'transporter', 'fatty live', 'out live', 'can live', 'only live', 'lived', 'news', 'astro boy', 'astro loco', 'çocuk', 'astronot', 'philippine', 'rastro', 'lastro', 'golfe']
    }
    
    if group in exclude_patterns:
        for pattern in exclude_patterns[group]:
            if pattern in ch_name_lower:
                return True
                
    return False

def is_low_resolution(resolution):
    """Kiểm tra xem độ phân giải có thấp hơn 720p không"""
    if not resolution:
        return False
        
    resolution = resolution.lower()
    
    # Loại bỏ các kênh SD
    if 'sd' in resolution:
        return True
        
    # Loại bỏ các độ phân giải thấp hơn 720p
    low_res_patterns = [
        r'360p', r'480p', r'576p', 
        r'360', r'480', r'576',
        r'low', r'very low'
    ]
    
    for pattern in low_res_patterns:
        if re.search(pattern, resolution):
            return True
            
    # Kiểm tra số: nếu có số nhỏ hơn 720 thì loại bỏ
    numbers = re.findall(r'\d+', resolution)
    for num in numbers:
        if int(num) < 720:
            return True
            
    return False

def is_vtv_channel(ch_name):
    """Kiểm tra xem kênh có phải là VTV theo danh sách cụ thể không"""
    vtv_patterns = [
        r'vtv1', r'vtv2', r'vtv3', r'vtv4', r'vtv5', 
        r'vtv6', r'vtv7', r'vtv8', r'vtv9', r'vtv cần thơ'
    ]
    
    ch_name_lower = ch_name.lower()
    
    for pattern in vtv_patterns:
        if re.search(pattern, ch_name_lower):
            return True
            
    return False

def is_live_event_channel(ch_name_lower):
    """Kiểm tra xem kênh có phải là live events hoặc trận đấu trực tiếp (hỗ trợ nhiều ngôn ngữ)"""
    # Loại trừ nếu có dấu hiệu của phim (năm trong ngoặc hoặc từ khóa phim)
    movie_patterns = [
        r'\(\d{4}\)',  # Năm phim như (2023)
        r'\[\d{2}\.\d{2}\.\d{4}\]',  # [dd.mm.yyyy]
        'movie', 'phim', 'film', 'doku', 'tehlike', 'macer', 'orman', 'ada', 
        'dönüş', 'ejderha', 'elio', 'dora', 'taş', 'sol dorado', 'the man who',
        'bay', 'tüyü', 'pesinde', 'devi', '2 macera', 'adasi', 'ormani', 'dönüs', 
        'ejderhan', 'eğitirsin', 'brescia', 'canli bay', 'tehlike 2', 'macera ormani',
        'gulliver', 'astro', 'christmas', 'icetastrophe', 'astronaut', 'astro', 'astroworld',
        'golfinho', 'kaçık', 'gulliverin', 'gulliver', 'o dia', 'pequenos', 'perde', 'untold',
        'verônica', 'youre a good sport', 'winter', 'replicant', 'replikalar', 'kaamelott',
        'peeples', 'peepli', 'trainwreck', 'beautiful day', 'grande dama', 'amor de cinema', 'winter', 'bloomberg', 'pierce', 'eventura', 'entertainment', 'livecam', 'llbn', 'quran', 'music', 'livenow', 'wnbc', 'shop', 'wall street', 'undefined', 'to live', 'eventy', 'happy event', 'serial', 'echo live', 'annie live', 'romanti', 'antenna', 'bloodsport', 'drama', 'clive', 'eplica', 'deliver', 'tale', 'to the moon', 'karanlik', 'event 15', 'movie', 'film', 'fantasy', 'fight to live', 'saldiri', 'fletch', 'fluefa', 'death lives', 'hatayspor', 'ulster', 'korku', 'horror', 'i live', 'pretty', 'replace', 'amelot', 'sporu', 'hikay', 'escape', 'is life', 'kanunu', 'direction', 'spore', 'cheerleading', 'no one', 'passport', 'peepli', 'eplica', 'eplika', 'secret live', 'shaolin', 'sleepless', 'slive', 'livet', 'strange event', 'spetsna', 'taking live', 'burden', 'seventeen', 'victim', 'transfer', 'the main event', 'you live', 'seventh', 'their live', 'transporter', 'fatty live', 'out live', 'can live', 'only live', 'lived', 'news'
    ]
    for pattern in movie_patterns:
        if re.search(pattern, ch_name_lower):
            return False
    
    live_keywords = [
        # Tiếng Việt
        # Tiếng Anh
        'live', 'event', 'events', 'match', 'matches', 'direct', 'arsenal', 'aston villa', 'bournemouth', 'brentford', 'brighton', 'chelsea', 'crystal palace', 'everton', 'fulham', 'leeds united', 'liverpool', 'manchester city', 'manchester united', 'newcastle', 'nottingham Forest', 'sunderland', 'tottenham hotspur', 'west ham united', 'wolverhampton', 'bayern', 'borussia dortmund', 'bayer leverkusen', 'inter milan', 'ac milan', 'napoli', 'barcelona', 'real madrid', 'atlético', 'psg', 'olympique marseille',
        # Tiếng Thổ Nhĩ Kỳ
        'Maç Yayınları',
        # Tiếng Pháp
        'direct', 'en direct', 'match', 'événement', 'evenement',
        # Tiếng Đức
        'live', 'direkt', 'spiel', 'veranstaltung',
        # Tiếng Tây Ban Nha
        'en vivo', 'directo', 'partido', 'evento',
        # Tiếng Ý
        'diretta', 'partita', 'evento',
        # Các từ khóa khác
    ]
    return any(keyword in ch_name_lower for keyword in live_keywords)

def is_movie_channel(ch_name_lower):
    """Kiểm tra xem kênh có phải là kênh phim truyện dành cho thị trường Việt Nam"""
    # Danh sách các kênh phim cụ thể phổ biến ở Việt Nam
    movie_keywords = [
        'hbo', 'axn', 'box movie', 'hollywood classic', 'cinemax', 
        'cinema', 'fox movies', 'planet earth', 'history', 'discovery', 'garden', 'fishing', 'national geographic',
        'animal planet', 'just for laughs', 'failarmy', 'funny video'  # Thêm các kênh thiên nhiên, lịch sử
    ]
    
    # Chỉ match nếu chứa chính xác một trong các keyword và có thể có 'vn' hoặc không
    return any(keyword in ch_name_lower for keyword in movie_keywords)

def main():
    start_time = time.time()
    
    # 1. Lấy danh sách M3U
    m3u_links = get_m3u_links()
    special_url = "https://raw.githubusercontent.com/t23-02/bongda/refs/heads/main/bongda.m3u"
    
    # 2. Tải EPG mapping
    epg_mapping = {}
    epg_sources = [
        "https://hnlive.dramahay.xyz/epg.xml",
        "https://raw.githubusercontent.com/mrprince/epg/refs/heads/main/epg.xml.gz", "https://bit.ly/a1xepg", "https://raw.githubusercontent.com/karepech/Epgku/main/epg_wib_sports.xml", "https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz"
    ]
    
    for url in epg_sources:
        epg_mapping.update(get_epg_mapping(url))
    
    all_channels = []
    group_order = {
        "Kênh VTV": 1,
        "Phim truyện": 2,
        "Trực tiếp": 3,
        "Live Events": 4,
        "Thể Thao": 5
    }
    
    # 3. Xử lý link đặc biệt trước để ưu tiên group "Trực tiếp" nếu có trùng URL
    try:
        response = requests.get(special_url, timeout=10)
        content = response.text
        channels = parse_m3u(content)
        
        for ch in channels:
            if 'name' not in ch:
                continue
                
            ch_name = ch['name']
            ch_name_lower = ch_name.lower()
            if 'highlight' in ch_name_lower or 'xem lại' in ch_name_lower or 'xem lạ' in ch_name_lower:
                continue
                
            # Phát hiện độ phân giải
            res_match = re.search(r'(\d{3,4}[pP]|\d+K|HD|SD|FHD|UHD)', ch_name_lower)
            resolution = res_match.group(0).upper() if res_match else ""
            
            # Kiểm tra độ phân giải thấp
            if is_low_resolution(resolution):
                continue
            
            ch['group'] = "Trực tiếp"
            
            # Chuẩn hóa tên cho EPG
            normalized_name = re.sub(r'\W+', '', ch_name_lower)
            ch['tvg-id'] = epg_mapping.get(normalized_name, ch['params'].get('tvg-id', ''))
            ch['resolution'] = resolution
            all_channels.append(ch)
            
    except Exception as e:
        print(f"Lỗi khi xử lý link đặc biệt: {str(e)}")
    
    # 4. Xử lý các link thường sau
    for url in m3u_links:
        if url == special_url:
            continue
            
        try:
            response = requests.get(url, timeout=10)
            content = response.text
            channels = parse_m3u(content)
            
            for ch in channels:
                if 'name' not in ch:
                    continue
                    
                ch_name = ch['name']
                ch_name_lower = ch_name.lower()
                
                # Phát hiện độ phân giải
                res_match = re.search(r'(\d{3,4}[pP]|\d+K|HD|SD|FHD|UHD)', ch_name_lower)
                resolution = res_match.group(0).upper() if res_match else ""
                
                # Kiểm tra độ phân giải thấp
                if is_low_resolution(resolution):
                    continue
                
                # Phân loại kênh
                group = None
                if is_vtv_channel(ch['name']):
                    group = "Kênh VTV"
                    if should_exclude_channel(ch['name'], group):
                        continue
                elif is_movie_channel(ch_name_lower):
                    group = "Phim truyện"
                    if should_exclude_channel(ch['name'], group):
                        continue
                elif any(x in ch_name_lower for x in ['thể thao', 'the thao', 'sport', 'bóng đá', 'bong da', 'SPORT', 'dazn', 'SPORTS', 'Thể thao', 'garden', 'spor', 'sport', 'hub premier', 'premier', 'mono max', 'astro', 'spotv', 'epl', 'football', 'soccer', 'tsn', 'la liga','bundesLiga','seria', 'uefa', 'premier league', 'golf', 'tennis', '4k uhd']):
                    if is_live_event_channel(ch_name_lower):
                        group = "Live Events"
                    else:
                        group = "Thể Thao"
                    if should_exclude_channel(ch['name'], group):
                        continue
                
                if not group:
                    continue
                
                ch['group'] = group
                
                # Chuẩn hóa tên cho EPG
                normalized_name = re.sub(r'\W+', '', ch_name_lower)
                ch['tvg-id'] = epg_mapping.get(normalized_name, ch['params'].get('tvg-id', ''))
                ch['resolution'] = resolution
                all_channels.append(ch)
                
        except Exception as e:
            print(f"Lỗi khi xử lý {url}: {str(e)}")
    
    # 5. Loại bỏ các kênh trùng lặp (cùng URL)
    print("Đang loại bỏ kênh trùng lặp...")
    unique_urls = set()
    unique_channels = []
    
    for ch in all_channels:
        if ch['url'] not in unique_urls:
            unique_urls.add(ch['url'])
            unique_channels.append(ch)
    
    # 6. Kiểm tra kênh lỗi
    print("Đang kiểm tra kênh lỗi...")
    valid_channels = []
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_ch = {executor.submit(check_channel_health, ch['url']): ch for ch in unique_channels}
        for future in as_completed(future_to_ch):
            ch = future_to_ch[future]
            if future.result():
                valid_channels.append(ch)
    
    # 7. Sắp xếp và xuất kết quả
    grouped = {}
    for ch in valid_channels:
        group = ch['group']
        if group not in grouped:
            grouped[group] = []
        grouped[group].append(ch)
    
    # Sắp xếp nhóm theo thứ tự yêu cầu
    sorted_groups = sorted(grouped.items(), key=lambda x: group_order.get(x[0], 5))
    
    # Ghi ra file M3U
    with open('output.m3u', 'w', encoding='utf-8') as f:
        f.write('#EXTM3U\n')
        
        for group_name, channels in sorted_groups:
            # Sắp xếp kênh trong nhóm theo tên
            channels.sort(key=lambda x: x['name'])
            
            for ch in channels:
                # Xây dựng thông tin kênh
                tvg_id = ch.get('tvg-id', '')
                tvg_logo = ch['params'].get('tvg-logo', '')
                resolution = ch.get('resolution', '')
                
                # Ghi các dòng extra (EXTVLCOPT, v.v.)
                if 'extra' in ch:
                    # Tạo dòng EXTINF mới với group-title chính xác
                    name = f"{ch['name']} - {resolution}" if resolution else ch['name']
                    extinf_line = f'#EXTINF:-1 tvg-id="{tvg_id}" group-title="{group_name}"'
                    if tvg_logo:
                        extinf_line += f' tvg-logo="{tvg_logo}"'
                    extinf_line += f',{name}'
                    
                    f.write(extinf_line + '\n')
                    
                    # Ghi các dòng extra không phải EXTINF
                    for extra_line in ch['extra']:
                        if not extra_line.startswith('#EXTINF'):
                            f.write(f"{extra_line}\n")
                else:
                    # Nếu không có extra lines, tạo dòng EXTINF
                    name = f"{ch['name']} - {resolution}" if resolution else ch['name']
                    f.write(f'#EXTINF:-1 tvg-id="{tvg_id}" group-title="{group_name}"')
                    if tvg_logo:
                        f.write(f' tvg-logo="{tvg_logo}"')
                    f.write(f',{name}\n')
                
                # Ghi URL
                f.write(f"{ch['url']}\n")
    
    # Thống kê
    total_time = time.time() - start_time
    stats = "\n".join([f"{group}: {len(channels)} kênh" for group, channels in sorted_groups])
    
    print(f"Hoàn thành!\nThời gian xử lý: {total_time:.2f}s\n")
    print(f"THỐNG KÊ KÊNH:\n{stats}")
    print(f"\nTổng số kênh hợp lệ: {len(valid_channels)}")
    print(f"Số kênh trùng lặp đã loại bỏ: {len(all_channels) - len(unique_channels)}")

if __name__ == "__main__":
    main()
