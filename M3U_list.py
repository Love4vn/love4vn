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
    lines = content.split('\n')
    
    for line in lines:
        if line.startswith('#EXTINF'):
            current_ch = {}
            params = re.findall(r'([a-zA-Z-]+)="([^"]*)"', line)
            current_ch['params'] = {k.lower(): v for k, v in params}
            current_ch['name'] = unquote(line.split(',')[-1].strip())
        elif line.startswith('http'):
            current_ch['url'] = line.strip()
            channels.append(current_ch)
            current_ch = {}
    
    return channels

def check_channel_health(url, timeout=3):
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
            display_name = channel.find('display-name').text if channel.find('display-name') is not None else ''
            
            if tvg_id and display_name:
                # Tạo bản đồ chuẩn hóa
                normalized = re.sub(r'\W+', '', display_name.lower())
                mapping[normalized] = tvg_id
    except Exception as e:
        print(f"Lỗi khi xử lý EPG {epg_url}: {str(e)}")
    
    return mapping

def main():
    start_time = time.time()
    
    # 1. Lấy danh sách M3U
    m3u_links = get_m3u_links()
    special_url = "https://raw.githubusercontent.com/t23-02/bongda/refs/heads/main/bongda.m3u"
    
    # 2. Tải EPG mapping
    epg_mapping = {}
    epg_sources = [
        "https://lichphatsong.xyz/schedule/epg.xml",
        "https://7pal.short.gy/alex-epg"
    ]
    
    for url in epg_sources:
        epg_mapping.update(get_epg_mapping(url))
    
    all_channels = []
    group_order = {
        "Kênh VTV": 1,
        "Phim truyện": 2,
        "Trực tiếp": 3,
        "Thể Thao": 4
    }
    
    # 3. Xử lý các link thường
    for url in m3u_links:
        if url == special_url:
            continue
            
        try:
            response = requests.get(url, timeout=10)
            content = response.text
            channels = parse_m3u(content)
            
            for ch in channels:
                ch_name = ch['name'].lower()
                # Phát hiện độ phân giải
                res_match = re.search(r'(\d{3,4}[pP]|\d+K|HD|SD|FHD|UHD)', ch_name)
                resolution = res_match.group(0).upper() if res_match else ""
                
                # Phân loại kênh
                if 'vtv' in ch_name:
                    ch['group'] = "Kênh VTV"
                elif any(x in ch_name for x in ['axn', 'hbo', 'cinema', 'cinemax', 'dreamworks', 
                                              'fashion', 'warner', 'wbtv', 'box movie', 'hollywood',
                                              'in the box', 'history', 'box hits', 'woman', 'man', 
                                              'planet earth']):
                    ch['group'] = "Phim truyện"
                elif any(x in ch_name for x in ['thể thao', 'the thao', 'sport', 'bóng đá', 'bong da']):
                    ch['group'] = "Thể Thao"
                else:
                    continue
                
                # Chuẩn hóa tên cho EPG
                normalized_name = re.sub(r'\W+', '', ch['name'].lower())
                ch['tvg-id'] = epg_mapping.get(normalized_name, ch['params'].get('tvg-id', ''))
                ch['resolution'] = resolution
                all_channels.append(ch)
                
        except Exception as e:
            print(f"Lỗi khi xử lý {url}: {str(e)}")
    
    # 4. Xử lý link đặc biệt
    try:
        response = requests.get(special_url, timeout=10)
        content = response.text
        channels = parse_m3u(content)
        
        for ch in channels:
            ch_name = ch['name'].lower()
            if 'highlight' in ch_name or 'xem lại' in ch_name or 'xem lạ' in ch_name:
                continue
                
            # Phát hiện độ phân giải
            res_match = re.search(r'(\d{3,4}[pP]|\d+K|HD|SD|FHD|UHD)', ch_name)
            resolution = res_match.group(0).upper() if res_match else ""
            
            ch['group'] = "Trực tiếp"
            
            # Chuẩn hóa tên cho EPG
            normalized_name = re.sub(r'\W+', '', ch['name'].lower())
            ch['tvg-id'] = epg_mapping.get(normalized_name, ch['params'].get('tvg-id', ''))
            ch['resolution'] = resolution
            all_channels.append(ch)
            
    except Exception as e:
        print(f"Lỗi khi xử lý link đặc biệt: {str(e)}")
    
    # 5. Kiểm tra kênh lỗi
    print("Đang kiểm tra kênh lỗi...")
    valid_channels = []
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_ch = {executor.submit(check_channel_health, ch['url']): ch for ch in all_channels}
        for future in as_completed(future_to_ch):
            ch = future_to_ch[future]
            if future.result():
                valid_channels.append(ch)
    
    # 6. Sắp xếp và xuất kết quả
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
                
                name = f"{ch['name']} - {resolution}" if resolution else ch['name']
                
                f.write(f"#EXTINF:-1 tvg-id=\"{tvg_id}\" group-title=\"{group_name}\"")
                if tvg_logo:
                    f.write(f" tvg-logo=\"{tvg_logo}\"")
                f.write(f", {name}\n")
                f.write(f"{ch['url']}\n")
    
    # Thống kê
    total_time = time.time() - start_time
    stats = "\n".join([f"{group}: {len(channels)} kênh" for group, channels in sorted_groups])
    
    print(f"Hoàn thành!\nThời gian xử lý: {total_time:.2f}s\n")
    print(f"THỐNG KÊ KÊNH:\n{stats}")
    print(f"\nTổng số kênh hợp lệ: {len(valid_channels)}")

if __name__ == "__main__":
    main()
