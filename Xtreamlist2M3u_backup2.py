import sys
import asyncio
import aiohttp
from urllib.parse import urlparse, parse_qs
import json
import platform
from collections import defaultdict
import re
import hashlib

# Áp dụng chính sách vòng lặp sự kiện cho Windows
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class XtreamChannelOptimizer:
    def __init__(self, provider_name, host, username, password, port=80):
        self.provider_name = provider_name
        self.host = host.replace('http://', '').replace('https://', '').split('/')[0]
        self.username = username
        self.password = password
        self.port = port
        self.base_api_url = f"http://{self.host}:{self.port}/player_api.php"
        self.headers = {
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; 22101320G Build/UKQ1.231003.002)",
            "Accept": "*/*"
        }
        # GIẢM TIMEOUT TỪ 20s XUỐNG 5s
        self.timeout = aiohttp.ClientTimeout(total=5)
        
        # Bộ lọc kênh theo yêu cầu
        self.sports_keywords = [
            "Sport", "Sports", "Football", "Soccer", "Live", "Racing", "Golf", 
            "Bóng đá", "Thể thao", "Tennis", "Basketball", "Đua xe", "Trực tiếp"
        ]
        self.unwanted_sports = [
            "baseball", "rugby", "hockey", "bóng chày", "bóng bầu dục", "khúc côn cầu"
        ]
        self.music_keywords = ["music", "nhạc", "mtv", "muzik", "musik", "song", "sóng", "karaoke"]
        self.sd_keywords = ["SD", "sd", "480p", "576i", "576p", "360p", "low quality"]
        
        # Danh sách các quốc gia/khu vực mong muốn
        self.desired_regions = {
            "europe": ["europe", "euro", "eu", "uk", "germany", "deutsch", "france", "francais", 
                      "italy", "spain", "portugal", "netherlands", "sweden", "norway", "denmark"],
            "north_america": ["usa", "united states", "canada", "mexico", "north america"],
            "asia": ["hong kong", "thailand", "singapore", "malaysia", "indonesia", "vietnam", "china"],
            "middle_east": ["bein", "beinsports", "be in", "qatar", "uae", "dubai"],
            "africa": ["south africa"],
            "australia": ["australia", "australian", "foxtel", "kayo"],
            "new_zealand": ["new zealand", "newzealand", "sky nz"]
        }

    async def _fetch_json(self, session, params):
        try:
            async with session.get(self.base_api_url, params=params, headers=self.headers, timeout=self.timeout) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, json.JSONDecodeError, asyncio.TimeoutError) as e:
            print(f"[{self.provider_name}] ❌ Lỗi khi tìm nạp dữ liệu: {e}")
            return None

    def _is_sports_channel(self, channel_name, category_name):
        """Kiểm tra có phải kênh thể thao hay không"""
        if not channel_name:
            return False
            
        channel_lower = channel_name.lower()
        category_lower = category_name.lower() if category_name else ""
        
        # Loại bỏ kênh không mong muốn
        for unwanted in self.unwanted_sports:
            if unwanted in channel_lower or unwanted in category_lower:
                return False
                
        # Loại bỏ kênh âm nhạc
        for music_kw in self.music_keywords:
            if music_kw in channel_lower or music_kw in category_lower:
                return False
                
        # Kiểm tra kênh thể thao
        for keyword in self.sports_keywords:
            if keyword.lower() in channel_lower or keyword.lower() in category_lower:
                return True
                
        # Kiểm tra sự kiện trực tiếp thể thao
        if "Live" in channel_lower and any(kw in channel_lower for kw in ["Football", "Soccer", "Sport", "SPORT", "Tennis", "TENNIS", "GOLF", "Golf", "Racing", "RACING", "F1", "f1"]):
            return True
            
        return False

    def _is_hd_channel(self, channel_name):
        """Kiểm tra kênh không phải SD"""
        if not channel_name:
            return False
            
        channel_lower = channel_name.lower()
        return not any(sd_kw in channel_lower for sd_kw in self.sd_keywords)

    def _is_desired_region(self, channel_name, category_name):
        """Kiểm tra kênh thuộc khu vực mong muốn"""
        if not channel_name:
            return False
            
        channel_lower = channel_name.lower()
        category_lower = category_name.lower() if category_name else ""
        
        for region, keywords in self.desired_regions.items():
            for keyword in keywords:
                if keyword in channel_lower or keyword in category_lower:
                    return True
                    
        return False

    def _create_epg_id(self, channel_name, stream_id):
        """Tạo EPG ID duy nhất từ tên kênh và stream_id"""
        # Chuẩn hóa tên kênh
        clean_name = re.sub(r'[^a-zA-Z0-9]+', '_', channel_name)
        # Kết hợp với stream_id để tạo ID duy nhất
        return f"{clean_name}_{stream_id}"

    async def test_channel_speed(self, session, stream_url):
        """KIỂM TRA TỐC ĐỘ SỬ DỤNG PHƯƠNG THỨC HEAD - CHỈ KIỂM TRA HEADER"""
        try:
            start_time = asyncio.get_event_loop().time()
            
            # Sử dụng HEAD thay vì GET để chỉ kiểm tra header
            async with session.head(
                stream_url, 
                timeout=self.timeout,
                allow_redirects=True
            ) as response:
                if response.status == 200:
                    return asyncio.get_event_loop().time() - start_time
                return float('inf')
        except:
            return float('inf')

    async def get_optimized_channels(self, session):
        print(f"[{self.provider_name}] ▶️ Đang tối ưu danh sách kênh...")
        auth_params = {"username": self.username, "password": self.password}

        # Lấy danh sách categories
        categories_data = await self._fetch_json(session, {**auth_params, "action": "get_live_categories"})
        if not categories_data:
            print(f"[{self.provider_name}] ⚠️ Không thể lấy danh mục.")
            return []
        
        category_map = {cat['category_id']: cat['category_name'] for cat in categories_data}

        # Lấy danh sách kênh
        streams_data = await self._fetch_json(session, {**auth_params, "action": "get_live_streams"})
        if not streams_data:
            print(f"[{self.provider_name}] ⚠️ Không thể lấy các luồng.")
            return []

        # Lọc kênh theo yêu cầu
        filtered_streams = []
        for stream in streams_data:
            channel_name = stream.get("name", "").strip()
            if not channel_name:
                continue
                
            category_id = stream.get("category_id")
            category_name = category_map.get(category_id, "")
            stream_id = stream.get("stream_id")
            
            if not stream_id:
                continue
            
            # Tạo EPG ID hợp lệ
            epg_id = stream.get("epg_channel_id")
            if not epg_id or epg_id == "None":
                epg_id = self._create_epg_id(channel_name, stream_id)
            
            # Áp dụng tất cả bộ lọc
            if (self._is_sports_channel(channel_name, category_name) 
                and self._is_hd_channel(channel_name)
                and self._is_desired_region(channel_name, category_name)):
                
                stream_url = f"http://{self.host}:{self.port}/live/{self.username}/{self.password}/{stream_id}.ts"
                filtered_streams.append({
                    "name": channel_name,
                    "url": stream_url,
                    "logo": stream.get("stream_icon", ""),
                    "epg_id": epg_id,
                    "category": category_name,
                    "stream_id": stream_id
                })

        print(f"[{self.provider_name}] 🔍 Đã lọc được {len(filtered_streams)} kênh phù hợp")

        # Tạo từ điển để phát hiện trùng lặp
        unique_channels = {}
        duplicate_count = 0
        
        for channel in filtered_streams:
            # Tạo khóa duy nhất dựa trên tên kênh và thể loại
            channel_key = f"{channel['name']}_{channel['category']}"
            
            # Nếu kênh chưa tồn tại hoặc có EPG ID tốt hơn
            if channel_key not in unique_channels:
                unique_channels[channel_key] = channel
            else:
                duplicate_count += 1
                # Ưu tiên kênh có EPG ID không phải là "None"
                existing = unique_channels[channel_key]
                if "None" in existing["epg_id"] and "None" not in channel["epg_id"]:
                    unique_channels[channel_key] = channel

        print(f"[{self.provider_name}] 🔄 Đã loại bỏ {duplicate_count} kênh trùng lặp")
        
        # Chuyển thành danh sách
        unique_streams = list(unique_channels.values())
        
        # Kiểm tra tốc độ đồng thời (CONCURRENCY)
        optimized_channels = []
        concurrency_limit = 20  # Số kết nối đồng thời tối đa
        
        # Tạo session con để kiểm tra kênh với giới hạn kết nối
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit_per_host=concurrency_limit)
        ) as test_session:
            tasks = []
            for channel in unique_streams:
                tasks.append(self.test_channel_speed(test_session, channel["url"]))
            
            # Chạy đồng thời tất cả task
            latencies = await asyncio.gather(*tasks)
            
            # Gán kết quả vào các kênh
            for i, channel in enumerate(unique_streams):
                channel["latency"] = latencies[i]
                optimized_channels.append(channel)
                print(f"\r[{self.provider_name}] 🚀 Đang kiểm tra tốc độ {i+1}/{len(unique_streams)}: {channel['name'][:50]}...", end="", flush=True)

        # Sắp xếp theo độ trễ
        optimized_channels.sort(key=lambda x: x["latency"])
        print(f"\n[{self.provider_name}] ✅ Đã tối ưu hóa {len(optimized_channels)} kênh")
        return optimized_channels

