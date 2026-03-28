import requests
import argparse
import signal
import os
import sys
import time
import subprocess
import logging
import shutil
import random
import json
import codecs
import csv
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
from dataclasses import dataclass
from requests.adapters import HTTPAdapter


@dataclass
class ScanConfig:
    """Configuration for an IPTV playlist scan."""
    group_title: str | None = None
    timeout: int = 15
    extended_timeout: int | None = None
    split: bool = False
    rename: bool = False
    skip_screenshots: bool = False
    output_file: str | None = None
    channel_search: str | None = None
    channel_pattern: object | None = None
    proxy_list: list | None = None
    test_geoblock: bool = False
    profile_bitrate: bool = False
    ffmpeg_available: bool = True
    ffprobe_available: bool = True
    backoff: str = 'linear'
    retries: int = 6
    workers: int = 4
    insecure: bool = False
    filter_min_res: str | None = None
    output_playlist: str | None = None


ACTIVE_SUBPROCESSES = set()
_subprocess_lock = threading.Lock()
cancel_event = threading.Event()


def setup_logging(verbose_level):
    if verbose_level == 1:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    elif verbose_level >= 2:
        logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    else:
        logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')


def terminate_process(process):
    if process is None:
        return
    if process.poll() is not None:
        return
    try:
        if os.name == 'nt':
            process.terminate()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except Exception:
        pass

    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            if os.name == 'nt':
                process.kill()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except Exception:
            pass


def cleanup_active_subprocesses():
    with _subprocess_lock:
        procs = list(ACTIVE_SUBPROCESSES)
    for process in procs:
        terminate_process(process)
    with _subprocess_lock:
        ACTIVE_SUBPROCESSES.clear()


def run_managed_subprocess(command, timeout):
    popen_kwargs = {
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE
    }
    if os.name == 'nt':
        creation_flag = getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
        if creation_flag:
            popen_kwargs['creationflags'] = creation_flag
    else:
        popen_kwargs['preexec_fn'] = os.setsid

    process = None
    try:
        process = subprocess.Popen(command, **popen_kwargs)
        with _subprocess_lock:
            ACTIVE_SUBPROCESSES.add(process)
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        if process is not None:
            terminate_process(process)
        raise
    finally:
        if process is not None:
            with _subprocess_lock:
                ACTIVE_SUBPROCESSES.discard(process)


def handle_sigint(signum, frame):
    logging.info("Interrupt received, stopping...")
    cancel_event.set()
    cleanup_active_subprocesses()


signal.signal(signal.SIGINT, handle_sigint)


def get_video_bitrate(url):
    """
    Measure approximate video bitrate by sampling the stream for 10 seconds.
    """
    if url.startswith('udp://'):
        return "N/A"
    command = [
        'ffmpeg',
        '-v',
        'debug',
        '-user_agent',
        'VLC/3.0.14',
        '-i',
        url,
        '-t',
        '10',
        '-f',
        'null',
        '-'
    ]
    try:
        result = run_managed_subprocess(command, timeout=20)
        output = result.stderr.decode(errors='ignore')
        total_bytes = 0
        for line in output.splitlines():
            if "Statistics:" in line and "bytes read" in line:
                parts = line.split("bytes read")
                try:
                    size_str = parts[0].strip().split()[-1]
                    total_bytes = int(size_str)
                    break
                except (IndexError, ValueError):
                    continue
        if total_bytes <= 0:
            return "N/A"
        bitrate_kbps = (total_bytes * 8) / 1000 / 10
        return f"{round(bitrate_kbps)} kbps"
    except FileNotFoundError:
        logging.warning("ffmpeg not found when attempting to measure video bitrate.")
        return "Unknown"
    except subprocess.TimeoutExpired:
        logging.error(f"Timeout when trying to get video bitrate for {url}")
        return "Unknown"
    except Exception as exc:
        logging.error(f"Error when attempting to retrieve video bitrate: {exc}")
        return "N/A"


def check_ffmpeg_availability():
    """Check whether ffmpeg and ffprobe are available in the system PATH."""
    tool_status = {}

    for tool in ['ffmpeg', 'ffprobe']:
        available = False
        try:
            result = run_managed_subprocess([tool, '-version'], timeout=5)
            if result.returncode == 0:
                logging.debug(f"{tool} is available")
                available = True
            else:
                logging.error(f"{tool} is installed but not working properly")
        except FileNotFoundError:
            logging.error(f"{tool} is not found in system PATH. Please install {tool} to use this tool.")
        except subprocess.TimeoutExpired:
            logging.error(f"{tool} check timed out")
        except Exception as e:
            logging.exception(f"Unexpected error checking {tool}: {e}")
        tool_status[tool] = available

    return tool_status


def test_with_proxy(url, proxy, timeout, retries=3):
    """
    Test stream access through a specific proxy
    """
    headers = {
        'User-Agent': 'VLC/3.0.14 LibVLC/3.0.14'
    }
    proxies = {'http': proxy, 'https': proxy}
    stream_extensions = ('.ts', '.m2ts', '.m4s', '.mp4', '.aac', '.m3u8')

    for attempt in range(max(1, retries)):
        try:
            with requests.get(url, stream=True, timeout=(5, timeout), headers=headers, proxies=proxies) as resp:
                if resp.status_code != 200:
                    continue
                content_type = resp.headers.get('Content-Type', '')
                lowered_type = content_type.lower()
                stream_path = urlparse(resp.url).path.lower()
                if (
                    lowered_type.startswith('video/')
                    or lowered_type.startswith('audio/')
                    or 'application/vnd.apple.mpegurl' in lowered_type
                    or 'application/x-mpegurl' in lowered_type
                    or 'application/octet-stream' in lowered_type
                    or 'application/mp4' in lowered_type
                    or stream_path.endswith(stream_extensions)
                ):
                    # Read some data to verify stream
                    for chunk in resp.iter_content(1024 * 500):  # 500KB
                        if chunk:
                            return True
        except requests.RequestException as e:
            logging.debug(f"Proxy test failed with {proxy} (attempt {attempt + 1}/{max(1, retries)}): {summarize_error(e)}")

        if attempt + 1 < max(1, retries):
            time.sleep(0.5 * (attempt + 1))

    return False


