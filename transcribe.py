import os
import re
import termcolor
import argparse
from googletrans import Translator
translator = Translator()


def check_chinese(w):
    filtered_word = re.findall(r"[\u4e00-\ufaff]+", str(w), re.UNICODE)
    if len(filtered_word) > 0:
        return True
    return False


def my_translate(in_text, from_lang='zh-cn', to_lang='en'):
    text_to_translate = translator.translate(in_text, src=from_lang, dest=to_lang)
    out_text = text_to_translate.text
    return out_text


def main(args):
    # src_chinese_file = args.src_ch
    # src_english_file = args.src_en
    src_chinese_file = "Sport.m3u"
    src_english_file = "Sporten.m3u"

    if src_chinese_file is None or not os.path.exists(src_chinese_file):
        raise Exception("There is no source file.")
    if src_english_file is not None and not os.path.exists(src_english_file):
        raise Exception("There is no target file.")

    new_chinese_file = 'translate_chinese.txt'
    new_english_file = 'translate_english.txt'
    error_chinese_file = 'error_chinese.txt'
    error_english_file = 'error_english.txt'

    is_pretranslated = True
    if src_english_file is None:
        src_english_file = src_chinese_file[:-4] + '_english.txt'
        is_pretranslated = False

    src_text_list = []
    tgt_text_list = []
    with open(src_chinese_file, 'r', encoding='utf-8') as src_fp:
        for line in src_fp:
            src_text_list.append(line.strip())

    if is_pretranslated:
        with open(src_english_file, 'r', encoding='utf-8') as tgt_fp:
            for line in tgt_fp:
                tgt_text_list.append(line.strip())
        assert len(src_text_list) == len(tgt_text_list)

    text_counts = len(src_text_list)
    termcolor.cprint('Data counts: {}'.format(text_counts), 'green')

    try:
        for i in range(text_counts):
            src_txt = src_text_list[i]
            if is_pretranslated:
                text = tgt_text_list[i]
            else:
                text = src_txt

            if check_chinese(text):
                translate_text = my_translate(text)
            else:
                translate_text = text

            if check_chinese(translate_text):  # error
                open(error_chinese_file, 'a', encoding='utf-8').write('{}\n'.format(src_txt))
                open(error_english_file, 'a', encoding='utf-8').write('{}\n'.format(translate_text))
                termcolor.cprint('({}): {}'.format(i+1, translate_text), 'red')
            else:
                open(new_chinese_file, 'a', encoding='utf-8').write('{}\n'.format(src_txt))
                open(new_english_file, 'a', encoding='utf-8').write('{}\n'.format(translate_text))
    except Exception as error:
        print(repr(error))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--src_ch', type=str, help="path of chinese text file")
    parser.add_argument('--src_en', type=str, help="path of english text file")
    args = parser.parse_args()

    main(args)
