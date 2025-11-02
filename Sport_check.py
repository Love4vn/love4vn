import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
import subprocess

def is_channel_working(url, timeout=36):
    try:
        response = requests.head(url, timeout=timeout)
        return response.status_code == 200
        #return 399 >= response.status_code >= 100
    except requests.RequestException:
        return False

def get_video_resolution(url, timeout=36):
    try:
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
        return None
    except Exception as e:
        return None

def clean_name(name):
    allowed_chars = re.compile(r'[^a-zA-Z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af\u1100-\u11ff\u3130-\u318f\u0E00-\u0E7F\u0400-\u04FF ;]+')
    return allowed_chars.sub('', name).strip()

def format_group_title(line):
    match = re.search(r'group-title="([^"]+)"', line)
    if match:
        group_title = match.group(1)
        group_title = re.sub(r'\s+', ' ', group_title)  # Reduce multiple spaces to single space
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

def check_and_filter_entries(entries):
    urls = [entry[-1].strip() for entry in entries]
    valid_urls = set()
    resolution_dict = {}

    with ThreadPoolExecutor(max_workers=1000) as executor:
        results = list(tqdm(executor.map(check_url, urls), total=len(urls), desc="Checking Channels"))

    valid_entries = [entry for entry, (url, is_valid) in zip(entries, results) if is_valid]
    valid_urls = [entry[-1].strip() for entry in valid_entries]

    with ThreadPoolExecutor(max_workers=40) as executor:
        future_to_url = {executor.submit(check_resolution, url): url for url in valid_urls}
        for future in tqdm(as_completed(future_to_url), total=len(future_to_url), desc="Checking Resolutions"):
            url = future_to_url[future]
            try:
                _, resolution = future.result()
                if resolution:
                    resolution_dict[url] = resolution
            except Exception:
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
            file.write('\n')  # Ensure there is a single newline after each entry

def main():
    input_path = "hubsport.m3u"
    output_path = "S_check.m3u"
    
    print("Parsing playlist...")
    entries = parse_playlist(input_path)

    print("Removing duplicates...")
    unique_entries = remove_duplicates(entries)

    print("Sorting entries...")
    sorted_entries = sort_entries(unique_entries)

    print("Checking URLs...")
    valid_entries = check_and_filter_entries(sorted_entries)

    print("Writing sorted playlist...")
    write_playlist(output_path, valid_entries)
    print("Process completed.")

if __name__ == '__main__':
    main()
