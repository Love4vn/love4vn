import sys
import asyncio
import aiohttp
from urllib.parse import urlparse, parse_qs
import json
import platform
import datetime
import re
from collections import defaultdict
import pytz
from typing import List, Tuple, Dict, Set

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class GetChannels:
    def __init__(self, url: str = None, host: str = None, port: int = None, 
                 username: str = None, password: str = None):
        if url:
            self.url = url
            self.host, self.port, self.username, self.password = self.parse_url()
        else:
            self.url = None
            self.host = host
            self.port = port or 80
            self.username = username
            self.password = password

        # Cấu hình múi giờ
        self.timezone_mapping = {
            'UK': 'Europe/London',      # GMT+1 (British Summer Time)
            'BST': 'Europe/London',     # British Summer Time (GMT+1)
            'GMT': 'GMT',               # Greenwich Mean Time
            'ET': 'America/New_York',   # Eastern Time (GMT-4/GMT-5)
            'EST': 'America/New_York',  # Eastern Standard Time (GMT-5)
            'EDT': 'America/New_York',  # Eastern Daylight Time (GMT-4)
            'CT': 'America/Chicago',    # Central Time
            'PT': 'America/Los_Angeles' # Pacific Time
        }
        self.vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
        
        # Danh sách từ khóa lọc
        self.sport_keywords = ["SPORT", "SPORTS", "Thể thao", "LIVE", "Live", "Spor", "Sport", "Matches", "MATCHES"]
        self.banned_keywords = ["Live Cam", "LIVECAM", "Webcam", "CAMERA", "Cam", "CAM", "camera", "cam", "BETTING",  "Betting"]
        self.unwanted_sports = ["CRICKET", "RUGBY", "NHL", "BASEBALL", "UFC", "MMA", "Cric", "CKT", "RUGY", "BASEBALL", "HOCKEY", "NFL", "NRL"]
        self.low_quality_keywords = ["SD", "480p", "360p", "240p", "LQ", "LOW", "LOWQ", "SQ", "STD"]

    def parse_url(self) -> Tuple[str, int, str, str]:
        if not self.url.startswith("http"):
            raise ValueError("URL phải bắt đầu bằng http hoặc https")

        parsed = urlparse(self.url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        
        # Lấy thông tin đăng nhập từ URL hoặc query string
        username = parsed.username
        password = parsed.password
        
        if not username or not password:
            qs = parse_qs(parsed.query)
            username = qs.get("username", [None])[0]
            password = qs.get("password", [None])[0]

        if not username or not password:
            raise ValueError("Không tìm thấy username hoặc password trong URL")

        return host, port, username, password

    async def fetch(self, session: aiohttp.ClientSession, url: str) -> str:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": f"http://{self.host}/"
            }
            async with session.get(url, headers=headers, timeout=20) as response:
                response.raise_for_status()
                return await response.text()
        except aiohttp.ClientError as e:
            print(f"⚠️ Lỗi khi truy cập {url}: {str(e)}")
            return ""
        except asyncio.TimeoutError:
            print(f"⌛ Timeout khi truy cập {url}")
            return ""

    async def get_server_info(self) -> Dict:
        api_url = f"http://{self.host}:{self.port}/player_api.php?username={self.username}&password={self.password}"
        async with aiohttp.ClientSession() as session:
            response = await self.fetch(session, api_url)
            try:
                return json.loads(response) if response else {}
            except json.JSONDecodeError:
                print(f"❌ Không thể parse JSON từ: {api_url}")
                return {}

    def _format_expiry(self, timestamp: int) -> str:
        if not timestamp or timestamp == 0:
            return "Vĩnh viễn"
        try:
            return datetime.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except:
            return "Không xác định"

    async def get_sports_channels(self) -> List[Tuple[str, str, str, str, str]]:
        base_url = f"http://{self.host}:{self.port}"
        sport_channels = []

        async with aiohttp.ClientSession() as session:
            try:
                # Lấy danh mục kênh
                cat_url = f"{base_url}/player_api.php?username={self.username}&password={self.password}&action=get_live_categories"
                cat_resp = await self.fetch(session, cat_url)
                if not cat_resp:
                    print(f"❌ Không nhận được dữ liệu từ {cat_url}")
                    return []
                
                try:
                    categories = json.loads(cat_resp)
                except json.JSONDecodeError:
                    print(f"❌ Dữ liệu không phải JSON từ {cat_url}")
                    return []
                
                cat_map = {c["category_id"]: c["category_name"] for c in categories}

                # Lấy danh sách kênh
                stream_url = f"{base_url}/player_api.php?username={self.username}&password={self.password}&action=get_live_streams"
                stream_resp = await self.fetch(session, stream_url)
                if not stream_resp:
                    print(f"❌ Không nhận được dữ liệu từ {stream_url}")
                    return []
                
                try:
                    streams = json.loads(stream_resp)
                except json.JSONDecodeError:
                    print(f"❌ Dữ liệu không phải JSON từ {stream_url}")
                    return []

                print(f"  Số danh mục: {len(categories)}, Số kênh: {len(streams)}")

                for s in streams:
                    cat_id = s.get("category_id")
                    cat_name = cat_map.get(cat_id, "Unknown")
                    name = s.get("name", "").strip()
                    stream_id = s.get("stream_id", "")
                    logo = s.get("stream_icon", "")

                    if not name or not stream_id:
                        continue

                    # Kiểm tra các điều kiện lọc
                    if self._should_skip_channel(name, cat_name):
                        continue

                    # Xử lý tên kênh (chuyển đổi thời gian)
                    processed_name = self._process_channel_name(name)

                    # Tạo entry cho kênh
                    tvg_id = re.sub(r'\W+', '', processed_name.lower())
                    extinf = f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{processed_name}" tvg-logo="{logo}" group-title="{cat_name}",{processed_name}'
                    stream_url = f"{base_url}/live/{self.username}/{self.password}/{stream_id}.ts"

                    sport_channels.append((extinf, stream_url, cat_name, processed_name, stream_id))

            except Exception as e:
                print(f"❌ Lỗi khi xử lý {self.host}: {str(e)}")

        return sport_channels

    def _should_skip_channel(self, name: str, category: str) -> bool:
        """Kiểm tra xem có nên bỏ qua kênh này không"""
        name_lower = name.lower()
        cat_lower = category.lower()

        # Bỏ qua nếu thuộc danh mục không mong muốn
        if any(kw.lower() in cat_lower for kw in self.banned_keywords):
            return True

        # Bỏ qua nếu là môn thể thao không mong muốn (kiểm tra cả tên và category)
        if (any(sport.lower() in name_lower for sport in self.unwanted_sports) or
            any(sport.lower() in cat_lower for sport in self.unwanted_sports)):
            return True

        # Bỏ qua nếu chất lượng thấp
        if any(kw.lower() in name_lower for kw in self.low_quality_keywords):
            return True

        # Chỉ giữ lại kênh thể thao
        if not any(kw.lower() in cat_lower for kw in self.sport_keywords):
            return True

        return False

    def _process_channel_name(self, name: str) -> str:
        """Xử lý tên kênh, chuyển đổi thời gian sang giờ Việt Nam và loại bỏ múi giờ gốc"""
        # Xử lý định dạng có ngày và thời gian: "07/26 14:45" hoặc "07-26 14:45"
        date_time_match = re.search(
            r'(\d{1,2}[/-]\d{1,2} \d{1,2}:\d{2})', 
            name
        )
        
        if date_time_match:
            try:
                date_time_str = date_time_match.group(1)
                # Chuyển đổi thời gian có ngày
                vn_time = self._convert_date_time(date_time_str)
                # Loại bỏ múi giờ gốc và thay thế bằng (VN)
                return name.replace(date_time_str, f"{vn_time} (VN)")
            except Exception:
                # Không in lỗi để tránh spam console
                pass
        
        # Xử lý định dạng phức tạp: "Sat 26th Jul 2:00AM UK/9:PM ET"
        complex_time_match = re.search(
            r'(\w{3} \d{1,2}(?:st|nd|rd|th)? \w{3} \d{1,2}:\d{2}(?:AM|PM)?) (\w+)/(\d{1,2}:\d{2}(?:AM|PM)?) (\w+)', 
            name
        )
        
        if complex_time_match:
            try:
                uk_time_str = complex_time_match.group(1)
                uk_timezone = complex_time_match.group(2)
                vn_time = self._convert_complex_time(uk_time_str, uk_timezone)
                # Loại bỏ phần múi giờ gốc (UK/ET) và thay bằng (VN)
                return name.replace(complex_time_match.group(0), f"{vn_time} (VN)")
            except Exception:
                # Không in lỗi để tránh spam console
                pass
        
        # Xử lý các định dạng đơn giản có múi giờ: "17:00 UK"
        simple_tz_match = re.search(
            r'(\d{1,2}:\d{2})\s*([A-Z]{2,4})\b', 
            name, 
            re.IGNORECASE
        )
        
        if simple_tz_match:
            try:
                time_str = simple_tz_match.group(1)
                tz_str = simple_tz_match.group(2)
                vn_time = self._convert_simple_timezone(time_str, tz_str)
                # Loại bỏ múi giờ gốc và thay thế bằng (VN)
                return name.replace(simple_tz_match.group(0), f"{vn_time} (VN)")
            except Exception:
                pass
        
        # Xử lý các định dạng thời gian đơn giản (không có múi giờ)
        return re.sub(
            r'\b(\d{1,2}:\d{2})\b', 
            lambda m: self._convert_simple_time(m.group(1)), 
            name
        )

    def _convert_date_time(self, date_time_str: str) -> str:
        """Chuyển đổi thời gian có ngày sang giờ Việt Nam"""
        # Chuẩn hóa định dạng - loại bỏ khoảng trắng thừa
        normalized = re.sub(r'\s+', ' ', date_time_str.strip())
        
        try:
            # Tách phần ngày và giờ
            date_part, time_part = normalized.split(' ')
            
            # Tìm định dạng ngày tháng (có thể là MM/DD hoặc DD/MM)
            if '/' in date_part:
                parts = date_part.split('/')
                if len(parts) != 2:
                    return date_time_str
                    
                # Kiểm tra xem phần nào có thể là tháng (1-12)
                if 1 <= int(parts[0]) <= 12:
                    month = int(parts[0])
                    day = int(parts[1])
                elif 1 <= int(parts[1]) <= 12:
                    month = int(parts[1])
                    day = int(parts[0])
                else:
                    return date_time_str
                    
            elif '-' in date_part:
                parts = date_part.split('-')
                if len(parts) != 2:
                    return date_time_str
                    
                # Kiểm tra xem phần nào có thể là tháng (1-12)
                if 1 <= int(parts[0]) <= 12:
                    month = int(parts[0])
                    day = int(parts[1])
                elif 1 <= int(parts[1]) <= 12:
                    month = int(parts[1])
                    day = int(parts[0])
                else:
                    return date_time_str
            else:
                return date_time_str

            # Xử lý phần giờ
            hour, minute = map(int, time_part.split(':'))
            
            # Sử dụng năm hiện tại
            now = datetime.datetime.now()
            year = now.year
            
            # Tạo đối tượng datetime (giả sử GMT+1)
            dt = datetime.datetime(year, month, day, hour, minute)
            
            # Áp dụng múi giờ GMT+1
            source_tz = pytz.timezone('Europe/London')
            localized_dt = source_tz.localize(dt)
            
            # Chuyển sang giờ Việt Nam
            vn_dt = localized_dt.astimezone(self.vietnam_tz)
            
            # Định dạng kết quả: DD/MM HH:MM
            return vn_dt.strftime("%d/%m %H:%M")
            
        except Exception as e:
            # Nếu có lỗi, trả về chuỗi gốc
            return date_time_str

    def _convert_complex_time(self, time_str: str, timezone: str) -> str:
        """Chuyển đổi thời gian phức tạp sang giờ Việt Nam"""
        # Chuẩn hóa chuỗi thời gian
        time_str = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', time_str)
        time_str = time_str.replace('AM', ' AM').replace('PM', ' PM')
        
        # Ánh xạ múi giờ
        tz_key = timezone.upper()
        tz_name = self.timezone_mapping.get(tz_key, 'Europe/London')
        
        try:
            # Parse thời gian
            dt = datetime.datetime.strptime(time_str, "%a %d %b %I:%M %p")
            
            # Thiết lập năm hiện tại
            now = datetime.datetime.now()
            dt = dt.replace(year=now.year)
            
            # Áp dụng múi giờ
            source_tz = pytz.timezone(tz_name)
            localized_dt = source_tz.localize(dt)
            
            # Chuyển sang giờ Việt Nam
            vn_dt = localized_dt.astimezone(self.vietnam_tz)
            
            return vn_dt.strftime("%H:%M")
        except Exception as e:
            return time_str

    def _convert_simple_timezone(self, time_str: str, timezone: str) -> str:
        """Chuyển đổi thời gian đơn giản có kèm múi giờ sang giờ Việt Nam"""
        tz_key = timezone.upper()
        tz_name = self.timezone_mapping.get(tz_key)
        if not tz_name:
            return time_str  # Không có ánh xạ, trả về thời gian gốc

        try:
            # Lấy ngày giờ hiện tại
            now = datetime.datetime.now()
            # Tách giờ và phút
            hour, minute = map(int, time_str.split(':'))
            # Tạo đối tượng datetime với ngày hôm nay và giờ phút đã cho
            dt = datetime.datetime(now.year, now.month, now.day, hour, minute)
            # Áp dụng múi giờ nguồn
            source_tz = pytz.timezone(tz_name)
            localized_dt = source_tz.localize(dt)
            # Chuyển sang giờ Việt Nam
            vn_dt = localized_dt.astimezone(self.vietnam_tz)
            return vn_dt.strftime("%H:%M")
        except Exception as e:
            return time_str

    def _convert_simple_time(self, time_str: str) -> str:
        """Chuyển đổi thời gian đơn giản sang giờ Việt Nam (+6 tiếng)"""
        try:
            hour, minute = map(int, time_str.split(':'))
            new_hour = (hour + 6) % 24
            return f"{new_hour:02d}:{minute:02d}"
        except:
            return time_str

