import requests
import json
from datetime import datetime
from urllib.parse import urlparse
import sys
import re
from typing import Dict, Tuple, Optional, Any, List

#IPTV_link = "http://mag.ukhd.tv/c/"
#Input_Mac = "00:1A:79:5F:CD:59"

def print_colored(text: str, color: str) -> None:
    """Prints colored text."""
    colors: Dict[str, str] = {
        "green": "\033[92m",
        "red": "\033[91m",
        "blue": "\033[94m",
        "yellow": "\033[93m",
        "cyan": "\033[96m",
        "magenta": "\033[95m",
    }
    color_code: str = colors.get(color.lower(), "\033[0m")
    print(f"{color_code}{text}\033[0m")


def input_colored(prompt: str, color: str) -> str:
    """Gets user input with a colored prompt."""
    colors: Dict[str, str] = {
        "green": "\033[92m",
        "red": "\033[91m",
        "blue": "\033[94m",
        "yellow": "\033[93m",
        "cyan": "\033[96m",
        "magenta": "\033[95m",
    }
    color_code: str = colors.get(color.lower(), "\033[0m")
    return input(f"{color_code}{prompt}\033[0m")


def get_base_url() -> str:
    #"""Gets base URL from user input and formats it correctly."""
    base_url_input: str = input_colored("http://mag.ukhd.tv/c/", "cyan")
    parsed_url = urlparse(base_url_input)
    scheme = parsed_url.scheme or "http"
    host = parsed_url.hostname
    port = parsed_url.port or 80
    return f"{scheme}://{host}:{port}"


def get_mac_address() -> str:
    #"""Gets MAC address from user input."""
    return input_colored("00:1A:79:5F:CD:59", "cyan").upper()


def get_token(
    session: requests.Session, base_url: str, mac: str, timeout: int = 10
) -> Optional[str]:
    """Gets token using MAC authentication."""
    url = f"{base_url}/portal.php?action=handshake&type=stb&token=&JsHttpRequest=1-xml"

    headers = {"Authorization": f"MAC {mac}"}
    try:
        res = session.get(url, headers=headers, timeout=timeout)
        res.raise_for_status()
        data = res.json()
        return data["js"]["token"]
    except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
        print_colored(f"Error fetching token: {e}", "red")
        if "res" in locals():
            print_colored(f"Server response: {res.text}", "yellow")
        return None


def get_subscription(
    session: requests.Session, base_url: str, token: str, timeout: int = 10
) -> bool:
    """Gets subscription information using a Bearer token."""
    url = f"{base_url}/portal.php?type=account_info&action=get_main_info&JsHttpRequest=1-xml"

    headers = {"Authorization": f"Bearer {token}"}
    try:
        res = session.get(url, headers=headers, timeout=timeout)
        res.raise_for_status()
        data = res.json()
        mac = data["js"]["mac"]

        expiry = data.get("js", {}).get("phone", "N/A")
        print_colored(f"MAC = {mac}\nExpiry = {expiry}", "green")
        return True
    except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
        print_colored(f"Error fetching subscription info: {e}", "red")
        if "res" in locals():
            print_colored(f"Server response: {res.text}", "yellow")
        return False


def get_channel_list(
    session: requests.Session, base_url: str, token: str, timeout: int = 10
) -> Tuple[Optional[List[Dict]], Optional[Dict]]:
    """Gets the full channel list and genre information."""

    headers = {"Authorization": f"Bearer {token}"}
    try:

        url_genre = (
            f"{base_url}/server/load.php?type=itv&action=get_genres&JsHttpRequest=1-xml"
        )
        res_genre = session.get(url_genre, headers=headers, timeout=timeout)
        res_genre.raise_for_status()
        genre_data = res_genre.json()["js"]
        group_info = {group["id"]: group["title"] for group in genre_data}

        url_channels = f"{base_url}/portal.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
        res_channels = session.get(url_channels, headers=headers, timeout=timeout)
        res_channels.raise_for_status()
        channels_data = res_channels.json()["js"]["data"]
        return channels_data, group_info

    except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
        print_colored(f"Error fetching channel list: {e}", "red")
        return None, None


def save_channel_list(
    base_url: str, channels_data: List[Dict], group_info: Dict, mac: str
) -> None:
    """Saves the channel list to an M3U file."""
    sanitized_url = re.sub(r"[\W_]+", "_", base_url)
    filename = "Mac2M3uPlaylist.m3u" #f'{sanitized_url}_{datetime.now().strftime("%Y-%m-%d")}.m3u'
    count = 0
    try:
        with open(filename, "w", encoding="utf-8") as file:
            file.write("#EXTM3U\n")
            for channel in channels_data:
                group_id = channel.get("tv_genre_id", "0")
                group_name = group_info.get(group_id, "General")
                name = channel.get("name", "Unknown Channel")
                logo = channel.get("logo", "")

                cmd_url_raw = channel.get("cmds", [{}])[0].get("url", "")
                cmd_url = cmd_url_raw.replace("ffmpeg ", "")
                if "localhost" in cmd_url:
                    ch_id_match = re.search(r"/ch/(\d+)", cmd_url)
                    if ch_id_match:
                        ch_id = ch_id_match.group(1)
                        cmd_url = f"{base_url}/play/live.php?mac={mac}&stream={ch_id}&extension=ts"

                if not cmd_url:
                    continue

                file.write(
                    f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group_name}",{name}\n'
                )
                file.write(f"{cmd_url}\n")
                count += 1
        print_colored(f"\nTotal channels found: {count}", "green")
        print_colored(f"Channel list saved to: {filename}", "blue")
    except IOError as e:
        print_colored(f"Error saving channel list file: {e}", "red")


def main() -> None:
    """Main function to orchestrate the process."""
    try:
        base_url = "http://saray68.darktv.nl/c/" #get_base_url()
        mac = "00:1A:79:04:0F:AD" #get_mac_address()

        session = requests.Session()
        session.cookies.update({"mac": mac})
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3",
                "Referer": f"{base_url}/c/",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
            }
        )

        token = get_token(session, base_url, mac)
        if token:
            print_colored("Token acquired successfully.", "green")
            if get_subscription(session, base_url, token):
                print_colored("Fetching channel list...", "cyan")
                channels_data, group_info = get_channel_list(session, base_url, token)
                if channels_data and group_info:
                    save_channel_list(base_url, channels_data, group_info, mac)
    except KeyboardInterrupt:
        print_colored("\nExiting gracefully...", "yellow")
        sys.exit(0)
    except Exception as e:
        print_colored(f"An unexpected error occurred in main: {e}", "red")


if __name__ == "__main__":
    main()
