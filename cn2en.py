import os
import re
import termcolor
import argparse
from googletrans import Translator
translator = Translator()
# import requests
# import re
# from concurrent.futures import ThreadPoolExecutor, as_completed
# from tqdm import tqdm
# import subprocess

def translate_channel_names(m3u_content):
    # Initialize the Google API translator
    translator = Translator()
    translated_lines = []

    # Load the M3U content
    pl = playlist.loads(m3u_content)

    # Translate channel names and group titles
    for line in m3u_content.splitlines():
        if line.startswith('#EXTINF:'):
            channel_name = line.split(',')[1]
            translated_name = translator.translate(channel_name, src='zh-CN', dest='en').text
            translated_line = f"{line.split(',')[0]}, {translated_name}"
            translated_lines.append(translated_line)
        else:
            translated_lines.append(line)

    return '\n'.join(translated_lines)

def write_playlist(file_path, entries):
    with open(file_path, 'w', encoding='utf-8') as file:
        file.write('#EXTM3U\n')
        for entry in entries:
            for line in entry:
                file.write(line)
            file.write('\n')  # Ensure there is a single newline after each entry

def main():
    input_path = "IPTV.m3u"
    output_path = "enIPTV.m3u"
    
    print("Translate cn 2 en ...")
    valid_entries = translate_channel_names(input_path)

    print("Writing sorted playlist...")
    write_playlist(output_path, valid_entries)
    print("Process completed.")

if __name__ == '__main__':
    main()
