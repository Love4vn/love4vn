import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import subprocess

# ============================================
# CONFIGURATION - Điều chỉnh các settings ở đây
# ============================================

# Timeout settings (giây)
URL_CHECK_TIMEOUT = 10          # Timeout cho check URL working
RESOLUTION_CHECK_TIMEOUT = 20   # Timeout cho check resolution (tăng lên nếu nhiều timeout)

# Worker threads
URL_CHECK_WORKERS = 100         # Số thread đồng thời cho check URL
RESOLUTION_CHECK_WORKERS = 10   # Số thread đồng thời cho check resolution (giảm để ổn định hơn)

# Skip options
SKIP_DEAD_CHECK = False         # True = giữ tất cả channels không check URL
SKIP_RESOLUTION_CHECK = False   # True = bỏ qua check resolution

# ============================================

def is_channel_working(url, timeout=URL_CHECK_TIMEOUT):
    """
    Improved channel checking with multiple methods
    """
    try:
        # Try HEAD request first (faster)
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        if response.status_code == 200:
            return True
        
        # If HEAD fails, try GET request (some servers don't support HEAD)
        response = requests.get(url, timeout=timeout, stream=True, allow_redirects=True)
        # Accept any 2xx or 3xx status code
        return 200 <= response.status_code < 400
        
    except requests.RequestException:
        return False

def get_video_resolution(url, timeout=RESOLUTION_CHECK_TIMEOUT):
    """
    Get video resolution using ffprobe with robust error handling
    """
    try:
        # Use ffprobe for faster detection
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0', 
             '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', url],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            timeout=timeout
        )
        output = result.stdout.decode('utf-8').strip()
        
        # Clean output - remove any whitespace and newlines
        output = ''.join(output.split())
        
        if 'x' in output:
            # Extract only the first occurrence of WIDTHxHEIGHT pattern
            match = re.search(r'(\d+)x(\d+)', output)
            if match:
                width_str, height_str = match.groups()
                
                # Validate before converting
                if width_str.isdigit() and height_str.isdigit():
                    width, height = int(width_str), int(height_str)
                    
                    # Sanity check: reasonable resolution ranges
                    if 100 <= width <= 10000 and 100 <= height <= 10000:
                        if width >= 2560 or height >= 1440:
                            return "4K"
                        elif width >= 1920 or height >= 1080:
                            return "FHD"
                        elif width >= 1280 or height >= 720:
                            return "HD"
                        else:
                            return "SD"
        
        # Fallback to ffmpeg if ffprobe fails
        result = subprocess.run(
            ['ffmpeg', '-i', url, '-t', '2', '-hide_banner'],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            timeout=timeout
        )
        output = result.stderr.decode('utf-8')
        match = re.search(r'Stream.*Video.*\s(\d{3,5})x(\d{3,5})', output)
        if match:
            width, height = int(match.group(1)), int(match.group(2))
            if 100 <= width <= 10000 and 100 <= height <= 10000:
                if width >= 2560 or height >= 1440:
                    return "4K"
                elif width >= 1920 or height >= 1080:
                    return "FHD"
                elif width >= 1280 or height >= 720:
                    return "HD"
                else:
                    return "SD"
                    
        return None
        
    except subprocess.TimeoutExpired:
        return None
    except (ValueError, AttributeError):
        return None
    except Exception:
        return None

def clean_name(name):
    allowed_chars = re.compile(r'[^a-zA-Z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u1100-\u11ff\u3130-\u318f\u0E00-\u0E7F\u0400-\u04FF ;]+')
    return allowed_chars.sub('', name).strip()

def format_group_title(line):
    match = re.search(r'group-title="([^"]+)"', line)
    if match:
        group_title = match.group(1)
        group_title = re.sub(r'\s+', ' ', group_title)
        group_title = clean_name(group_title)
        line = line.replace(match.group(1), group_title)
    return line

def format_channel_name(line):
    match = re.search(r'#EXTINF[^,]*,(.*)', line)
    if match:
        channel_name = clean_name(match.group(1))
        line = line.replace(match.group(1), channel_name)
    return line

