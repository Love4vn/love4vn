import sys
import asyncio
import aiohttp
from urllib.parse import urlparse, parse_qs
import json
import platform
import re
from collections import defaultdict
import datetime
import pytz
from typing import List, Tuple, Dict, Set, Optional, Callable, Match
from datetime import timedelta

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
            'UK': 'Europe/London',
            'BST': 'Europe/London',
            'GMT': 'GMT',
            'ET': 'America/New_York',
            'EST': 'America/New_York',
            'EDT': 'America/New_York',
            'CT': 'America/Chicago',
            'PT': 'America/Los_Angeles'
        }
        self.vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
        self.weekday_map = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
        
        # Danh sách từ khóa lọc
        self.sport_keywords = ["SPORT", "SPORTS", "Thể thao", "LIVE", "Live", "Spor", "Sport", "Matches", "MATCHES", "Direct", "DIRECT", "Event", "EVENT", "Events", "EVENTS", "Hub Premier", "EPL", "Football", "La Liga", "UEFA", "Premier League", "Golf", "Tennis", "Viaplay", "Serie A", "Bundesliga"]
        self.banned_keywords = ["Live Cam", "LIVECAM", "Webcam", "CAMERA", "Cam", "CAM", "camera", "cam", "BETTING",  "Betting"]
        self.unwanted_sports = ["CRICKET", "RUGBY", "NHL", "BASEBALL", "UFC", "MMA", "Cric", "CKT", "RUGY", "BASEBALL", "HOCKEY", "NFL", "NRL", "AHL", "ARL", "Events/PPV", "Matchroom", "STAN EVENT", "Speedway", "Clubber", "FITETV", "SKWEEK", "GAAGO", "EFL", "MLS Pass", "National League", "PDC", "ARL", "SPFL", "Wrestling", "WSL", "WWE", "MOLA", "NCAAB", "NCAAF", "OHL", "JHL", "STAN", "Supercross", "WHL", "WNBA", "IN/PK", "REPLAY", "Flo", "Dirtvision", "Ligue 1 Pass", "Malaysia", "Saudi Arabia", "STARZPLAY", "Ultimate Pool", "FANDUEL", "NIFL", "XFL", "Box Office", "Backup", "Itauma"]
        self.low_quality_keywords = ["SD", "480p", "360p", "240p", "LQ", "LOW", "LOWQ", "SQ", "STD"]
        
        # Từ khóa nhận diện độ phân giải cao
        self.hd_keywords = ["4K", "UHD", "4K UHD", "UHD 4K", "ULTRA HD", "ULTRAHD", "ULTRA-HD"]

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

    def _is_hd_sport_channel(self, name: str, category: str) -> bool:
        """Kiểm tra xem kênh có phải là độ phân giải cao VÀ liên quan đến thể thao không"""
        name_upper = name.upper()
        category_upper = category.upper()
        
        # Kiểm tra độ phân giải cao
        is_hd = any(keyword in name_upper for keyword in self.hd_keywords)
        
        # Kiểm tra liên quan đến thể thao
        is_sport = (any(keyword in name_upper for keyword in self.sport_keywords) or
                   any(keyword in category_upper for keyword in self.sport_keywords))
        
        return is_hd and is_sport

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
                    print(f"❌ Dữliệu không phải JSON từ {stream_url}")
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

                    # Kiểm tra xem có phải là kênh độ phân giải cao và thể thao không
                    is_hd_sport = self._is_hd_sport_channel(name, cat_name)
                    
                    # Nếu là kênh HD thể thao, đổi nhóm thành "4K UHD SPORT"
                    if is_hd_sport:
                        cat_name = "4K UHD SPORT"
                    else:
                        # Nếu không phải kênh HD thể thao, kiểm tra các điều kiện lọc thông thường
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
        """Xử lý tên kênh, chuyển đổi thời gian sang giờ Việt Nam"""
        # Danh sách các processor theo thứ tự ưu tiên
        processors = [
            self._process_full_datetime_format,
            self._process_weekday_ordinal_format,
            self._process_weekday_date_format,
            self._process_date_slash_format,
            self._process_date_dot_format,
            self._process_weekday_time_format,
            self._process_ampm_time_format,  # Xử lý định dạng AM/PM
            self._process_simple_time_with_tz,
            self._process_simple_time
        ]
        
        # Áp dụng lần lượt các processor
        for processor in processors:
            name, changed = processor(name)
            if changed:
                break
            
        return name

    def _process_full_datetime_format(self, name: str) -> Tuple[str, bool]:
        """Xử lý định dạng đầy đủ: Saturday. 02 August 2025 19:30"""
        pattern = r'(\b\w+\.?\s+)(\d{1,2})\s+(\w+)\s+(\d{4})\s+(\d{1,2}:\d{2}\b)'
        changed = False
        
        def replace(match: Match) -> str:
            nonlocal changed
            changed = True
            prefix = match.group(1)
            day = int(match.group(2))
            month = match.group(3)
            year = int(match.group(4))
            time_str = match.group(5)
            
            # Tạo datetime object
            try:
                dt = datetime.datetime.strptime(f"{day} {month} {year} {time_str}", "%d %B %Y %H:%M")
                uk_dt = pytz.timezone('Europe/London').localize(dt)
                vn_dt = uk_dt.astimezone(self.vietnam_tz)
                
                # Format lại kết quả
                new_prefix = vn_dt.strftime("%a. ")
                new_day = vn_dt.day
                new_month = vn_dt.strftime("%B")
                new_year = vn_dt.year
                new_time = vn_dt.strftime("%H:%M")
                
                return f"{new_prefix}{new_day} {new_month} {new_year} {new_time} (VN)"
            except:
                return match.group(0)
        
        result = re.sub(pattern, replace, name, flags=re.IGNORECASE)
        return result, changed

    def _process_weekday_ordinal_format(self, name: str) -> Tuple[str, bool]:
        """Xử lý định dạng: Sat 2nd Aug 19:30 UK"""
        pattern = r'(\b\w{3})\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\w{3})\s+(\d{1,2}:\d{2})\s*([A-Z]{2,4})?'
        changed = False
        
        def replace(match: Match) -> str:
            nonlocal changed
            changed = True
            weekday = match.group(1)
            day = int(match.group(2))
            month = match.group(3)
            time_str = match.group(4)
            tz = match.group(5) or "UK"
            
            # Xác định múi giờ
            tz_key = tz.upper()
            tz_name = self.timezone_mapping.get(tz_key, 'Europe/London')
            
            try:
                # Tạo datetime object
                now = datetime.datetime.now()
                dt = datetime.datetime(now.year, self._month_to_number(month), day, 
                                      *map(int, time_str.split(':')))
                source_dt = pytz.timezone(tz_name).localize(dt)
                vn_dt = source_dt.astimezone(self.vietnam_tz)
                
                # Format lại kết quả
                new_weekday = vn_dt.strftime("%a")
                new_day = vn_dt.day
                new_month = vn_dt.strftime("%b")
                new_time = vn_dt.strftime("%H:%M")
                
                # Xử lý hậu tố ordinal
                suffix = "th"
                if 11 <= new_day <= 13:
                    suffix = "th"
                else:
                    suffix = {1: "st", 2: "nd", 3: "rd"}.get(new_day % 10, "th")
                
                return f"{new_weekday} {new_day}{suffix} {new_month} {new_time} (VN)"
            except:
                return match.group(0)
        
        result = re.sub(pattern, replace, name, flags=re.IGNORECASE)
        return result, changed

    def _process_weekday_date_format(self, name: str) -> Tuple[str, bool]:
        """Xử lý định dạng: Tue,05 Aug 17:05"""
        pattern = r'(\b\w{3}),\s*(\d{1,2})\s+(\w{3})\s+(\d{1,2}:\d{2})'
        changed = False
        
        def replace(match: Match) -> str:
            nonlocal changed
            changed = True
            weekday = match.group(1)
            day = int(match.group(2))
            month = match.group(3)
            time_str = match.group(4)
            
            try:
                # Tạo datetime object
                now = datetime.datetime.now()
                dt = datetime.datetime(now.year, self._month_to_number(month), day, 
                                      *map(int, time_str.split(':')))
                uk_dt = pytz.timezone('Europe/London').localize(dt)
                vn_dt = uk_dt.astimezone(self.vietnam_tz)
                
                # Format lại kết quả
                new_weekday = vn_dt.strftime("%a")
                new_day = vn_dt.day
                new_month = vn_dt.strftime("%b")
                new_time = vn_dt.strftime("%H:%M")
                
                return f"{new_weekday},{new_day:02d} {new_month} {new_time} (VN)"
            except:
                return match.group(0)
        
        result = re.sub(pattern, replace, name, flags=re.IGNORECASE)
        return result, changed

    def _process_date_slash_format(self, name: str) -> Tuple[str, bool]:
        """Xử lý định dạng: 29/12 16:00 UK"""
        pattern = r'(\d{1,2})/(\d{1,2})\s+(\d{1,2}:\d{2})\s*([A-Z]{2,4})?'
        changed = False
        
        def replace(match: Match) -> str:
            nonlocal changed
            changed = True
            day = int(match.group(1))
            month = int(match.group(2))
            time_str = match.group(3)
            tz = match.group(4) or "UK"
            
            # Xác định múi giờ
            tz_key = tz.upper()
            tz_name = self.timezone_mapping.get(tz_key, 'Europe/London')
            
            try:
                # Tạo datetime object
                now = datetime.datetime.now()
                dt = datetime.datetime(now.year, month, day, 
                                      *map(int, time_str.split(':')))
                source_dt = pytz.timezone(tz_name).localize(dt)
                vn_dt = source_dt.astimezone(self.vietnam_tz)
                
                # Format lại kết quả
                new_day = vn_dt.day
                new_month = vn_dt.month
                new_time = vn_dt.strftime("%H:%M")
                
                return f"{new_day}/{new_month} {new_time} (VN)"
            except:
                return match.group(0)
        
        result = re.sub(pattern, replace, name, flags=re.IGNORECASE)
        return result, changed

    def _process_date_dot_format(self, name: str) -> Tuple[str, bool]:
        """Xử lý định dạng: 16.08. 18:30"""
        pattern = r'(\d{1,2})\.(\d{1,2})\.\s+(\d{1,2}:\d{2})'
        changed = False
        
        def replace(match: Match) -> str:
            nonlocal changed
            changed = True
            day = int(match.group(1))
            month = int(match.group(2))
            time_str = match.group(3)
            
            try:
                # Tạo datetime object
                now = datetime.datetime.now()
                dt = datetime.datetime(now.year, month, day, 
                                      *map(int, time_str.split(':')))
                uk_dt = pytz.timezone('Europe/London').localize(dt)
                vn_dt = uk_dt.astimezone(self.vietnam_tz)
                
                # Format lại kết quả
                new_day = vn_dt.day
                new_month = vn_dt.month
                new_time = vn_dt.strftime("%H:%M")
                
                return f"{new_day}.{new_month:02d}. {new_time} (VN)"
            except:
                return match.group(0)
        
        result = re.sub(pattern, replace, name, flags=re.IGNORECASE)
        return result, changed

    def _process_weekday_time_format(self, name: str) -> Tuple[str, bool]:
        """Xử lý định dạng: Sun 16:00 UK"""
        pattern = r'(\b\w{3})\s+(\d{1,2}:\d{2})\s*([A-Z]{2,4})?'
        changed = False
        
        def replace(match: Match) -> str:
            nonlocal changed
            changed = True
            weekday_str = match.group(1)
            time_str = match.group(2)
            tz = match.group(3) or "UK"
            
            # Xác định múi giờ
            tz_key = tz.upper()
            tz_name = self.timezone_mapping.get(tz_key, 'Europe/London')
            
            try:
                # Ánh xạ thứ
                target_weekday = self.weekday_map.get(weekday_str[:3].title())
                if target_weekday is None:
                    return match.group(0)

                tz_info = pytz.timezone(tz_name)
                now_in_tz = datetime.datetime.now(tz_info)

                # Tính toán số ngày cần thêm để đến thứ target_weekday
                current_weekday = now_in_tz.weekday()
                days_ahead = (target_weekday - current_weekday) % 7

                # Tạo candidate_date
                hour, minute = map(int, time_str.split(':'))
                candidate_date = now_in_tz + timedelta(days=days_ahead)
                candidate_date = candidate_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

                # Nếu đã qua thời gian này thì cộng thêm 1 tuần
                if candidate_date < now_in_tz:
                    candidate_date += timedelta(days=7)

                # Chuyển sang giờ Việt Nam
                vn_time = candidate_date.astimezone(self.vietnam_tz)

                # Format kết quả - cập nhật cả thứ nếu đã thay đổi
                new_weekday = vn_time.strftime("%a")
                new_time = vn_time.strftime("%H:%M")
                return f"{new_weekday} {new_time} (VN)"
            except:
                return match.group(0)
        
        result = re.sub(pattern, replace, name, flags=re.IGNORECASE)
        return result, changed

    def _process_ampm_time_format(self, name: str) -> Tuple[str, bool]:
        """Xử lý định dạng thời gian có AM/PM: 7:30pm UK"""
        pattern = r'(\d{1,2}:\d{2})\s*([ap]m)\s*([A-Z]{2,4})?'
        changed = False
        
        def replace(match: Match) -> str:
            nonlocal changed
            changed = True
            time_str = match.group(1)
            am_pm = match.group(2).lower()
            tz = match.group(3) or "UK"
            
            # Chuyển đổi sang định dạng 24h
            try:
                hour, minute = map(int, time_str.split(':'))
                if am_pm == 'pm' and hour < 12:
                    hour += 12
                elif am_pm == 'am' and hour == 12:
                    hour = 0
                time_24h = f"{hour:02d}:{minute:02d}"
            except:
                return match.group(0)
            
            # Xác định múi giờ
            tz_key = tz.upper()
            tz_name = self.timezone_mapping.get(tz_key, 'Europe/London')
            
            try:
                tz_info = pytz.timezone(tz_name)
                # Tạo thời điểm từ giờ hiện tại
                now = datetime.datetime.now(tz_info)
                
                # Tạo candidate: ngày hôm nay với giờ đã chuyển đổi
                candidate = tz_info.localize(
                    datetime.datetime(now.year, now.month, now.day, hour, minute)
                )
                
                # Nếu đã qua thời gian này thì dùng ngày mai
                if candidate < now:
                    candidate += timedelta(days=1)
                
                # Chuyển sang giờ Việt Nam
                vn_time = candidate.astimezone(self.vietnam_tz)
                
                # Format lại kết quả
                new_time = vn_time.strftime("%H:%M")
                return f"{new_time} (VN)"
            except:
                return match.group(0)
        
        result = re.sub(pattern, replace, name, flags=re.IGNORECASE)
        return result, changed

    def _process_simple_time_with_tz(self, name: str) -> Tuple[str, bool]:
        """Xử lý định dạng đơn giản có múi giờ: 19:30 UK"""
        pattern = r'(\d{1,2}:\d{2})\s*([A-Z]{2,4})\b'
        changed = False
        
        def replace(match: Match) -> str:
            nonlocal changed
            changed = True
            time_str = match.group(1)
            tz = match.group(2)
            
            # Xác định múi giờ
            tz_key = tz.upper()
            tz_name = self.timezone_mapping.get(tz_key, 'Europe/London')
            
            try:
                tz_info = pytz.timezone(tz_name)
                # Tạo thời điểm từ giờ hiện tại
                hour, minute = map(int, time_str.split(':'))
                now = datetime.datetime.now(tz_info)
                
                # Tạo candidate: ngày hôm nay với giờ đã cho
                candidate = tz_info.localize(
                    datetime.datetime(now.year, now.month, now.day, hour, minute)
                )
                
                # Nếu đã qua thời gian này thì dùng ngày mai
                if candidate < now:
                    candidate += timedelta(days=1)
                
                # Chuyển sang giờ Việt Nam
                vn_time = candidate.astimezone(self.vietnam_tz)
                
                # Format lại kết quả
                new_time = vn_time.strftime("%H:%M")
                return f"{new_time} (VN)"
            except:
                return match.group(0)
        
        result = re.sub(pattern, replace, name, flags=re.IGNORECASE)
        return result, changed

    def _process_simple_time(self, name: str) -> Tuple[str, bool]:
        """Xử lý định dạng giờ đơn giản: 18:00"""
        pattern = r'\b(\d{1,2}:\d{2})\b'
        changed = False
        
        def replace(match: Match) -> str:
            nonlocal changed
            changed = True
            time_str = match.group(1)
            try:
                hour, minute = map(int, time_str.split(':'))
                new_hour = (hour + 6) % 24
                return f"{new_hour:02d}:{minute:02d}"
            except:
                return time_str
        
        result = re.sub(pattern, replace, name, flags=re.IGNORECASE)
        return result, changed

    def _month_to_number(self, month_str: str) -> int:
        """Chuyển đổi tên tháng (viết tắt) thành số"""
        months = {
            "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
            "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
            "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
        }
        return months.get(month_str[:3].title(), 1)

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
    for extinf, stream_url, group, name, _ in channels:
        group_channels[group].append((extinf, stream_url, name))

    # Tách riêng nhóm "4K UHD SPORT" nếu có
    fourk_group = "4K UHD SPORT"
    fourk_channels = group_channels.pop(fourk_group, [])
    
    # Từ khóa xác định group sự kiện trực tiếp
    live_keywords = ["LIVE", "EPL", "TRỰC TIẾP", "EVENT", "MATCH", "GAME", "DIRECT", "UK", "NL"]
    
    # Phân loại group: sự kiện trực tiếp và thông thường
    live_groups = []
    other_groups = []
    
    for group in group_channels:
        group_upper = group.upper()
        if any(kw in group_upper for kw in live_keywords):
            live_groups.append(group)
        else:
            other_groups.append(group)
    
    # Hàm sắp xếp group: ưu tiên số rồi đến chữ
    def sort_group(groups):
        with_numbers = []
        without_numbers = []
        
        for group in groups:
            numbers = re.findall(r'\d+', group)
            if numbers:
                with_numbers.append((int(numbers[0]), group))
            else:
                without_numbers.append(group)
        
        with_numbers.sort(key=lambda x: x[0])
        without_numbers.sort(key=str.lower)
        
        return [item[1] for item in with_numbers] + without_numbers
    
    # Sắp xếp từng loại group
    sorted_live = sort_group(live_groups)
    sorted_other = sort_group(other_groups)
    
    # Kết hợp: nhóm 4K UHD SPORT lên đầu tiên, sau đó là group sự kiện trực tiếp, cuối cùng là các group khác
    sorted_groups = []
    if fourk_channels:
        sorted_groups.append(fourk_group)
        group_channels[fourk_group] = fourk_channels
    
    sorted_groups.extend(sorted_live)
    sorted_groups.extend(sorted_other)

    # Tạo playlist
    playlist = ["#EXTM3U"]
    for group in sorted_groups:
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
                all_channels.append((extinf, stream_url, group, name, stream_id))

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