def load_proxy_list(proxy_file):
    """
    Load proxy list from file. Supports formats:
    - ip:port
    - protocol://ip:port
    - JSON format with proxy objects (supports both 'protocol' and 'protocols' fields)
    """
    proxies = []
    valid_proxies = []

    def validate_proxy_entry(proxy_value):
        if not proxy_value:
            return None, "entry is empty"

        candidate = proxy_value.strip()
        if not candidate:
            return None, "entry is empty"
        if '://' not in candidate:
            candidate = f"http://{candidate}"

        parsed = urlparse(candidate)
        scheme = parsed.scheme.lower()
        if scheme not in {'http', 'https', 'socks4', 'socks4a', 'socks5', 'socks5h'}:
            return None, f"unsupported proxy scheme '{parsed.scheme}'"
        if not parsed.hostname:
            return None, "missing proxy host"

        try:
            port = parsed.port
        except ValueError:
            return None, "invalid proxy port"
        if port is None:
            return None, "missing proxy port"
        if port < 1 or port > 65535:
            return None, f"proxy port {port} is out of range (1-65535)"

        if parsed.path not in ('', '/'):
            return None, "proxy URL must not include a path"
        if parsed.params or parsed.query or parsed.fragment:
            return None, "proxy URL must not include params, query, or fragment"

        return f"{scheme}://{parsed.netloc}", None

    try:
        with open(proxy_file, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read().strip()
            
            # Try JSON format first
            try:
                proxy_data = json.loads(content)
                if isinstance(proxy_data, list):
                    for proxy in proxy_data:
                        if isinstance(proxy, dict):
                            ip = proxy.get('ip')
                            port = proxy.get('port')
                            
                            if ip and port:
                                # Check for protocols array (new format)
                                if 'protocols' in proxy and isinstance(proxy['protocols'], list):
                                    for protocol in proxy['protocols']:
                                        proxies.append(f"{protocol}://{ip}:{port}")
                                # Fall back to single protocol (legacy format)
                                elif 'protocol' in proxy:
                                    protocol = proxy.get('protocol', 'http')
                                    proxies.append(f"{protocol}://{ip}:{port}")
                                # Default to http if no protocol specified
                                else:
                                    proxies.append(f"http://{ip}:{port}")
                        elif isinstance(proxy, str):
                            proxies.append(proxy)
            except json.JSONDecodeError:
                # Fall through to plain text format parsing.
                pass
            
            if not proxies:
                # Plain text format
                lines = content.split('\n')
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        proxies.append(line)
                    
    except FileNotFoundError:
        logging.error(f"Proxy file not found: {proxy_file}")
    except Exception as e:
        logging.error(f"Error loading proxy file: {str(e)}")

    skipped = 0
    for idx, proxy in enumerate(proxies, 1):
        validated_proxy, error_message = validate_proxy_entry(proxy)
        if error_message:
            logging.warning(f"Proxy entry #{idx} '{proxy}': {error_message}")
            skipped += 1
            continue
        valid_proxies.append(validated_proxy)

    if proxies:
        if valid_proxies:
            logging.info(f"Loaded {len(valid_proxies)} of {len(proxies)} proxies ({skipped} skipped)")
        else:
            logging.error("No valid proxy entries remain after validation.")

    return valid_proxies


def summarize_error(exc):
    msg = str(exc).lower()
    if isinstance(exc, requests.Timeout):
        return "Connection timed out"
    if isinstance(exc, requests.ConnectionError):
        if any(kw in msg for kw in ['dns', 'name or service not known', 'nodename nor servname', 'no such host', 'getaddrinfo failed']):
            return "DNS resolution failed"
        if 'ssl' in msg or 'tls' in msg or 'certificate' in msg or 'handshake' in msg:
            return "SSL/TLS error"
        if 'connection refused' in msg:
            return "Connection refused"
        return "Connection error"
    if isinstance(exc, requests.TooManyRedirects):
        return "Redirect loop"
    return str(exc)[:80]


def check_channel_status(url, timeout, retries=6, extended_timeout=None, proxy_list=None, test_geoblock=False, ffmpeg_available=True, backoff='linear', session=None):
    # Handle UDP streams
    if url.startswith('udp://'):
        logging.debug(f"UDP stream detected, treating as alive: {url}")
        return 'Alive', url, None

    headers = {
        'User-Agent': 'VLC/3.0.14 LibVLC/3.0.14'
    }
    min_data_threshold = 1024 * 500  # 500KB minimum threshold for direct streams
    playlist_segment_threshold = 1024 * 128  # Smaller threshold for HLS media segments
    max_playlist_depth = 4
    initial_timeout = 5
    retryable_http_statuses = {408, 425, 429, 500, 502, 503, 504}
    geoblock_statuses = {403, 451, 426}
    secondary_geoblock_statuses = {401, 423, 451}
    backoff_mode = (backoff or 'linear').strip().lower()
    if backoff_mode not in {'none', 'linear', 'exponential'}:
        logging.warning(f"Unknown backoff mode '{backoff_mode}', defaulting to linear.")
        backoff_mode = 'linear'

    def is_playlist(content_type, target_url):
        lowered_type = content_type.lower()
        lowered_url = target_url.lower()
        lowered_path = urlparse(lowered_url).path
        return (
            'application/vnd.apple.mpegurl' in lowered_type
            or 'application/x-mpegurl' in lowered_type
            or lowered_path.endswith('.m3u8')
        )

    def is_direct_stream(content_type, target_url):
        lowered_type = content_type.lower()
        lowered_path = urlparse(target_url).path.lower()
        stream_extensions = ('.ts', '.m2ts', '.m4s', '.mp4', '.aac')
        return (
            lowered_type.startswith('video/')
            or lowered_type.startswith('audio/')
            or 'application/octet-stream' in lowered_type
            or 'application/mp4' in lowered_type
            or lowered_path.endswith(stream_extensions)
        )

    def extract_next_url(base_url, playlist_body):
        def parse_tag_attributes(tag_line):
            attributes = {}
            _, _, payload = tag_line.partition(':')
            if not payload:
                return attributes

            index = 0
            payload_length = len(payload)
            while index < payload_length:
                while index < payload_length and payload[index] in ' \t,':
                    index += 1
                if index >= payload_length:
                    break

                key_start = index
                while index < payload_length and payload[index] not in '=,':
                    index += 1
                key = payload[key_start:index].strip().upper()
                if not key:
                    index += 1
                    continue
                if index >= payload_length or payload[index] != '=':
                    while index < payload_length and payload[index] != ',':
                        index += 1
                    continue

                index += 1
                if index < payload_length and payload[index] == '"':
                    index += 1
                    value_chars = []
                    while index < payload_length:
                        char = payload[index]
                        if char == '\\' and index + 1 < payload_length:
                            value_chars.append(payload[index + 1])
                            index += 2
                            continue
                        if char == '"':
                            index += 1
                            break
                        value_chars.append(char)
                        index += 1
                    value = ''.join(value_chars)
                else:
                    value_start = index
                    while index < payload_length and payload[index] != ',':
                        index += 1
                    value = payload[value_start:index].strip()

                attributes[key] = value
                if index < payload_length and payload[index] == ',':
                    index += 1

            return attributes

        def parse_resolution_pixels(resolution_value):
            if not resolution_value:
                return 0
            match = re.match(r'^\s*(\d+)\s*x\s*(\d+)\s*$', resolution_value, flags=re.IGNORECASE)
            if not match:
                return 0
            width = int(match.group(1))
            height = int(match.group(2))
            if width <= 0 or height <= 0:
                return 0
            return width * height

        def parse_int(value):
            if not value:
                return 0
            try:
                parsed = int(value.strip())
                return parsed if parsed > 0 else 0
            except (TypeError, ValueError):
                return 0

        saw_stream_inf = False
        pending_variant_attrs = None
        best_variant_url = None
        best_variant_score = None
        fallback_url = None

        for raw_line in playlist_body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith('#'):
                if line.upper().startswith('#EXT-X-STREAM-INF'):
                    saw_stream_inf = True
                    pending_variant_attrs = parse_tag_attributes(line)
                continue

            resolved_url = urljoin(base_url, line)
            if not saw_stream_inf:
                return resolved_url

            if pending_variant_attrs is not None:
                resolution_pixels = parse_resolution_pixels(pending_variant_attrs.get('RESOLUTION'))
                average_bandwidth = parse_int(pending_variant_attrs.get('AVERAGE-BANDWIDTH'))
                bandwidth = parse_int(pending_variant_attrs.get('BANDWIDTH'))
                quality_score = (
                    1 if resolution_pixels else 0,
                    resolution_pixels,
                    average_bandwidth,
                    bandwidth
                )
                if best_variant_score is None or quality_score > best_variant_score:
                    best_variant_score = quality_score
                    best_variant_url = resolved_url
                pending_variant_attrs = None
            elif fallback_url is None:
                fallback_url = resolved_url

        if best_variant_url:
            return best_variant_url
        return fallback_url

    def read_stream(response, min_bytes):
        bytes_read = 0
        for chunk in response.iter_content(1024 * 128):  # 128KB chunks
            if not chunk:
                continue
            bytes_read += len(chunk)
            if bytes_read >= min_bytes:
                logging.debug(f"Data received: {bytes_read} bytes")
                return 'Alive', response.url, None

        logging.debug(f"Data received: {bytes_read} bytes")
        if min_bytes >= min_data_threshold:
            fallback_threshold = min_bytes
        else:
            fallback_threshold = max(32768, min_bytes // 2)  # Allow smaller segments to pass
        if bytes_read >= fallback_threshold:
            return 'Alive', response.url, None
        return 'Dead', None, 'Insufficient data received'

    def verify(target_url, current_timeout, depth, visited):
        if depth > max_playlist_depth:
            logging.debug("Maximum playlist nesting depth reached")
            return 'Dead', None, 'Max playlist depth exceeded'

        normalized_url = target_url.split('#')[0]
        if normalized_url in visited:
            logging.debug(f"Detected playlist loop at {target_url}")
            return 'Dead', None, 'Playlist loop detected'
        visited.add(normalized_url)

        playlist_text = None
        final_url = target_url

        http = session or requests
        try:
            with http.get(
                target_url,
                stream=True,
                timeout=(initial_timeout, current_timeout),
                headers=headers
            ) as resp:
                if resp.status_code in retryable_http_statuses:
                    logging.debug(f"Retryable HTTP status {resp.status_code} for {target_url}, retrying...")
                    return 'Retry', None, f'HTTP {resp.status_code}'
                if resp.status_code in geoblock_statuses:
                    logging.debug(f"Potential geoblock detected: HTTP {resp.status_code}")
                    return 'Geoblocked', None, None
                if resp.status_code != 200:
                    logging.debug(f"HTTP status code not OK: {resp.status_code}")
                    if resp.status_code in secondary_geoblock_statuses:
                        return 'Geoblocked', None, None
                    return 'Dead', None, f'HTTP {resp.status_code}'

                content_type = resp.headers.get('Content-Type', '')
                logging.debug(f"Content-Type: {content_type}")

                final_url = resp.url
                if is_playlist(content_type, final_url):
                    playlist_text = resp.text
                elif is_direct_stream(content_type, final_url):
                    min_bytes = min_data_threshold if depth == 0 else playlist_segment_threshold
                    return read_stream(resp, min_bytes)
                else:
                    if content_type.lower().startswith('text/'):
                        logging.debug(f"Content-Type not recognized as stream: {content_type}")
                        return 'Dead', None, f'Unrecognized content type: {content_type}'
                    logging.debug(f"Unrecognized Content-Type '{content_type}'. Attempting fallback stream read.")
                    min_bytes = min_data_threshold if depth == 0 else playlist_segment_threshold
                    return read_stream(resp, min_bytes)
        except requests.ConnectionError as exc:
            logging.warning(f"{summarize_error(exc)} for {target_url}")
            return 'Retry', None, summarize_error(exc)
        except requests.Timeout as exc:
            logging.warning(f"{summarize_error(exc)} for {target_url}")
            return 'Retry', None, summarize_error(exc)
        except requests.RequestException as e:
            logging.error(f"Request failed for {target_url}: {summarize_error(e)}")
            return 'Dead', None, summarize_error(e)

        if not playlist_text:
            logging.debug("Playlist response was empty")
            return 'Dead', None, 'Empty playlist response'

        next_url = extract_next_url(final_url, playlist_text)
        if not next_url:
            logging.debug("No media segments found in playlist")
            return 'Dead', None, 'No media segments in playlist'

        logging.debug(f"Following playlist entry: {next_url}")
        return verify(next_url, current_timeout, depth + 1, visited)

    def get_retry_delay(attempt_index):
        if backoff_mode == 'none':
            return 0
        if backoff_mode == 'exponential':
            return min(2 ** attempt_index, 30)
        return min(attempt_index + 1, 10)

    def attempt_check(current_timeout):
        total_attempts = max(1, retries)
        last_reason = None
        for attempt in range(total_attempts):
            if cancel_event.is_set():
                return 'Dead', None, 'Cancelled'
            visited = set()
            status, stream_url, reason = verify(url, current_timeout, 0, visited)
            if status == 'Retry':
                last_reason = reason
                logging.debug(f"Retrying stream check for {url} ({attempt + 1}/{total_attempts})")
                if attempt + 1 < total_attempts:
                    delay_seconds = get_retry_delay(attempt)
                    if delay_seconds > 0:
                        logging.debug(f"Applying {backoff_mode} backoff delay of {delay_seconds}s")
                        time.sleep(delay_seconds)
                continue
            return status, stream_url, reason
        logging.error("Maximum retries exceeded for checking channel status")
        return 'Dead', None, last_reason or 'Max retries exceeded'

    # First attempt with the initial timeout
    status, stream_url, error_reason = attempt_check(timeout)

    # If the channel is detected as dead and extended_timeout is specified, retry with extended timeout
    if status == 'Dead' and extended_timeout:
        logging.info(f"Channel initially detected as dead. Retrying with an extended timeout of {extended_timeout} seconds.")
        status, stream_url, error_reason = attempt_check(extended_timeout)

    # If geoblocked and proxy testing is enabled, test with proxies
    if status == 'Geoblocked' and test_geoblock and proxy_list:
        logging.info(f"Testing geoblocked stream with {len(proxy_list)} proxies...")
        for proxy in random.sample(proxy_list, min(3, len(proxy_list))):  # Test up to 3 random proxies
            if test_with_proxy(url, proxy, timeout):
                logging.info(f"Stream accessible via proxy {proxy} - confirming geoblock")
                return 'Geoblocked (Confirmed)', None, None
        logging.info("Stream not accessible via tested proxies")
        return 'Geoblocked (Unconfirmed)', None, None

    # Final Verification using ffmpeg/ffprobe for streams marked alive
    if status == 'Alive' and ffmpeg_available:
        verification_url = stream_url or url
        try:
            command = [
                'ffmpeg', '-user_agent', headers['User-Agent'], '-i', verification_url, '-t', '5', '-f', 'null', '-'
            ]
            ffmpeg_result = run_managed_subprocess(command, timeout=15)
            if ffmpeg_result.returncode != 0:
                logging.warning(f"ffmpeg failed to read stream ({verification_url}); continuing with HTTP validation result")
        except FileNotFoundError:
            logging.warning(f"ffmpeg not found for stream verification, skipping ffmpeg check")
            # Keep status as 'Alive' since we already verified via HTTP
        except subprocess.TimeoutExpired:
            logging.warning(f"Timeout when trying to verify stream with ffmpeg for {verification_url}; continuing with HTTP validation result")
        except Exception as e:
            logging.warning(f"Error verifying stream with ffmpeg ({verification_url}): {str(e)}; continuing with HTTP validation result")

    return status, stream_url, error_reason


def build_screenshot_filename(output_path, channel_index, channel_name, max_length=200):
    illegal_chars_pattern = r'[\\/:*?"<>|]'
    windows_reserved_names = {
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    }

    normalized_name = channel_name if channel_name else "channel"
    normalized_name = re.sub(illegal_chars_pattern, '-', normalized_name)
    normalized_name = normalized_name.strip().strip('.')
    normalized_name = re.sub(r'\s+', ' ', normalized_name)
    if not normalized_name:
        normalized_name = "channel"

    if normalized_name.upper() in windows_reserved_names:
        normalized_name = f"{normalized_name}_channel"

    base_prefix = f"{channel_index}-"
    remaining_length = max(1, max_length - len(base_prefix))
    base_name = normalized_name[:remaining_length]
    candidate = f"{base_prefix}{base_name}"

    suffix_index = 1
    while os.path.exists(os.path.join(output_path, f"{candidate}.png")):
        suffix = f"_{suffix_index}"
        allowed_length = max(1, remaining_length - len(suffix))
        candidate = f"{base_prefix}{base_name[:allowed_length]}{suffix}"
        suffix_index += 1

    return candidate


def capture_frame(url, output_path, file_name):
    if url.startswith('udp://'):
        logging.debug(f"Skipping screenshot for UDP stream: {url}")
        return False
    command = [
        'ffmpeg', '-y', '-i', url, '-frames:v', '1',
        os.path.join(output_path, f"{file_name}.png")
    ]
    try:
        run_managed_subprocess(command, timeout=30)
        logging.debug(f"Screenshot saved for {file_name}")
        return True
    except FileNotFoundError:
        logging.error(f"ffmpeg not found. Please install ffmpeg to capture screenshots.")
        return False
    except subprocess.TimeoutExpired:
        logging.error(f"Timeout when trying to capture frame for {file_name}")
        return False
    except Exception as e:
        logging.error(f"Error capturing frame for {file_name}: {str(e)}")
        return False


def get_detailed_stream_info(url, profile_bitrate=False):
    if url.startswith('udp://'):
        return "Unknown", "N/A", "Unknown", None
    command = [
        'ffprobe', '-v', 'error',
        '-analyzeduration', '15000000', '-probesize', '15000000',
        '-select_streams', 'v', '-show_entries',
        'stream=codec_name,width,height,r_frame_rate', '-of', 'json', url
    ]
    try:
        result = run_managed_subprocess(command, timeout=10)
        output = result.stdout.decode(errors='ignore')
        codec_name = "Unknown"
        width = height = 0
        fps = None
        probe_data = json.loads(output) if output else {}
        streams = probe_data.get('streams', []) if isinstance(probe_data, dict) else []

        selected_stream = None
        selected_pixels = -1
        for stream in streams:
            if not isinstance(stream, dict):
                continue
            stream_width = int(stream.get('width') or 0)
            stream_height = int(stream.get('height') or 0)
            pixel_count = stream_width * stream_height
            if pixel_count > selected_pixels:
                selected_pixels = pixel_count
                selected_stream = stream

        if selected_stream:
            codec_name = (selected_stream.get('codec_name') or "Unknown").upper()
            width = int(selected_stream.get('width') or 0)
            height = int(selected_stream.get('height') or 0)
            fps_data = selected_stream.get('r_frame_rate')
            if fps_data:
                try:
                    if '/' in fps_data:
                        numerator_str, denominator_str = fps_data.split('/', 1)
                        numerator = float(numerator_str)
                        denominator = float(denominator_str)
                        if denominator > 0:
                            fps = round(numerator / denominator)
                    else:
                        fps = round(float(fps_data))
                except ValueError:
                    logging.debug(f"Unable to parse frame rate '{fps_data}' for {url}")
        else:
            # No video streams found — check if audio-only
            try:
                audio_probe_cmd = [
                    'ffprobe', '-v', 'error',
                    '-analyzeduration', '15000000', '-probesize', '15000000',
                    '-select_streams', 'a', '-show_entries', 'stream=codec_type',
                    '-of', 'json', url
                ]
                audio_result = run_managed_subprocess(audio_probe_cmd, timeout=10)
                audio_output = audio_result.stdout.decode(errors='ignore')
                audio_data = json.loads(audio_output) if audio_output else {}
                audio_streams = audio_data.get('streams', []) if isinstance(audio_data, dict) else []
                if audio_streams:
                    return "Audio Only", "N/A", "Audio Only", None
            except Exception:
                pass

        if fps is not None and fps <= 0:
            fps = None

        # Determine resolution string with FPS
        resolution = "Unknown"
        if width > 0 and height > 0:
            if width >= 3840 and height >= 2160:
                resolution = "4K"
            elif width >= 1920 and height >= 1080:
                resolution = "1080p"
            elif width >= 1280 and height >= 720:
                resolution = "720p"
            else:
                resolution = "SD"

        video_bitrate = get_video_bitrate(url) if profile_bitrate else "N/A"

        return codec_name, video_bitrate, resolution, fps
    except FileNotFoundError:
        logging.error(f"ffprobe not found. Please install ffprobe to get stream info.")
        return "Unknown", "Unknown", "Unknown", None
    except subprocess.TimeoutExpired:
        logging.error(f"Timeout when trying to get stream info for {url}")
        return "Unknown", "Unknown", "Unknown", None
    except Exception as e:
        logging.error(f"Error getting stream info: {str(e)}")
        return "Unknown", "Unknown", "Unknown", None


def format_stream_info(codec_name, video_bitrate, resolution, fps):
    if resolution != "Unknown" and fps:
        resolution_display = f"{resolution}{fps}"
    else:
        resolution_display = resolution

    components = []
    if resolution_display != "Unknown":
        components.append(resolution_display)
    if codec_name and codec_name != "Unknown":
        components.append(codec_name)

    base_info = " ".join(components) if components else "Unknown"
    if video_bitrate and isinstance(video_bitrate, str) and video_bitrate not in ("Unknown", "N/A"):
        return f"{base_info} ({video_bitrate})"
    return base_info


def get_audio_bitrate(url):
    if url.startswith('udp://'):
        return "UDP Stream (No audio info)"
    command = [
        'ffprobe', '-v', 'error',
        '-analyzeduration', '15000000', '-probesize', '15000000',
        '-select_streams', 'a:0', '-show_entries',
        'stream=codec_name,bit_rate', '-of', 'default=noprint_wrappers=1', url
    ]
    try:
        result = run_managed_subprocess(command, timeout=10)
        output = result.stdout.decode()
        audio_bitrate = None
        codec_name = None
        for line in output.splitlines():
            if line.startswith("bit_rate="):
                bitrate_value = line.split('=')[1]
                if bitrate_value.isdigit():
                    audio_bitrate = int(bitrate_value) // 1000  # Convert to kbps
                else:
                    audio_bitrate = 'N/A'
            elif line.startswith("codec_name="):
                codec_name = line.split('=')[1].upper()

        return f"{audio_bitrate} kbps {codec_name}" if codec_name and audio_bitrate else "Unknown"
    except FileNotFoundError:
        logging.error(f"ffprobe not found. Please install ffprobe to get audio bitrate.")
        return "Unknown"
    except subprocess.TimeoutExpired:
        logging.error(f"Timeout when trying to get audio bitrate for {url}")
        return "Unknown"
    except Exception as e:
        logging.error(f"Error getting audio bitrate: {str(e)}")
        return "Unknown"


def check_label_mismatch(channel_name, resolution):
    channel_name_lower = channel_name.lower()

    mismatches = []

    # Compare resolution ignoring the framerate part (word-boundary matching)
    if re.search(r'\b4k\b', channel_name_lower) or re.search(r'\buhd\b', channel_name_lower):
        if resolution != "4K":
            mismatches.append(f"\033[91mExpected 4K, got {resolution}\033[0m")
    elif re.search(r'\b1080p\b', channel_name_lower) or re.search(r'\bfhd\b', channel_name_lower):
        if resolution != "1080p":
            mismatches.append(f"\033[91mExpected 1080p, got {resolution}\033[0m")
    elif re.search(r'\bhd\b', channel_name_lower):
        if resolution not in ["1080p", "720p"]:
            mismatches.append(f"\033[91mExpected 720p or 1080p, got {resolution}\033[0m")
    elif resolution == "4K":
        mismatches.append(f"\033[91m4K channel not labeled as such\033[0m")

    return mismatches


def parse_extinf_metadata(extinf_line):
    """
    Parse an EXTINF line into attributes and channel name while handling quoted values.
    """
    if not extinf_line.startswith('#EXTINF'):
        return {}, "Unknown Channel"

    _, _, payload = extinf_line.partition(':')
    if not payload:
        return {}, "Unknown Channel"

    in_quotes = False
    escape_next = False
    split_index = -1
    for idx, char in enumerate(payload):
        if escape_next:
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue
        if char == '"':
            in_quotes = not in_quotes
            continue
        if char == ',' and not in_quotes:
            split_index = idx
            break

    if split_index >= 0:
        metadata_payload = payload[:split_index]
        channel_name = payload[split_index + 1:].strip() or "Unknown Channel"
    else:
        metadata_payload = payload
        channel_name = "Unknown Channel"

    attributes = {}
    metadata = metadata_payload.strip()
    index = 0
    metadata_length = len(metadata)

    while index < metadata_length:
        while index < metadata_length and metadata[index].isspace():
            index += 1
        if index >= metadata_length:
            break

        key_start = index
        while index < metadata_length and not metadata[index].isspace() and metadata[index] != '=':
            index += 1
        key = metadata[key_start:index].strip().lower()

        if not key:
            if index < metadata_length:
                index += 1
            continue

        equals_index = index
        while equals_index < metadata_length and metadata[equals_index].isspace():
            equals_index += 1

        if equals_index >= metadata_length or metadata[equals_index] != '=':
            # Token without '=' (e.g., duration) is ignored.
            index = equals_index
            continue

        index = equals_index + 1
        while index < metadata_length and metadata[index].isspace():
            index += 1

        if index < metadata_length and metadata[index] == '"':
            index += 1
            value_chars = []
            while index < metadata_length:
                char = metadata[index]
                if char == '\\' and index + 1 < metadata_length:
                    value_chars.append(metadata[index + 1])
                    index += 2
                    continue
                if char == '"':
                    index += 1
                    break
                value_chars.append(char)
                index += 1
            value = ''.join(value_chars)
        else:
            value_start = index
            while index < metadata_length and not metadata[index].isspace():
                index += 1
            value = metadata[value_start:index].strip()

        if key:
            attributes[key] = value

    return attributes, channel_name


def get_channel_name(extinf_line):
    _, channel_name = parse_extinf_metadata(extinf_line)
    return channel_name


def get_group_name(extinf_line):
    attributes, _ = parse_extinf_metadata(extinf_line)
    group_name = attributes.get('group-title')
    if group_name:
        return group_name
    return "Unknown Group"


def get_channel_id(url):
    if not url:
        return "Unknown"
    segment = url.rsplit('/', 1)[-1]
    if segment:
        return segment.replace('.ts', '')
    return "Unknown"


def get_channel_stream_entry(lines, extinf_index):
    """
    Return (stream_url, metadata_lines, end_index) for a channel entry starting at #EXTINF.
    metadata_lines includes intermediary comment/blank lines between #EXTINF and the stream URL.
    """
    metadata_lines = []
    j = extinf_index + 1
    while j < len(lines):
        candidate = lines[j].strip()
        if candidate.startswith('#EXTINF'):
            return None, metadata_lines, j - 1
        if not candidate or candidate.startswith('#'):
            metadata_lines.append(candidate)
            j += 1
            continue
        return candidate, metadata_lines, j
    return None, metadata_lines, len(lines) - 1


def is_line_needed(line, group_title, pattern):
    if not line.startswith('#EXTINF'):
        return False
    if group_title:
        group_name = get_group_name(line).strip().lower()
        if group_name != group_title.strip().lower():
            return False
    if pattern:
        channel_name = get_channel_name(line)
        if not pattern.search(channel_name):
            return False
    return True


def compile_channel_pattern(channel_search):
    if not channel_search:
        return None
    try:
        return re.compile(channel_search, flags=re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Invalid channel search regex '{channel_search}': {exc}") from exc


# Query parameters commonly used for tracking/auth tokens that change between sessions
_TRACKING_PARAMS = frozenset({
    'token', 'auth', 'key', 'sig', 'signature', 'expires', 'expire',
    'ts', 'timestamp', 'nonce', 'hash', 'h', 'tk', 'st', 'e',
    'utid', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content',
    'utm_term', 'fbclid', 'gclid', '_', 'cb', 'cachebuster', 'rand',
})

def normalize_url_for_hash(url):
    """Normalize a URL for hashing by stripping tracking params and sorting query params."""
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        # Remove known tracking/session parameters
        filtered = {k: sorted(v) for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
        # Rebuild with sorted params for deterministic ordering
        normalized_query = urlencode(filtered, doseq=True)
        normalized = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.params,
            normalized_query,
            '',  # strip fragment
        ))
        return normalized
    except Exception:
        return url

def url_resume_hash(url):
    """Compute a SHA-256 hash of the normalized URL for resume matching."""
    normalized = normalize_url_for_hash(url)
    return hashlib.sha256(normalized.encode('utf-8', errors='replace')).hexdigest()[:16]

def extract_resume_identifier(entry_text):
    """Extract hash and URL from a resume log entry. Returns (hash, url) or (None, raw_text)."""
    if not entry_text:
        return None, ""
    text = entry_text.strip()
    # New format: "hash|url"
    if '|' in text:
        parts = text.split('|', 1)
        return parts[0].strip(), parts[1].strip()
    # Legacy format: just the URL
    if '://' in text:
        for token in reversed(text.split()):
            if '://' in token:
                return None, token.strip()
    return None, text

def load_processed_channels(log_file):
    """Load processed channels from resume log. Supports both hash|url and legacy URL formats."""
    processed_hashes = set()
    processed_urls = set()
    processed_channel_indices = {}
    last_index = 0
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                parts = line.rstrip('\n').split(' - ', 1)
                if len(parts) > 1:
                    parsed_index = None
                    index_source = parts[0].strip()
                    if index_source:
                        index_tokens = index_source.split()
                        if index_tokens:
                            index_part = index_tokens[0]
                            if index_part.isdigit():
                                parsed_index = int(index_part)
                                last_index = max(last_index, parsed_index)
                    entry_hash, entry_url = extract_resume_identifier(parts[1].strip())
                    if entry_hash:
                        processed_hashes.add(entry_hash)
                        if parsed_index is not None:
                            previous_index = processed_channel_indices.get(entry_hash, 0)
                            processed_channel_indices[entry_hash] = max(previous_index, parsed_index)
                    if entry_url:
                        processed_urls.add(entry_url)
                        if not entry_hash and parsed_index is not None:
                            previous_index = processed_channel_indices.get(entry_url, 0)
                            processed_channel_indices[entry_url] = max(previous_index, parsed_index)
    return processed_hashes, processed_urls, last_index, processed_channel_indices

def write_log_entry(log_file, entry):
    with open(log_file, 'a', encoding='utf-8', errors='replace') as f:
        f.write(entry + "\n")

class CheckpointWriter:
    def __init__(self, log_file, flush_interval=0.25, flush_threshold=128):
        self._log_file = log_file
        self._flush_interval = flush_interval
        self._flush_threshold = flush_threshold
        self._buffer = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()

    def write(self, entry):
        with self._lock:
            self._buffer.append(entry)
            now = time.monotonic()
            if len(self._buffer) >= self._flush_threshold or (now - self._last_flush) >= self._flush_interval:
                self._flush_locked()

    def _flush_locked(self):
        if not self._buffer:
            return
        try:
            with open(self._log_file, 'a', encoding='utf-8', errors='replace') as f:
                for entry in self._buffer:
                    f.write(entry + "\n")
        except OSError as exc:
            logging.error(f"Failed to flush checkpoint log '{self._log_file}': {exc}")
        self._buffer.clear()
        self._last_flush = time.monotonic()

    def flush(self):
        with self._lock:
            self._flush_locked()

    def close(self):
        self.flush()

class UrlDeduplicator:
    def __init__(self):
        self._lock = threading.Lock()
        self._results = {}
        self._pending = {}

    def get_or_start(self, url):
        with self._lock:
            if url in self._results:
                return 'cached', self._results[url]
            if url in self._pending:
                return 'waiting', self._pending[url]
            event = threading.Event()
            self._pending[url] = event
            return 'check', None

    def set_result(self, url, result):
        with self._lock:
            self._results[url] = result
            event = self._pending.pop(url, None)
        if event:
            event.set()

    def get_result(self, url):
        with self._lock:
            return self._results.get(url)

def sanitize_csv_field(value):
    if value is None:
        return ""
    normalized = str(value).replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')
    check_value = normalized.lstrip()
    if check_value.startswith(('=', '+', '-', '@')):
        return "'" + normalized
    return normalized

def file_log_entry(f_output, playlist_file, current_channel, total_channels, group_name, channel_name, channel_id, status, codec_name, video_bitrate, resolution, fps, audio_info, error_reason=None):
    if f_output is None:
        return
    safe_playlist = sanitize_csv_field(playlist_file)
    safe_status = sanitize_csv_field(status)
    safe_group = sanitize_csv_field(group_name)
    safe_channel = sanitize_csv_field(channel_name)
    codec_field = sanitize_csv_field(codec_name if codec_name else "Unknown")
    bitrate_field = video_bitrate.replace("kbps", "").strip() if isinstance(video_bitrate, str) else video_bitrate
    if not bitrate_field:
        bitrate_field = "Unknown"
    bitrate_field = sanitize_csv_field(bitrate_field)
    resolution_field = sanitize_csv_field(resolution)
    fps_field = fps if fps is not None else ""
    audio_field = sanitize_csv_field(audio_info if audio_info else "Unknown")
    channel_id_field = sanitize_csv_field(channel_id if channel_id else "Unknown")
    error_field = sanitize_csv_field(error_reason) if error_reason else ""
    csv.writer(f_output, lineterminator='\n').writerow([
        safe_playlist,
        current_channel,
        total_channels,
        safe_status,
        safe_group,
        safe_channel,
        channel_id_field,
        codec_field,
        bitrate_field,
        resolution_field,
        fps_field,
        audio_field,
        error_field
    ])
    f_output.flush()

def console_log_entry(playlist_file, current_channel, total_channels, channel_name, status, video_info, audio_info, max_name_length, use_padding):
    # Set colors and symbols based on status
    if status == 'Alive':
        color = "\033[92m"  # Green
        status_symbol = '✓'
    elif 'Geoblocked' in status:
        color = "\033[93m"  # Yellow
        status_symbol = '🔒'  # Lock emoji
    else:  # Dead
        color = "\033[91m"  # Red
        status_symbol = '✕'
    
    if use_padding:
        name_padding = ' ' * (max_name_length - len(channel_name) + 3)  # +3 for additional spaces
    else:
        name_padding = ''
    
    if status == 'Alive':
        prefix = f"{playlist_file}| " if playlist_file else ""
        print(f"{color}{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}{name_padding} | Video: {video_info} - Audio: {audio_info}\033[0m")
        logging.info(f"{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}{name_padding} | Video: {video_info} - Audio: {audio_info}")
    elif 'Geoblocked' in status:
        geoblock_info = f" [{status}]" if 'Confirmed' in status or 'Unconfirmed' in status else " [Geoblocked]"
        if use_padding:
            prefix = f"{playlist_file}| " if playlist_file else ""
            print(f"{color}{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}{name_padding} |{geoblock_info}\033[0m")
            logging.info(f"{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}{name_padding} |{geoblock_info}")
        else:
            prefix = f"{playlist_file}| " if playlist_file else ""
            print(f"{color}{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}{geoblock_info}\033[0m")
            logging.info(f"{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}{geoblock_info}")
    else:  # Dead
        if use_padding:
            prefix = f"{playlist_file}| " if playlist_file else ""
            print(f"{color}{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}{name_padding} |\033[0m")
            logging.info(f"{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}{name_padding} |")
        else:
            prefix = f"{playlist_file}| " if playlist_file else ""
            print(f"{color}{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}\033[0m")
            logging.info(f"{prefix}{current_channel}/{total_channels} {status_symbol} {channel_name}")

def parse_m3u8_files(playlists, config):
    if not playlists:
        logging.error("No playlists to process.")
        return

    group_title = config.group_title
    timeout = config.timeout
    extended_timeout = config.extended_timeout
    split = config.split
    rename = config.rename
    skip_screenshots = config.skip_screenshots
    output_file = config.output_file
    channel_search = config.channel_search
    channel_pattern = config.channel_pattern
    proxy_list = config.proxy_list
    test_geoblock = config.test_geoblock
    profile_bitrate = config.profile_bitrate
    ffmpeg_available = config.ffmpeg_available
    ffprobe_available = config.ffprobe_available
    backoff = config.backoff
    retries = config.retries
    workers = config.workers
    insecure = config.insecure
    filter_min_res = config.filter_min_res
    output_playlist = config.output_playlist

    session = requests.Session()
    session.headers.update({'User-Agent': 'VLC/3.0.14 LibVLC/3.0.14'})
    if insecure:
        session.verify = False
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    adapter = HTTPAdapter(pool_maxsize=20)
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    group_suffix = group_title.replace('|', '').replace(' ', '') if group_title else 'AllGroups'
    if channel_pattern is not None:
        pattern = channel_pattern
    else:
        try:
            pattern = compile_channel_pattern(channel_search)
        except ValueError as exc:
            logging.error(str(exc))
            return
    console_width = shutil.get_terminal_size((80, 20)).columns

    low_framerate_channels = []
    mislabeled_channels = []
    geoblocked_summary = {}
    error_summary = {}
    url_dedup = UrlDeduplicator()

    f_output = None
    if output_file:
        output_dir = os.path.dirname(output_file)
        if output_dir:
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as exc:
                logging.error(f"Failed to create output directory '{output_dir}': {exc}")
                output_file = None
        if output_file:
            try:
                f_output = codecs.open(output_file, "w", "utf-8-sig")
                f_output.write("Playlist,Channel Number,Total Channels in Playlist,Channel Status,Group Name,Channel Name,Channel ID,Codec,Bit Rate (kbps),Resolution,Frame Rate,Audio,Error Reason\n")
            except OSError as exc:
                logging.error(f"Unable to open output file '{output_file}': {exc}")
                f_output = None

    # Initialize filtered entries list (global across playlists)
    all_filtered_entries = []
    all_seen_urls = set()
    filtered_lock = threading.Lock()

    for file_path in playlists:
        playlist_file = os.path.basename(file_path)
        base_playlist_name = os.path.splitext(playlist_file)[0]
        playlist_dir = os.path.dirname(file_path) or '.'
        logging.info(f"Loading channels from {file_path} with group '{group_title}' and search '{channel_search if channel_search else 'None'}'...")

        output_folder = None
        if not skip_screenshots:
            output_folder = os.path.join(playlist_dir, f"{base_playlist_name}_{group_suffix}_screenshots")
            try:
                os.makedirs(output_folder, exist_ok=True)
            except OSError as exc:
                logging.error(f"Failed to create output folder '{output_folder}': {exc}")
                output_folder = None

        log_file = os.path.join(playlist_dir, f"{base_playlist_name}_{group_suffix}_checklog.txt")
        processed_hashes, processed_urls, last_index, processed_channel_indices = load_processed_channels(log_file)
        try:
            with open(log_file, 'w', encoding='utf-8', errors='replace'):
                pass
        except OSError as exc:
            logging.error(f"Failed to truncate resume log '{log_file}': {exc}")
        current_channel = last_index
        written_resume_entries = set()
        working_channels = []
        dead_channels = []
        geoblocked_channels = []

        total_channels = 0
        max_name_length = 0
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as file:
                for raw_line in file:
                    line = raw_line.strip()
                    if is_line_needed(line, group_title, pattern):
                        total_channels += 1
                        channel_name = get_channel_name(line)
                        max_name_length = max(max_name_length, len(channel_name))
        except FileNotFoundError:
            logging.error(f"M3U file not found: {file_path}. Please check the path and try again.")
            continue
        except PermissionError:
            logging.error(f"Permission denied: Cannot read M3U file '{file_path}'")
            continue
        except Exception as exc:
            logging.error(f"Failed to read M3U file '{file_path}': {exc}")
            continue

        logging.info(f"{playlist_file}: Total channels matching selection: {total_channels}\n")

        max_line_length = max_name_length + len("1/5 ✓ | Video: 1080p50 H264 - Audio: 160 kbps AAC") + 3
        use_padding = max_line_length <= console_width

        renamed_lines = [] if rename else None
        pending_extinf = None
        pending_channel_name = None
        pending_metadata_lines = []
        pending_selected = False
        checkpoint_writer = CheckpointWriter(log_file)
        entries_to_check = []

        def write_resume_entry(stream_hash, stream_url, channel_index):
            if not stream_hash or stream_hash in written_resume_entries:
                return
            checkpoint_writer.write(f"{channel_index} - {stream_hash}|{stream_url}")
            written_resume_entries.add(stream_hash)

        def append_pending_entry(extinf_line, metadata_lines, stream_line=None):
            if renamed_lines is None:
                return
            renamed_lines.append(extinf_line)
            renamed_lines.extend(metadata_lines)
            if stream_line is not None:
                renamed_lines.append(stream_line)

        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as file:
                for raw_line in file:
                    line = raw_line.strip()

                    if pending_extinf is None:
                        if line.startswith('#EXTINF'):
                            pending_extinf = line
                            pending_channel_name = get_channel_name(line)
                            pending_selected = is_line_needed(line, group_title, pattern)
                            pending_metadata_lines = []
                        else:
                            if renamed_lines is not None:
                                renamed_lines.append(line)
                        continue

                    if line.startswith('#EXTINF'):
                        if pending_selected:
                            logging.warning(f"No stream URL found for channel '{pending_channel_name}' in {playlist_file}")
                        append_pending_entry(pending_extinf, pending_metadata_lines)
                        pending_extinf = line
                        pending_channel_name = get_channel_name(line)
                        pending_selected = is_line_needed(line, group_title, pattern)
                        pending_metadata_lines = []
                        continue

                    if not line or line.startswith('#'):
                        pending_metadata_lines.append(line)
                        continue

                    stream_line = line
                    output_extinf_line = pending_extinf
                    channel_name = pending_channel_name
                    channel_metadata_lines = pending_metadata_lines

                    if pending_selected:
                        stream_hash = url_resume_hash(stream_line)
                        already_processed = stream_hash in processed_hashes or stream_line in processed_urls
                        if not already_processed:
                            current_channel += 1
                            entry = {
                                'channel_index': current_channel,
                                'extinf_line': pending_extinf,
                                'channel_name': channel_name,
                                'metadata_lines': list(channel_metadata_lines),
                                'stream_line': stream_line,
                                'group_value': get_group_name(pending_extinf),
                                'channel_id': get_channel_id(stream_line),
                                'result': None,
                            }
                            if renamed_lines is not None:
                                entry['renamed_line_idx'] = len(renamed_lines)
                            entries_to_check.append(entry)
                            processed_hashes.add(stream_hash)
                            processed_channel_indices[stream_hash] = current_channel
                        else:
                            logging.debug(f"Skipping previously processed channel: {channel_name}")
                            resume_index = processed_channel_indices.get(stream_hash) or processed_channel_indices.get(stream_line)
                            if resume_index is None:
                                resume_index = max(1, current_channel)
                                processed_channel_indices[stream_hash] = resume_index
                            write_resume_entry(stream_hash, stream_line, resume_index)

                    append_pending_entry(output_extinf_line, channel_metadata_lines, stream_line)
                    pending_extinf = None
                    pending_channel_name = None
                    pending_selected = False
                    pending_metadata_lines = []
        except FileNotFoundError:
            logging.error(f"M3U file not found: {file_path}. Please check the path and try again.")
            checkpoint_writer.close()
            continue
        except PermissionError:
            logging.error(f"Permission denied: Cannot read M3U file '{file_path}'")
            checkpoint_writer.close()
            continue
        except Exception as exc:
            logging.error(f"Failed to parse M3U file '{file_path}': {exc}")
            checkpoint_writer.close()
            continue

        if pending_extinf is not None:
            if pending_selected:
                logging.warning(f"No stream URL found for channel '{pending_channel_name}' in {playlist_file}")
            append_pending_entry(pending_extinf, pending_metadata_lines)

        # Concurrent check phase
        print_lock = threading.Lock()
        diag_semaphore = threading.Semaphore(min(workers, 4))

        def check_channel_worker(check_entry):
            if cancel_event.is_set():
                return {
                    'status': 'Dead', 'stream_url': None, 'target_url': None,
                    'video_info': 'Unknown', 'audio_info': 'Unknown',
                    'codec_name': 'Unknown', 'video_bitrate': 'Unknown',
                    'resolution': 'Unknown', 'fps': None, 'error_reason': 'Cancelled',
                }
            s_line = check_entry['stream_line']
            action, cached = url_dedup.get_or_start(s_line)
            if action == 'cached':
                logging.debug(f"Reusing cached check result for duplicate URL: {s_line}")
                return cached
            if action == 'waiting':
                logging.debug(f"Waiting for in-progress check of duplicate URL: {s_line}")
                cached.wait()
                return url_dedup.get_result(s_line)

            result = None
            try:
                status, stream_url, check_reason = check_channel_status(
                    s_line, timeout, retries=retries,
                    extended_timeout=extended_timeout,
                    proxy_list=proxy_list, test_geoblock=test_geoblock,
                    ffmpeg_available=ffmpeg_available, backoff=backoff,
                    session=session
                )

                target_url = (stream_url or s_line) if status == 'Alive' else None
                video_info = "Unknown"
                audio_info = "Unknown"
                codec_name = "Unknown"
                video_bitrate = "Unknown"
                resolution = "Unknown"
                fps = None

                if status == 'Alive' and ffprobe_available and target_url:
                    with diag_semaphore:
                        codec_name, video_bitrate, resolution, fps = get_detailed_stream_info(
                            target_url, profile_bitrate=profile_bitrate and ffmpeg_available
                        )
                        video_info = format_stream_info(codec_name, video_bitrate, resolution, fps)
                        audio_info = get_audio_bitrate(target_url)

                if status == 'Alive' and not skip_screenshots and output_folder and ffmpeg_available:
                    with diag_semaphore:
                        file_name = build_screenshot_filename(output_folder, check_entry['channel_index'], check_entry['channel_name'])
                        capture_frame(target_url or s_line, output_folder, file_name)

                result = {
                    'status': status, 'stream_url': stream_url, 'target_url': target_url,
                    'video_info': video_info, 'audio_info': audio_info,
                    'codec_name': codec_name, 'video_bitrate': video_bitrate,
                    'resolution': resolution, 'fps': fps, 'error_reason': check_reason,
                }
            except Exception as worker_exc:
                result = {
                    'status': 'Dead', 'stream_url': None, 'target_url': None,
                    'video_info': 'Unknown', 'audio_info': 'Unknown',
                    'codec_name': 'Unknown', 'video_bitrate': 'Unknown',
                    'resolution': 'Unknown', 'fps': None,
                    'error_reason': summarize_error(worker_exc),
                }
                raise
            finally:
                if result is not None:
                    url_dedup.set_result(s_line, result)
            return result

        cancelled = False
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(check_channel_worker, e): e for e in entries_to_check}
            try:
                for future in as_completed(future_map):
                    if cancel_event.is_set():
                        for pending in future_map:
                            pending.cancel()
                        cancelled = True
                        break

                    check_entry = future_map[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        logging.error(f"Error checking channel '{check_entry['channel_name']}': {summarize_error(exc)}")
                        result = {
                            'status': 'Dead', 'stream_url': None, 'target_url': None,
                            'video_info': 'Unknown', 'audio_info': 'Unknown',
                            'codec_name': 'Unknown', 'video_bitrate': 'Unknown',
                            'resolution': 'Unknown', 'fps': None,
                            'error_reason': summarize_error(exc),
                        }

                    check_entry['result'] = result
                    status = result['status']

                    with print_lock:
                        if status == 'Alive' and ffprobe_available:
                            mismatches = check_label_mismatch(check_entry['channel_name'], result['resolution'])
                            if result['fps'] is not None and result['fps'] < 29:
                                low_framerate_channels.append(
                                    f"{playlist_file}: {check_entry['channel_index']}/{total_channels} {check_entry['channel_name']} - \033[91m{result['fps']}fps\033[0m"
                                )
                            if mismatches:
                                mislabeled_channels.append(
                                    f"{playlist_file}: {check_entry['channel_index']}/{total_channels} {check_entry['channel_name']} - {', '.join(mismatches)}"
                                )

                        if 'Geoblocked' in status:
                            geoblocked_summary[playlist_file] = geoblocked_summary.get(playlist_file, 0) + 1
                        elif status == 'Dead':
                            reason = result.get('error_reason') or 'Unknown'
                            error_summary[reason] = error_summary.get(reason, 0) + 1

                        console_log_entry(
                            playlist_file, check_entry['channel_index'], total_channels,
                            check_entry['channel_name'], status, result['video_info'], result['audio_info'],
                            max_name_length, use_padding
                        )
                        file_log_entry(
                            f_output, playlist_file, check_entry['channel_index'], total_channels,
                            check_entry['group_value'], check_entry['channel_name'], check_entry['channel_id'],
                            status, result['codec_name'], result['video_bitrate'],
                            result['resolution'], result['fps'], result['audio_info'],
                            error_reason=result.get('error_reason')
                        )

                        # --- Filtering by resolution ---
                        stream_url = check_entry['stream_line']
                        if stream_url.startswith('udp://'):
                            # UDP streams always considered alive and meet resolution requirement
                            keep = True
                        else:
                            if filter_min_res:
                                res = result.get('resolution', 'Unknown')
                                keep = False
                                if filter_min_res == '720p' and res in ['720p', '1080p', '4K']:
                                    keep = True
                                elif filter_min_res == '1080p' and res in ['1080p', '4K']:
                                    keep = True
                                elif filter_min_res == '4K' and res == '4K':
                                    keep = True
                            else:
                                keep = (status == 'Alive')

                        # Thêm vào danh sách lọc nếu thỏa mãn
                        if keep:
                            with filtered_lock:
                                if stream_url not in all_seen_urls:
                                    all_seen_urls.add(stream_url)
                                    all_filtered_entries.append((
                                        check_entry['extinf_line'],
                                        list(check_entry['metadata_lines']),
                                        stream_url
                                    ))

                    write_resume_entry(url_resume_hash(check_entry['stream_line']), check_entry['stream_line'], check_entry['channel_index'])
            except KeyboardInterrupt:
                cancel_event.set()
                for pending in future_map:
                    pending.cancel()
                cancelled = True
                logging.info("Cancelling remaining checks...")

        if cancelled:
            checkpoint_writer.close()
            cleanup_active_subprocesses()
            if f_output:
                f_output.close()
            session.close()
            print("\n\033[93mInterrupted. Checkpoint saved for resume.\033[0m")
            sys.exit(130)

        # Post-processing: build split lists and patch rename in original order
        for check_entry in entries_to_check:
            result = check_entry.get('result', {})
            status = result.get('status', 'Dead')
            output_extinf = check_entry['extinf_line']
            metadata_lines = check_entry['metadata_lines']
            s_line = check_entry['stream_line']

            if status == 'Alive' and rename and renamed_lines is not None:
                video_info = result.get('video_info', 'Unknown')
                audio_info = result.get('audio_info', 'Unknown')
                renamed_channel_name = f"{check_entry['channel_name']} ({video_info} | Audio: {audio_info})"
                extinf_parts = output_extinf.split(',', 1)
                if len(extinf_parts) > 1:
                    extinf_parts[1] = renamed_channel_name
                    output_extinf = ','.join(extinf_parts)
                if 'renamed_line_idx' in check_entry:
                    renamed_lines[check_entry['renamed_line_idx']] = output_extinf

            if split:
                entry_lines = [output_extinf, *metadata_lines, s_line]
                if status == 'Alive':
                    working_channels.append(entry_lines)
                elif 'Geoblocked' in status:
                    geoblocked_channels.append(entry_lines)
                else:
                    dead_channels.append(entry_lines)

        checkpoint_writer.close()

        if split:
            working_playlist_path = os.path.join(playlist_dir, f"{base_playlist_name}_working.m3u8")
            dead_playlist_path = os.path.join(playlist_dir, f"{base_playlist_name}_dead.m3u8")
            geoblocked_playlist_path = os.path.join(playlist_dir, f"{base_playlist_name}_geoblocked.m3u8")

            if working_channels:
                with open(working_playlist_path, 'w', encoding='utf-8') as working_file:
                    working_file.write("#EXTM3U\n")
                    for entry in working_channels:
                        for entry_line in entry:
                            working_file.write(entry_line + "\n")
                logging.info(f"Working channels playlist saved to {working_playlist_path}")

            if dead_channels:
                with open(dead_playlist_path, 'w', encoding='utf-8') as dead_file:
                    dead_file.write("#EXTM3U\n")
                    for entry in dead_channels:
                        for entry_line in entry:
                            dead_file.write(entry_line + "\n")
                logging.info(f"Dead channels playlist saved to {dead_playlist_path}")

            if geoblocked_channels:
                with open(geoblocked_playlist_path, 'w', encoding='utf-8') as geoblocked_file:
                    geoblocked_file.write("#EXTM3U\n")
                    for entry in geoblocked_channels:
                        for entry_line in entry:
                            geoblocked_file.write(entry_line + "\n")
                logging.info(f"Geoblocked channels playlist saved to {geoblocked_playlist_path}")
        if rename and renamed_lines:
            renamed_playlist_path = os.path.join(playlist_dir, f"{base_playlist_name}_renamed.m3u8")
            with open(renamed_playlist_path, 'w', encoding='utf-8') as renamed_file:
                has_header = any(entry.upper().startswith("#EXTM3U") for entry in renamed_lines if entry)
                if not has_header:
                    renamed_file.write("#EXTM3U\n")
                for line in renamed_lines:
                    renamed_file.write(line + "\n")
            logging.info(f"Renamed playlist saved to {renamed_playlist_path}")

    session.close()

    if f_output:
        f_output.close()

    # Write filtered playlist (global)
    if output_playlist and all_filtered_entries:
        # Resolve output path
        if not os.path.isabs(output_playlist):
            # Use directory of first input playlist as base
            base_dir = os.path.dirname(playlists[0]) if playlists else '.'
            output_playlist = os.path.join(base_dir, output_playlist)
        output_dir = os.path.dirname(output_playlist)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(output_playlist, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            for extinf, metadata, url in all_filtered_entries:
                f.write(extinf + "\n")
                for meta in metadata:
                    if meta:
                        f.write(meta + "\n")
                f.write(url + "\n")
        logging.info(f"Filtered playlist saved to {output_playlist}")

    if low_framerate_channels:
        print("\n\033[93mLow Framerate Channels:\033[0m")
        for entry in low_framerate_channels:
            print(entry)
        logging.info("Low Framerate Channels Detected:")
        for entry in low_framerate_channels:
            logging.info(entry)

    if mislabeled_channels:
        print("\n\033[93mMislabeled Channels:\033[0m")
        for entry in mislabeled_channels:
            print(entry)
        logging.info("Mislabeled Channels Detected:")
        for entry in mislabeled_channels:
            logging.info(entry)

    if geoblocked_summary:
        print("\n\033[93mGeoblocked Channels Summary:\033[0m")
        for playlist_file, count in geoblocked_summary.items():
            print(f"{playlist_file}: {count} channels detected")
            logging.info(f"{playlist_file}: {count} geoblocked channels detected")

    if error_summary:
        total_dead = sum(error_summary.values())
        print(f"\n\033[93mDead Channels Breakdown ({total_dead} total):\033[0m")
        for reason, count in sorted(error_summary.items(), key=lambda x: x[1], reverse=True):
            print(f"  {reason}: {count}")
            logging.info(f"Dead channels - {reason}: {count}")


def main():
    parser = argparse.ArgumentParser(description="Check the status of channels in an IPTV M3U8 playlist and capture frames of live channels.")
    parser.add_argument("playlist", type=str, help="Path to the M3U8 playlist file")
    parser.add_argument("-group", "-g", type=str, default=None, help="Specific group title to check within the playlist")
    parser.add_argument("-timeout", "-t", type=float, default=10.0, help="Timeout in seconds for checking channel status")
    parser.add_argument("-v", action="count", default=0, help="Increase output verbosity (-v for info, -vv for debug)")
    parser.add_argument("-extended", "-e", type=int, nargs='?', const=10, default=None, help="Enable extended timeout check for dead channels. Default is 10 seconds if used without specifying time.")
    parser.add_argument("-split", "-s", action="store_true", help="Create separate playlists for working, dead, and geoblocked channels")
    parser.add_argument("-rename", "-r", action="store_true", help="Rename alive channels to include video and audio info")
    parser.add_argument("-proxy-list", "-p", type=str, default=None, help="Path to proxy list file for geoblock testing")
    parser.add_argument("-test-geoblock", "-tg", action="store_true", help="Test geoblocked streams with proxies to confirm geoblocking")
    parser.add_argument("--retries", "-R", type=int, default=6, help="Number of stream-check attempts (0-10)")
    parser.add_argument("-output", "-o", type=str, default=None, help="Write channel details to CSV at the provided path")
    parser.add_argument("-channel_search", "-c", type=str, default=None, help="Regex used to filter channels by name (case-insensitive)")
    parser.add_argument("-skip_screenshots", action="store_true", help="Skip capturing screenshots for alive channels")
    parser.add_argument("--profile-bitrate", "-b", action="store_true", help="Profile average video bitrate (slower, uses a 10-second ffmpeg sample)")
    parser.add_argument("--backoff", "-B", type=str, choices=["none", "linear", "exponential"], default="linear", help="Retry backoff strategy: none, linear (1s,2s,3s...), exponential (1s,2s,4s...)")
    parser.add_argument("--workers", "-w", type=int, default=4, help="Number of concurrent workers for channel checking (1-20, default: 4)")
    parser.add_argument("--insecure", "-k", action="store_true", help="Disable SSL certificate verification for HTTPS streams")
    parser.add_argument("--filter-min-res", type=str, choices=['720p', '1080p', '4K'], default=None,
                        help="Only keep channels with resolution at least this value (720p, 1080p, 4K). If not set, keep all alive channels.")
    parser.add_argument("--output-playlist", type=str, default=None,
                        help="Output filtered playlist file (e.g., live_schedule_check.m3u). If not set, no filtered playlist is written.")

    args = parser.parse_args()

    if not 0.5 <= args.timeout <= 300:
        parser.error("`-timeout/--timeout` must be between 0.5 and 300 seconds.")
    if args.extended is not None and not 1 <= args.extended <= 600:
        parser.error("`-extended/--extended` must be between 1 and 600 seconds.")
    if not 0 <= args.retries <= 10:
        parser.error("`--retries/-R` must be between 0 and 10.")
    if not 1 <= args.workers <= 20:
        parser.error("`--workers/-w` must be between 1 and 20.")

    try:
        channel_pattern = compile_channel_pattern(args.channel_search)
    except ValueError as exc:
        parser.error(str(exc))

    setup_logging(args.v)

    # Check for ffmpeg and ffprobe availability
    tool_status = check_ffmpeg_availability()
    ffmpeg_available = tool_status.get('ffmpeg', False)
    ffprobe_available = tool_status.get('ffprobe', False)
    if not (ffmpeg_available and ffprobe_available):
        logging.warning("ffmpeg and/or ffprobe not found. Some features will be disabled.")
        print("\033[93mWarning: ffmpeg and/or ffprobe not found. Screenshot capture and media info detection will be disabled.\033[0m")
    if args.profile_bitrate and not ffmpeg_available:
        logging.error("Disabling args.profile_bitrate because ffmpeg_available is False.")
        print("\033[93mWarning: args.profile_bitrate disabled because ffmpeg_available is False.\033[0m")
        args.profile_bitrate = False

    if args.insecure:
        logging.warning("SSL certificate verification is disabled.")
        print("\033[93mWarning: SSL certificate verification is disabled (--insecure).\033[0m")

    # Load proxy list if provided
    proxy_list = None
    if args.proxy_list:
        proxy_path = os.path.expanduser(args.proxy_list)
        proxy_list = load_proxy_list(proxy_path)
        if proxy_list:
            logging.info(f"Loaded {len(proxy_list)} proxies from {proxy_path}")
        else:
            logging.error(f"No valid proxies loaded from {proxy_path}. Aborting.")
            return

    playlist_input = os.path.expanduser(args.playlist)
    playlists = []
    if os.path.isdir(playlist_input):
        for entry in sorted(os.listdir(playlist_input)):
            full_path = os.path.join(playlist_input, entry)
            if os.path.isfile(full_path) and entry.lower().endswith((".m3u", ".m3u8")):
                playlists.append(full_path)
    else:
        if os.path.isfile(playlist_input):
            playlists.append(playlist_input)
        else:
            logging.error(f"Playlist path not found: {playlist_input}")
            return

    if not playlists:
        logging.error("No playlist files found to process.")
        return

    for playlist in playlists:
        logging.info(f"Will process playlist: {playlist}")

    output_file = os.path.expanduser(args.output) if args.output else None

    config = ScanConfig(
        group_title=args.group,
        timeout=args.timeout,
        extended_timeout=args.extended,
        split=args.split,
        rename=args.rename,
        skip_screenshots=args.skip_screenshots,
        output_file=output_file,
        channel_search=args.channel_search,
        channel_pattern=channel_pattern,
        proxy_list=proxy_list,
        test_geoblock=args.test_geoblock,
        profile_bitrate=args.profile_bitrate,
        ffmpeg_available=ffmpeg_available,
        ffprobe_available=ffprobe_available,
        backoff=args.backoff,
        retries=args.retries,
        workers=args.workers,
        insecure=args.insecure,
        filter_min_res=args.filter_min_res,
        output_playlist=args.output_playlist,
    )
    parse_m3u8_files(playlists, config)


if __name__ == "__main__":
    main()
