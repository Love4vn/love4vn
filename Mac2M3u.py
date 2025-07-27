import requests
import json
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import sys
import re
import time
from typing import Dict, Tuple, Optional, Any, List


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


def get_token(
    session: requests.Session, base_url: str, mac: str, timeout: int = 10
) -> Optional[str]:
    """Gets token using MAC authentication."""
    url = f"{base_url}portal.php?action=handshake&type=stb&token=&JsHttpRequest=1-xml"
    headers = {
        "Authorization": f"MAC {mac}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Connection": "keep-alive",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    try:
        res = session.get(url, headers=headers, timeout=timeout)
        
        # Print detailed response info
        print_colored(f"\nToken response status: {res.status_code}", "yellow")
        print_colored(f"Response content type: {res.headers.get('Content-Type', '')}", "yellow")
        print_colored(f"Response length: {len(res.text)} characters", "yellow")
        print_colored(f"Response content: {res.text[:100]}", "cyan")
        
        # Force JSON parsing regardless of content-type
        try:
            data = json.loads(res.text)
            token = data["js"]["token"]
            print_colored(f"Token acquired: {token}", "green")
            return token
        except json.JSONDecodeError:
            print_colored("JSON decode failed. Trying to extract token manually...", "yellow")
            # Try to extract token directly from text
            match = re.search(r'"token"\s*:\s*"([a-fA-F0-9]+)"', res.text)
            if match:
                token = match.group(1)
                print_colored(f"Token extracted manually: {token}", "green")
                return token
            print_colored("Failed to extract token", "red")
            return None
        except KeyError:
            print_colored("Token key not found in JSON", "red")
            return None
        
    except requests.RequestException as e:
        print_colored(f"Request error: {e}", "red")
        return None


def get_subscription(
    session: requests.Session, base_url: str, token: str, timeout: int = 10
) -> bool:
    """Gets subscription information using a Bearer token."""
    if not token:
        print_colored("No token available for subscription check", "red")
        return False
        
    url = f"{base_url}portal.php?type=account_info&action=get_main_info&JsHttpRequest=1-xml"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }
    
    try:
        res = session.get(url, headers=headers, timeout=timeout)
        res.raise_for_status()
        
        # Handle JSON response
        try:
            data = json.loads(res.text)
            mac = data["js"]["mac"]
            expiry = data.get("js", {}).get("phone", "N/A")
            print_colored(f"MAC = {mac}\nExpiry = {expiry}", "green")
            return True
        except json.JSONDecodeError:
            print_colored("Subscription response is not JSON", "red")
            print_colored(f"Response content: {res.text[:500]}", "yellow")
            return False
            
    except requests.RequestException as e:
        print_colored(f"Error fetching subscription info: {e}", "red")
        print_colored(f"Response content: {res.text[:500] if 'res' in locals() else ''}", "yellow")
        return False


def get_channel_list(
    session: requests.Session, base_url: str, token: str, timeout: int = 10
) -> Tuple[Optional[List[Dict]], Optional[Dict]]:
    """Gets the full channel list and genre information."""
    if not token:
        print_colored("No token available for channel list", "red")
        return None, None
        
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }
    
    try:
        # Get genres
        url_genre = f"{base_url}server/load.php?type=itv&action=get_genres&JsHttpRequest=1-xml"
        res_genre = session.get(url_genre, headers=headers, timeout=timeout)
        res_genre.raise_for_status()
        
        # Handle JSON response
        try:
            genre_data = json.loads(res_genre.text)["js"]
        except json.JSONDecodeError:
            print_colored("Genre response is not JSON", "red")
            print_colored(f"Response content: {res_genre.text[:500]}", "yellow")
            return None, None
            
        group_info = {group["id"]: group["title"] for group in genre_data}

        # Get channels
        url_channels = f"{base_url}portal.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
        res_channels = session.get(url_channels, headers=headers, timeout=timeout)
        res_channels.raise_for_status()
        
        # Handle JSON response
        try:
            channels_data = json.loads(res_channels.text)["js"]["data"]
        except json.JSONDecodeError:
            print_colored("Channels response is not JSON", "red")
            print_colored(f"Response content: {res_channels.text[:500]}", "yellow")
            return None, None
        
        return channels_data, group_info
    except requests.RequestException as e:
        print_colored(f"Error fetching channel list: {e}", "red")
        return None, None