async def process_server(server_data: Tuple[str, Tuple]) -> Tuple[List[Tuple[str, str, str, str, str]], int]:
    try:
        if server_data[0] == "url":
            fetcher = GetChannels(url=server_data[1])
        else:
            host, port, username, password = server_data[1]
            fetcher = GetChannels(host=host, port=port, username=username, password=password)

        print(f"\n🔍 Đang xử lý server: {fetcher.host}:{fetcher.port}")
        
        # Hiển thị thông tin server
        server_info = await fetcher.get_server_info()
        if server_info:
            user_info = server_info.get("user_info", {})
            print(f"👤 User: {user_info.get('username', '?')}")
            print(f"📶 Active connections: {user_info.get('active_cons', '?')}/{user_info.get('max_connections', '?')}")
            print(f"⏳ Expiry: {fetcher._format_expiry(user_info.get('exp_date'))}")

        # Lấy kênh thể thao
        channels = await fetcher.get_sports_channels()
        print(f"✅ Tìm thấy {len(channels)} kênh thể thao phù hợp")
        return channels, len(channels)

    except Exception as e:
        print(f"❌ Lỗi khi xử lý server: {str(e)}")
        return [], 0

def parse_server_list(lines: List[str]) -> List[Tuple]:
    servers = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        if line.startswith("http"):
            servers.append(("url", line))
        elif "," in line:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 4:
                try:
                    port = int(parts[1])
                    servers.append(("creds", (parts[0], port, parts[2], parts[3])))
                except ValueError:
                    print(f"⚠️ Port không hợp lệ: {line}")
    return servers

