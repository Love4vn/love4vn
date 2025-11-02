import sys
import asyncio
import aiohttp
from urllib.parse import urlparse, parse_qs
import json
import platform

# Áp dụng chính sách vòng lặp sự kiện cho Windows
if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

class XtreamChannelFetcher:
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
        self.sports_keywords = ["Sport", "Sports", "Football", "Soccer", "Live", "Racing", "Golf", "VTV", "K+"]

    async def _fetch_json(self, session, params):
        try:
            async with session.get(self.base_api_url, params=params, headers=self.headers, timeout=15) as response:
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, json.JSONDecodeError, asyncio.TimeoutError) as e:
            print(f"[{self.provider_name}] ❌ Lỗi khi tìm nạp dữ liệu: {e}")
            return None

    def _is_sports_channel(self, channel_name, category_name):
        for keyword in self.sports_keywords:
            if keyword.lower() in channel_name.lower() or (category_name and keyword.lower() in category_name.lower()):
                return True
        return False

    async def get_sports_channels(self, session):
        print(f"[{self.provider_name}] ▶️ Đang tìm nạp danh sách kênh...")
        auth_params = {"username": self.username, "password": self.password}

        user_info_data = await self._fetch_json(session, auth_params)
        epg_url = None
        if user_info_data and 'server_info' in user_info_data and user_info_data.get('server_info'):
            epg_url = user_info_data['server_info'].get('url')

        categories_data = await self._fetch_json(session, {**auth_params, "action": "get_live_categories"})
        if not categories_data:
            print(f"[{self.provider_name}] ⚠️ Không thể lấy danh mục.")
            return [], None
        
        category_map = {cat['category_id']: cat['category_name'] for cat in categories_data}

        streams_data = await self._fetch_json(session, {**auth_params, "action": "get_live_streams"})
        if not streams_data:
            print(f"[{self.provider_name}] ⚠️ Không thể lấy các luồng.")
            return [], None

        m3u_entries = []
        for stream in streams_data:
            channel_name = stream.get("name", "")
            category_id = stream.get("category_id")
            category_name = category_map.get(category_id)

            if self._is_sports_channel(channel_name, category_name):
                stream_id = stream.get("stream_id")
                logo_url = stream.get("stream_icon", "")
                epg_id = stream.get("epg_channel_id") or channel_name
                
                # <--- THAY ĐỔI CHÍNH: Sử dụng category_name làm group-title.
                # Nếu category_name trống, dùng provider_name làm nhóm dự phòng.
                group_title = category_name or self.provider_name

                if stream_id and channel_name:
                    stream_url = f"http://{self.host}:{self.port}/live/{self.username}/{self.password}/{stream_id}.ts"
                    m3u_entry = (
                        f'#EXTINF:-1 tvg-id="{epg_id}" tvg-name="{channel_name}" tvg-logo="{logo_url}" group-title="{group_title}",{channel_name}\n'
                        f'{stream_url}'
                    )
                    m3u_entries.append(m3u_entry)

        print(f"[{self.provider_name}] ✅ Đã tìm thấy {len(m3u_entries)} kênh thể thao.")
        return m3u_entries, epg_url

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
    provider_details = []
    try:
        with open("Xtream_List.txt", "r", encoding="utf-8") as f:
            for line in f:
                parsed_data = parse_provider_line(line)
                if parsed_data:
                    provider_details.append(parsed_data)
    except FileNotFoundError:
        print("❌ Lỗi: Không tìm thấy tệp 'Xtream_List.txt'. Vui lòng tạo tệp này.")
        with open("Xtream_List.txt", "w", encoding="utf-8") as f_template:
            f_template.write("# Vui lòng thêm nhà cung cấp của bạn vào đây.\n")
        return

    if not provider_details:
        print("⚠️ Tệp 'Xtream_List.txt' trống hoặc không chứa thông tin hợp lệ. Thoát.")
        return

    all_m3u_entries = []
    all_epg_urls = set()

    async with aiohttp.ClientSession() as session:
        tasks = []
        for name, host, user, pw, port in provider_details:
            fetcher = XtreamChannelFetcher(name, host, user, pw, port)
            tasks.append(fetcher.get_sports_channels(session))

        results = await asyncio.gather(*tasks)

        for m3u_entries, epg_url in results:
            all_m3u_entries.extend(m3u_entries)
            if epg_url:
                parsed_epg_url = urlparse(epg_url)
                epg_address = f"{parsed_epg_url.scheme}://{parsed_epg_url.netloc}/xmltv.php"
                all_epg_urls.add(epg_address)

    if not all_m3u_entries:
        print("\nKhông tìm thấy kênh thể thao nào từ bất kỳ nhà cung cấp nào. Thoát.")
        return

    epg_urls_str = ",".join(sorted(list(all_epg_urls)))
    header = f'#EXTM3U x-tvg-url="{epg_urls_str}" x-tvg-shift="+7"\n' if epg_urls_str else '#EXTM3U\n'
    
    final_m3u_content = header + "\n".join(all_m3u_entries)

    file_name = "Sports_Playlist.m3u"
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(final_m3u_content)

    print(f"\n🎉 Hoàn tất! Đã lưu thành công {len(all_m3u_entries)} kênh vào tệp '{file_name}'.")
    print("ℹ️ Các kênh thể thao đã được lọc và giữ nguyên nhóm kênh gốc từ nhà cung cấp.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nĐã thoát bởi người dùng.")
        sys.exit(0)