def build_stream_url(cmd_url: str, base_url: str, mac: str, token: str) -> str:
    """Builds a valid stream URL from the command URL."""
    # Clean up ffmpeg prefix if exists
    clean_url = cmd_url.replace("ffmpeg ", "").strip()
    
    # Parse the URL
    parsed = urlparse(clean_url)
    
    # Handle localhost URLs
    if parsed.hostname in ["localhost", "127.0.0.1"]:
        base_parsed = urlparse(base_url)
        new_netloc = base_parsed.netloc
        scheme = "http" if base_parsed.scheme == "http" else "https"
        parsed = parsed._replace(netloc=new_netloc, scheme=scheme)
        clean_url = urlunparse(parsed)
    
    # Add token and MAC parameters
    if "?" in clean_url:
        clean_url += f"&token={token}&mac={mac}"
    else:
        clean_url += f"?token={token}&mac={mac}"
    
    return clean_url


def save_channel_list(
    session: requests.Session,
    base_url: str,
    channels_data: List[Dict],
    group_info: Dict,
    mac: str,
    token: str
) -> None:
    """Saves the channel list to an M3U file."""
    sanitized_url = re.sub(r"[\W_]+", "_", base_url)
    filename = f'{sanitized_url}_{datetime.now().strftime("%Y-%m-%d")}.m3u'
    count = 0
    
    try:
        with open(filename, "w", encoding="utf-8") as file:
            file.write("#EXTM3U\n")
            for channel in channels_data:
                group_id = channel.get("tv_genre_id", "0")
                group_name = group_info.get(group_id, "General")
                name = channel.get("name", "Unknown Channel")
                logo = channel.get("logo", "")

                # Get first valid cmd URL
                cmd_url_raw = ""
                for cmd in channel.get("cmds", []):
                    if cmd.get("url"):
                        cmd_url_raw = cmd["url"]
                        break
                
                if not cmd_url_raw:
                    continue
                
                # Build valid stream URL
                try:
                    stream_url = build_stream_url(cmd_url_raw, base_url, mac, token)
                except Exception as e:
                    print_colored(f"Error building URL for {name}: {e}", "yellow")
                    continue

                # Write to file
                file.write(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group_name}",{name}\n')
                file.write(f"{stream_url}\n")
                count += 1

        print_colored(f"\nTotal channels found: {count}", "green")
        print_colored(f"Channel list saved to: {filename}", "blue")
                
    except IOError as e:
        print_colored(f"Error saving channel list file: {e}", "red")


def main() -> None:
    """Main function to orchestrate the process."""
    try:
        # Fixed parameters
        base_url = "http://sup-4k.org/c/"
        mac = "00:1A:79:6e:87:57"

        print_colored("Using fixed parameters:", "yellow")
        print_colored(f"Base URL: {base_url}", "yellow")
        print_colored(f"MAC: {mac}", "yellow")

        session = requests.Session()
        session.cookies.update({"mac": mac})
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Referer": base_url,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive"
        })

        token = get_token(session, base_url, mac)
        if not token:
            print_colored("Failed to acquire token. Exiting.", "red")
            return

        print_colored("Token acquired successfully.", "green")
        
        if not get_subscription(session, base_url, token):
            print_colored("Failed to get subscription info. Continuing anyway...", "yellow")

        print_colored("Fetching channel list...", "cyan")
        channels_data, group_info = get_channel_list(session, base_url, token)
        
        if not channels_data or not group_info:
            print_colored("Failed to fetch channel list. Exiting.", "red")
            return

        save_channel_list(session, base_url, channels_data, group_info, mac, token)
        
    except KeyboardInterrupt:
        print_colored("\nExiting gracefully...", "yellow")
        sys.exit(0)
    except Exception as e:
        print_colored(f"An unexpected error occurred in main: {e}", "red")


if __name__ == "__main__":
    main()