def generate_sorted_playlist(channels: List[Tuple]) -> List[str]:
    # Nhóm kênh theo category
    group_channels = defaultdict(list)
    for extinf, stream_url, group, name in channels:
        group_channels[group].append((extinf, stream_url, name))

    # Sắp xếp các nhóm
    def sort_key(group):
        nums = re.findall(r'\d+', group)
        return (0, int(nums[0])) if nums else (1, group.lower())

    sorted_groups = sorted(group_channels.keys(), key=sort_key)

    # Tạo playlist
    playlist = ["#EXTM3U"]
    for group in sorted_groups:
        # Sắp xếp kênh trong nhóm theo tên
        for extinf, stream_url, _ in sorted(group_channels[group], key=lambda x: x[2].lower()):
            playlist.extend([extinf, stream_url])

    return playlist

async def main_async():
    try:
        # Đọc danh sách server từ file
        with open("Xtream_List.txt", "r") as f:
            servers = parse_server_list(f.readlines())
    except FileNotFoundError:
        print("❌ Không tìm thấy file Xtream_List.txt")
        return

    if not servers:
        print("❌ Không có server hợp lệ trong file")
        return

    all_channels = []
    channel_keys = set()
    total_channels = 0

    # Xử lý từng server
    tasks = [process_server(server) for server in servers]
    results = await asyncio.gather(*tasks)

    for channels, count in results:
        total_channels += count
        for channel in channels:
            extinf, stream_url, group, name, stream_id = channel
            channel_key = f"{group}_{name}_{stream_id}"
            if channel_key not in channel_keys:
                channel_keys.add(channel_key)
                all_channels.append((extinf, stream_url, group, name))

    # Sắp xếp kênh
    sorted_playlist = generate_sorted_playlist(all_channels)

    # Lưu file
    output_file = "Xtreamlist_Sports_All.m3u"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted_playlist))

    print(f"\n🎉 Hoàn thành! Đã lưu {len(all_channels)} kênh vào {output_file}")

def main():
    try:
        # Kiểm tra và cài đặt pytz nếu cần
        try:
            import pytz
        except ImportError:
            import subprocess
            subprocess.check_call([sys.executable, "-m", "pip", "install", "pytz"])
            import pytz

        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n🛑 Đã dừng chương trình")
    except Exception as e:
        print(f"❌ Lỗi nghiêm trọng: {str(e)}")

if __name__ == "__main__":
    main()
