import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import subprocess

def is_channel_working(url, timeout=10):
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

def get_video_resolution(url, timeout=15):
    """
    Get video resolution using ffprobe (faster than ffmpeg)
    """
    try:
        # Use ffprobe instead of ffmpeg for faster detection
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0', 
             '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', url],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            timeout=timeout
        )
        output = result.stdout.decode('utf-8').strip()
        
        if 'x' in output:
            width, height = map(int, output.split('x'))
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
            ['ffmpeg', '-i', url, '-hide_banner'],
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            timeout=timeout
        )
        output = result.stderr.decode('utf-8')
        match = re.search(r'Stream.*Video.* (\d{2,5})x(\d{2,5})', output)
        if match:
            width, height = map(int, match.groups())
            if width >= 2560 or height >= 1440:
                return "4K"
            elif width >= 1920 or height >= 1080:
                return "FHD"
            elif width >= 1280 or height >= 720:
                return "HD"
            else:
                return "SD"
    except subprocess.TimeoutExpired:
        print(f"Timeout checking resolution for: {url[:50]}...")
        return None
    except Exception as e:
        print(f"Error checking resolution: {str(e)[:50]}")
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

def check_and_filter_entries(entries, skip_dead_check=False, skip_resolution_check=False):
    """
    Enhanced checking with options to skip certain checks
    
    Args:
        entries: List of playlist entries
        skip_dead_check: If True, skip URL validation (keeps all channels)
        skip_resolution_check: If True, skip resolution detection
    """
    urls = [entry[-1].strip() for entry in entries]
    valid_urls = set()
    resolution_dict = {}

    if skip_dead_check:
        print("Skipping dead channel check - keeping all channels...")
        valid_entries = entries
        valid_urls = urls
    else:
        print(f"Checking {len(urls)} URLs...")
        with ThreadPoolExecutor(max_workers=100) as executor:
            results = list(tqdm(executor.map(check_url, urls), total=len(urls), desc="Checking Channels"))

        valid_entries = [entry for entry, (url, is_valid) in zip(entries, results) if is_valid]
        valid_urls = [entry[-1].strip() for entry in valid_entries]
        
        print(f"Valid channels: {len(valid_entries)}/{len(urls)}")

    if not skip_resolution_check and valid_urls:
        print(f"Checking resolutions for {len(valid_urls)} channels...")
        with ThreadPoolExecutor(max_workers=20) as executor:
            future_to_url = {executor.submit(check_resolution, url): url for url in valid_urls}
            for future in tqdm(as_completed(future_to_url), total=len(future_to_url), desc="Checking Resolutions"):
                url = future_to_url[future]
                try:
                    _, resolution = future.result()
                    if resolution:
                        resolution_dict[url] = resolution
                except Exception as e:
                    print(f"Error: {str(e)[:50]}")
                    resolution_dict[url] = None

        for entry in valid_entries:
            url = entry[-1].strip()
            if url in resolution_dict and resolution_dict[url]:
                resolution = resolution_dict[url]
                channel_name_match = re.search(r'#EXTINF[^,]*,(.*)', entry[0])
                if channel_name_match:
                    channel_name = channel_name_match.group(1)
                    new_channel_name = f"{channel_name} ({resolution})"
                    entry[0] = entry[0].replace(channel_name, new_channel_name)

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
    
    print("="*50)
    print("M3U Playlist Checker - Enhanced Version")
    print("="*50)
    
    # Configuration options
    SKIP_DEAD_CHECK = False  # Set to True to keep all channels without checking
    SKIP_RESOLUTION_CHECK = False  # Set to True to skip resolution detection
    
    print("\nParsing playlist...")
    entries = parse_playlist(input_path)
    print(f"Found {len(entries)} total entries")

    print("\nRemoving duplicates...")
    unique_entries = remove_duplicates(entries)
    print(f"After removing duplicates: {len(unique_entries)} entries")

    print("\nSorting entries...")
    sorted_entries = sort_entries(unique_entries)

    print("\nChecking channels...")
    valid_entries = check_and_filter_entries(
        sorted_entries, 
        skip_dead_check=SKIP_DEAD_CHECK,
        skip_resolution_check=SKIP_RESOLUTION_CHECK
    )

    print(f"\nWriting {len(valid_entries)} channels to {output_path}...")
    write_playlist(output_path, valid_entries)
    print("\n" + "="*50)
    print("Process completed!")
    print(f"Output file: {output_path}")
    print(f"Total channels in output: {len(valid_entries)}")
    print("="*50)

if __name__ == '__main__':
    main()
