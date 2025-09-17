import sys
import asyncio
import aiohttp
from urllib.parse import urlparse, parse_qs
import json
import platform
import datetime

if platform.system() == "Windows":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class GetChannels:
    def __init__(self, url):
        self.url = url

    def parse_url(self):
        if not self.url.startswith("http"):
            raise ValueError(
                "Invalid URL format. Please provide a valid URL starting with 'http' or 'https'."
            )
        parsed = urlparse(self.url)
        host = parsed.hostname
        if parsed.port:
            port = parsed.port
        elif parsed.scheme == "https":
            port = 443
        else:
            port = 80
        username = parsed.username
        password = parsed.password
        if not username or not password:
            qs = parse_qs(parsed.query)
            username = username or (qs.get("username", [None])[0])
            password = password or (qs.get("password", [None])[0])
        return host, port, username, password

    def construct_url(self, host, port, username="", password=""):
        if username and password:
            if port:
                url1 = f"http://{host}:{port}/get.php?username={username}&password={password}&type=m3u_plus"
                url2 = f"http://{host}:{port}/player_api.php?username={username}&password={password}"
            else:
                url1 = f"http://{host}/get.php?username={username}&password={password}&type=m3u_plus"
                url2 = f"http://{host}/player_api.php?username={username}&password={password}"
            return url1, url2
        else:
            print("No username or password provided.")
            return None, None

    @staticmethod
    def create_payload(username, password):
        return {"username": username, "password": password}

    def data(self, resp):
        max_connections = resp.text().split('ections":"')[1].split('"')[0]
        if max_connections == "0":
            max_connections = "Unlimited"
        text = resp.text
        data = json.loads(text)
        active_connections = data["user_info"]["active_cons"]
        trial = resp.text().split('trial":"')[1].split('"')[0]
        if trial == "0":
            trial = "No"
        elif trial == "1":
            trial = "Yes"
        expire_values = resp.text().split('exp_date":"')
        if len(expire_values) > 1:
            expire = expire_values[1].split('"')[0]
            expire = datetime.datetime.fromtimestamp(int(expire)).strftime(
                "%Y-%m-%d:%H-%M-%S"
            )
        else:
            expire = "Unlimited"
        status = resp.text().split('status":"')[1].split('"')[0]
        return trial, expire, status, max_connections, active_connections

    async def display_info(self, host, port, username, password, payload):
        host, port, username, password = self.parse_url()
        url1, url2 = self.construct_url(host, port, username, password)
        if not url1 or not url2:
            print("Error: Unable to construct valid URLs.")
            return
        headers = {
            "Accept": "*/*",
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; 22101320G Build/UKQ1.231003.002)",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": f"http://{host}/",
            "Host": host,
        }
        api_url = f"http://{host}:{port}/player_api.php?username={username}&password={password}"
        async with aiohttp.ClientSession() as session:
            print(f"Fetching server info from: {api_url}")
            resp = await self.fetch(session, api_url, method="GET", headers=headers)
            try:
                info = json.loads(resp)
                user_info = info.get("user_info", {})
                server_info = info.get("server_info", {})
                max_connections = user_info.get("max_connections", "?")
                active_connections = user_info.get("active_cons", "?")
                trial = user_info.get("is_trial", "?")
                if trial == "0":
                    trial = "No"
                elif trial == "1":
                    trial = "Yes"
                expire = user_info.get("exp_date", "Unlimited")
                if expire and expire != "Unlimited" and expire is not None:
                    try:
                        expire = datetime.datetime.fromtimestamp(int(expire)).strftime(
                            "%Y-%m-%d:%H-%M-%S"
                        )
                    except Exception:
                        pass
                status = user_info.get("status", "?")
                if port:
                    streams_url = f"http://{host}:{port}/player_api.php?username={username}&password={password}&action=get_live_streams"
                else:
                    streams_url = f"http://{host}/player_api.php?username={username}&password={password}&action=get_live_streams"
                streams_resp = await self.fetch(
                    session, streams_url, method="GET", headers=headers
                )
                try:
                    streams = json.loads(streams_resp)
                    total_channels = len(streams) if isinstance(streams, list) else "?"
                except Exception:
                    total_channels = "?"
                print(
                    f"Host: http://{host}:{port}\nUser: {username}:{password}\nM3U: http://{host}:{port}/get.php?username={username}&password={password}&type=m3u_plus\nMax Connections: {max_connections}\nActive Connections: {active_connections}\nTrial: {trial}\nStatus: {status}\nExpiry: {expire}\nTotal Channels: {total_channels}\n"
                )
            except Exception as e:
                print(f"Failed to parse server info: {e}\nRaw response: {resp}")
            return

    async def fetch(self, session, url, method="GET", data=None, headers=None):
        async with session.request(method, url, data=data, headers=headers) as resp:
            return await resp.text()

    async def save_m3u(self, content, host):
        if not content:
            print("No content to save.")
            return
        if not host:
            print("Error: Host is not defined.")
            return
        _, _, username, password = self.parse_url()
        userpass = f"_{username}_{password}" if username and password else ""
        file_name = "Mac2M3uPlaylist.m3u" #(
            #f"{host}{userpass}.m3u".replace(":", "_")
            #.replace("/", "_")
            #.replace("?", "_")
        #)
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"{file_name} saved.")

    async def get_channels(self):
        host, port, username, password = self.parse_url()
        url1, url2 = self.construct_url(host, port, username, password)
        if not url1 or not url2:
            print("Error: Unable to construct valid URLs.")
            return

        headers = {
            "Accept": "*/*",
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; 22101320G Build/UKQ1.231003.002)",
            "Accept-Language": "en-US,en;q=0.5",
        }

        async with aiohttp.ClientSession() as session:
            # Lấy danh sách thể loại
            cat_data = {
                "username": username,
                "password": password,
                "action": "get_live_categories",
            }
            cat_resp = await self.fetch(
                session,
                f"http://{host}:{port}/player_api.php?",
                method="POST",
                data=cat_data,
                headers=headers,
            )
            categories = json.loads(cat_resp)
            cat_map = {
                c["category_id"]: c["category_name"]
                for c in categories
                if "category_id" in c and "category_name" in c
            }

            # Lấy danh sách kênh
            stream_data = {
                "username": username,
                "password": password,
                "action": "get_live_streams",
            }
            stream_resp = await self.fetch(
                session,
                f"http://{host}:{port}/player_api.php?",
                method="POST",
                data=stream_data,
                headers=headers,
            )
            streams = json.loads(stream_resp)

            # Lọc các kênh thể thao
            sport_keywords = ["LIVE", "Live", "SPORT", "SPORTS", "Sport", "Thể thao", "Sports", "Spor"]
            
            #epg_url = "https://epg.ott-app.cc/xmltv.php"
            epg_url = f"http://{host}:{port}/xmltv.php?username={username}&password={password}"
            m3u = [f'#EXTM3U url-tvg="{epg_url}" x-tvg-url-time-shift="7"']
            for s in streams:
                cat_id = str(s.get("category_id", ""))
                cat_name = cat_map.get(cat_id, "Unknown")

                # Lọc theo từ khóa
                if not any(k.lower() in cat_name.lower() for k in sport_keywords):
                    continue

                name = s.get("name", "")
                stream_id = s.get("stream_id", "")
                logo = s.get("stream_icon", "")
                tvg_id = name.lower().replace(" ", "").replace("-", "")
                tvg_name = name

                if not name or not stream_id:
                    continue

                m3u.append(
                    f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{tvg_name}" tvg-logo="{logo}" group-title="{cat_name}",{tvg_name}'
                )
                stream_url = f"http://{host}:{port}/live/{username}/{password}/{stream_id}.ts"
                m3u.append(stream_url)

            # Ghi ra file SPORT
            sport_file = "Mac2M3uPlaylist_SPORT.m3u"
            with open(sport_file, "w", encoding="utf-8") as f:
                f.write("\n".join(m3u))
            print(f"🏆 File M3U thể thao đã lưu: {sport_file}")

def main():
    url_input = "http://groundhogday.in:80/get.php?username=Jok123&password=Jok123&type=m3u_plus&output=m3u8" #input("Enter URL to fetch channels: ").strip()
    if not url_input:
        print("Error: No URL provided.")
        sys.exit(1)
    url = url_input
    try:
        channel_fetcher = GetChannels(url)
        host, port, username, password = channel_fetcher.parse_url()
        payload = GetChannels.create_payload(username, password)
        # Only display info for the relevant URL type
        if "player_api.php" in url:
            asyncio.run(
                channel_fetcher.display_info(host, port, username, password, payload)
            )
            asyncio.run(channel_fetcher.get_channels())
            return
        elif "get.php" in url:
            asyncio.run(
                channel_fetcher.display_info(host, port, username, password, payload)
            )
            asyncio.run(channel_fetcher.get_channels())
            return
        else:
            print(
                "Unsupported URL type. Please provide a get.php or player_api.php URL."
            )
    except Exception as e:
        print(f"❌ Failed: {e}")
        return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting...")
        sys.exit(0)
    except Exception as e:
        print(f"Error: {e}. Please try again.")