def parse_provider_line(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    try:
        if line.startswith('http'):
            parsed = urlparse(line)
            host = parsed.hostname
            port = parsed.port or 80
            qs = parse_qs(parsed.query)
            username = qs.get('username', [None])[0]
            password = qs.get('password', [None])[0]
            provider_name = host
            if not all([host, port, username, password]): return None
            return (provider_name, host, username, password, port)
        elif ',' in line:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) == 4:
                host, port_str, username, password = parts
                host = host.replace('http://', '').replace('https://', '')
                provider_name = host
                return (provider_name, host, username, password, int(port_str))
    except Exception as e:
        print(f"❌ Lỗi khi phân tích cú pháp dòng: {line} - {e}")
        return None
    return None

async def main():
    # Đọc danh sách provider từ file
    try:
        with open("Xtream_List.txt", "r", encoding="utf-8") as f:
            provider_details = []
            for line in f:
                parsed_data = parse_provider_line(line)
                if parsed_data:
                    provider_details.append(parsed_data)
    except FileNotFoundError:
        print("❌ Lỗi: Không tìm thấy tệp 'Xtream_List.txt'")
        return

    if not provider_details:
        print("⚠️ Tệp 'Xtream_List.txt' trống hoặc không chứa thông tin hợp lệ")
        return

    # Tạo session HTTP dùng chung
    connector = aiohttp.TCPConnector(limit_per_host=5)  # Giới hạn kết nối chung
    session = aiohttp.ClientSession(connector=connector)

    all_channels = []
    
    try:
        # Lấy danh sách kênh từ tất cả provider
        tasks = []
        for name, host, user, pw, port in provider_details:
            optimizer = XtreamChannelOptimizer(name, host, user, pw, port)
            tasks.append(optimizer.get_optimized_channels(session))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Xử lý kết quả
        for result in results:
            if isinstance(result, Exception):
                print(f"\n⚠️ Có lỗi xảy ra: {str(result)}")
            elif isinstance(result, list):
                all_channels.extend(result)

        if not all_channels:
            print("\n❌ Không lấy được kênh nào từ các provider")
            return

        # Sắp xếp kênh theo thể loại và tên
        all_channels.sort(key=lambda x: (x["category"], x["name"]))

        # Tạo file M3U
        m3u_content = "#EXTM3U\n"
        for channel in all_channels:
            m3u_content += (
                f'#EXTINF:-1 tvg-id="{channel["epg_id"]}" tvg-name="{channel["name"]}" '
                f'tvg-logo="{channel["logo"]}" group-title="{channel["category"]}",{channel["name"]}\n'
                f'{channel["url"]}\n'
            )

        # Lưu file
        with open("Sports_Playlist_Optimized.m3u", "w", encoding="utf-8") as f:
            f.write(m3u_content)

        print(f"\n🎉 Hoàn tất! Đã lưu {len(all_channels)} kênh vào Sports_Playlist_Optimized.m3u")
        print("✔ Chỉ lấy kênh thể thao và sự kiện thể thao trực tiếp")
        print("✔ Lọc theo quốc gia/khu vực mong muốn")
        print("✔ Loại bỏ kênh trùng lặp")
        print("✔ Chỉ giữ lại kênh có tốc độ truy cập nhanh nhất")
        print("✔ Sắp xếp theo thể loại và tên kênh")
        print("✔ Tạo EPG ID hợp lệ cho tất cả kênh")

    except Exception as e:
        print(f"\n⚠️ Lỗi nghiêm trọng trong chương trình chính: {str(e)}")
    finally:
        await session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nĐã dừng bởi người dùng")
        sys.exit(0)