def parse_playlist(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    entries = []
    entry = []
    for line in lines:
        if line.startswith('#EXTINF'):
            line = format_group_title(line)
            line = format_channel_name(line)
            if entry:
                entries.append(entry)
            entry = [line]
        elif line.strip():
            entry.append(line)

    if entry:
        entries.append(entry)

    return entries

def remove_duplicates(entries):
    unique_entries = []
    seen_urls = set()
    for entry in entries:
        url = entry[-1].strip()
        if url not in seen_urls:
            seen_urls.add(url)
            unique_entries.append(entry)
    return unique_entries

def sort_entries(entries):
    def sort_key(entry):
        channel_name = entry[0].split(',')[-1].strip()
        url = entry[-1].strip()
        return (channel_name, url)
    return sorted(entries, key=sort_key)

def check_url(url):
    return url, is_channel_working(url)

def check_resolution(url):
    return url, get_video_resolution(url)

def check_and_filter_entries(entries, skip_dead_check=SKIP_DEAD_CHECK, skip_resolution_check=SKIP_RESOLUTION_CHECK):
    """
    Enhanced checking with options to skip certain checks
    """
    urls = [entry[-1].strip() for entry in entries]
    resolution_dict = {}

    if skip_dead_check:
        print("⏭️  Skipping dead channel check - keeping all channels...")
        valid_entries = entries
        valid_urls = urls
    else:
        print(f"🔍 Checking {len(urls)} URLs for availability...")
        with ThreadPoolExecutor(max_workers=URL_CHECK_WORKERS) as executor:
            results = list(tqdm(executor.map(check_url, urls), total=len(urls), desc="Checking Channels"))

        valid_entries = [entry for entry, (url, is_valid) in zip(entries, results) if is_valid]
        valid_urls = [entry[-1].strip() for entry in valid_entries]
        
        print(f"✅ Valid channels: {len(valid_entries)}/{len(urls)}")

    if not skip_resolution_check and valid_urls:
        print(f"\n📺 Checking resolutions for {len(valid_urls)} channels...")
        print(f"⚙️  Using {RESOLUTION_CHECK_WORKERS} workers, timeout: {RESOLUTION_CHECK_TIMEOUT}s")
        print("⏳ This may take several minutes, please be patient...")
        
        resolution_success = 0
        resolution_failed = 0
        
        with ThreadPoolExecutor(max_workers=RESOLUTION_CHECK_WORKERS) as executor:
            future_to_url = {executor.submit(check_resolution, url): url for url in valid_urls}
            for future in tqdm(as_completed(future_to_url), total=len(future_to_url), desc="Checking Resolutions"):
                url = future_to_url[future]
                try:
                    _, resolution = future.result()
                    if resolution:
                        resolution_dict[url] = resolution
                        resolution_success += 1
                    else:
                        resolution_failed += 1
                except Exception:
                    resolution_failed += 1

        print(f"📊 Resolution stats: {resolution_success} detected, {resolution_failed} failed/timeout")

        # Add resolution labels to channel names
        for entry in valid_entries:
            url = entry[-1].strip()
            if url in resolution_dict and resolution_dict[url]:
                resolution = resolution_dict[url]
                channel_name_match = re.search(r'#EXTINF[^,]*,(.*)', entry[0])
                if channel_name_match:
                    channel_name = channel_name_match.group(1)
                    # Remove existing resolution tag if present
                    channel_name = re.sub(r'\s*\([4K|FHD|HD|SD]+\)\s*$', '', channel_name)
                    new_channel_name = f"{channel_name} ({resolution})"
                    entry[0] = entry[0].replace(channel_name_match.group(1), new_channel_name)

    return valid_entries

def write_playlist(file_path, entries):
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write('#EXTM3U\n')
        for entry in entries:
            for line in entry:
                file.write(line)
            file.write('\n')

def main():
    input_path = "hubsport.m3u"
    output_path = "S_check.m3u"
    
    print("="*60)
    print("🎬 M3U Playlist Checker - Enhanced Version v2.0")
    print("="*60)
    
    print(f"\n⚙️  Configuration:")
    print(f"   - URL check timeout: {URL_CHECK_TIMEOUT}s")
    print(f"   - Resolution check timeout: {RESOLUTION_CHECK_TIMEOUT}s")
    print(f"   - URL workers: {URL_CHECK_WORKERS}")
    print(f"   - Resolution workers: {RESOLUTION_CHECK_WORKERS}")
    print(f"   - Skip dead check: {SKIP_DEAD_CHECK}")
    print(f"   - Skip resolution check: {SKIP_RESOLUTION_CHECK}")
    
    print("\n📂 Parsing playlist...")
    entries = parse_playlist(input_path)
    print(f"   Found {len(entries)} total entries")

    print("\n🔄 Removing duplicates...")
    unique_entries = remove_duplicates(entries)
    print(f"   After deduplication: {len(unique_entries)} entries")

    print("\n📋 Sorting entries...")
    sorted_entries = sort_entries(unique_entries)
    print("   Sorted alphabetically")

    print("\n🔍 Validating channels...")
    valid_entries = check_and_filter_entries(sorted_entries)

    print(f"\n💾 Writing {len(valid_entries)} channels to '{output_path}'...")
    write_playlist(output_path, valid_entries)
    
    print("\n" + "="*60)
    print("✅ Process completed successfully!")
    print("="*60)
    print(f"📄 Input file:  {input_path}")
    print(f"📄 Output file: {output_path}")
    print(f"📊 Total channels in output: {len(valid_entries)}")
    print("="*60)

if __name__ == '__main__':
    main()
