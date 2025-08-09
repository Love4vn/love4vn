import requests
import re
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
import subprocess
import time

# Configuration values
INPUT_PATH = "Grab_VTV.m3u"
OUTPUT_PATH = "Time_VTV.m3u"
TIMEOUT = 10  # Timeout for checking channel availability
FFPROBE_TIMEOUT = 60  # Timeout for ffprobe response time check
CHECK_CHANNEL_WORKING = False  # Flag to turn on/off channel working check

def is_channel_working(url, timeout=TIMEOUT):
    try:
        response = requests.head(url, timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False

def get_ffprobe_response_time(url, timeout=FFPROBE_TIMEOUT):
    try:
        start_time = time.time()
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'default=nw=1:nk=1', url],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout
        )
        if result.returncode == 0:
            response_time = time.time() - start_time
            return response_time
    except Exception:
        return None
    return None

def format_group_title(line):
    match = re.search(r'group-title="([^"]+)"', line)
    if match:
        group_title = match.group(1)
        group_title = re.sub(r'\s+', ' ', group_title)
        line = line.replace(match.group(1), group_title)
    return line

def parse_playlist(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        lines = file.readlines()

    entries = []
    entry = []
    for line in lines:
        if line.startswith('#EXTINF'):
            line = format_group_title(line)
            if entry:
                entries.append(entry)
            entry = [line]
        elif line.strip():
            entry.append(line)

    if entry:
        entries.append(entry)

    return entries

def remove_duplicates(entries):
    url_to_entry = {}
    for entry in entries:
        url = entry[-1].strip()
        url_to_entry[url] = entry
    return list(url_to_entry.values())

def sort_entries(entries):
    def sort_key(entry):
        # Check for tvg-id
        tvg_id_match = re.search(r'tvg-id="([^"]+)"', entry[0])
        tvg_id_filled = bool(tvg_id_match and tvg_id_match.group(1).strip())
        
        # Extract response time
        response_time_match = re.search(r'\((\d+\.\d+)s\)', entry[0])
        response_time = float(response_time_match.group(1)) if response_time_match else float('inf')
        
        return (not tvg_id_filled, response_time)
    
    return sorted(entries, key=sort_key)

def check_url(url):
    return url, is_channel_working(url)

def check_and_filter_entries(entries):
    if not CHECK_CHANNEL_WORKING:
        return entries

    urls = [entry[-1].strip() for entry in entries]

    with ThreadPoolExecutor(max_workers=1000) as executor:
        results = list(tqdm(executor.map(check_url, urls), total=len(urls), desc="Checking Channels"))

    valid_entries = [entry for entry, (url, is_valid) in zip(entries, results) if is_valid]

    return valid_entries

def append_ffprobe_time(entries):
    urls = [entry[-1].strip() for entry in entries]

    def process_url(url):
        response_time = get_ffprobe_response_time(url)
        return url, response_time

    with ThreadPoolExecutor(max_workers=100) as executor:
        results = list(tqdm(executor.map(process_url, urls), total=len(urls), desc="Getting FFprobe Response Times"))

    for entry, (url, response_time) in zip(entries, results):
        if response_time is not None:
            extinf_line = entry[0]
            channel_name_match = re.search(r'#EXTINF[^,]*,(.*)', extinf_line)
            if channel_name_match:
                channel_name = channel_name_match.group(1).strip()
                new_channel_name = f'{channel_name} ({response_time:.1f}s)'
                entry[0] = extinf_line.replace(channel_name, new_channel_name)

    return entries

def write_playlist(file_path, entries):
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write('#EXTM3U\n')
        for entry in entries:
            extinf_line = entry[0]
            url_line = entry[-1].strip()
            additional_lines = entry[1:-1]

            tvg_id = re.search(r'tvg-id="([^"]*)"', extinf_line)
            tvg_name = re.search(r'tvg-name="([^"]*)"', extinf_line)
            tvg_logo = re.search(r'tvg-logo="([^"]*)"', extinf_line)
            group_title = re.search(r'group-title="([^"]*)"', extinf_line)
            channel_name = re.search(r'#EXTINF[^,]*,(.*)', extinf_line)

            tvg_id_str = f'tvg-id="{tvg_id.group(1)}"' if tvg_id else ''
            tvg_name_str = f'tvg-name="{tvg_name.group(1)}"' if tvg_name else ''
            tvg_logo_str = f'tvg-logo="{tvg_logo.group(1)}"' if tvg_logo else ''
            group_title_str = f'group-title="{group_title.group(1)}"' if group_title else ''
            channel_name_str = channel_name.group(1).strip() if channel_name else ''

            attributes = [tvg_name_str, tvg_id_str, tvg_logo_str, group_title_str]
            attributes = [attr for attr in attributes if attr]  # Filter out empty strings
            formatted_extinf = f'#EXTINF:-1 {" ".join(attributes)},{channel_name_str}\n'

            file.write(formatted_extinf)
            
            for additional_line in additional_lines:
                file.write(additional_line.strip() + '\n')
            
            file.write(url_line + '\n\n')  # Add an extra newline for separation

def main():
    print("Parsing playlist...")
    entries = parse_playlist(INPUT_PATH)

    print("Removing duplicates...")
    entries = remove_duplicates(entries)

    # print("Standardizing group titles...")
    # entries = standardize_group_titles(entries)

    print("Getting FFprobe response times...")
    entries = append_ffprobe_time(entries)

    print("Sorting entries...")
    entries = sort_entries(entries)

    if CHECK_CHANNEL_WORKING:
        print("Checking URLs...")
        entries = check_and_filter_entries(entries)

    print("Writing sorted playlist...")
    write_playlist(OUTPUT_PATH, entries)

    print("Process completed.")

if __name__ == '__main__':
    main()
