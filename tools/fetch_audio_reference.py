"""Fetch hash-verified audio references from the already pinned manufacturer tree."""
from pathlib import Path
import hashlib
import json
import requests

tree = json.loads(Path('vendor/waveshare-tree.json').read_text())
index = {item['path']: item['sha'] for item in tree['tree']}
files = [
    'Arduino/examples/08_esp_sr/08_esp_sr.ino',
    'Arduino/examples/08_esp_sr/es7210.cpp',
    'Arduino/examples/08_esp_sr/es7210.h',
    'Arduino/examples/08_esp_sr/es7210_reg.h',
    'Arduino/libraries/es7210/es7210.c',
    'Arduino/libraries/es7210/es7210.h',
    'Arduino/libraries/es7210/es7210_reg.h',
    'hardware/schematics/ESP32-S3-Touch-LCD-1.85C-V2--Schematic.pdf_.pdf',
    'Arduino/examples/03_audio_out_no_tf/03_audio_out_no_tf.ino',
    'Arduino/examples/03_audio_out_no_tf/es8311.cpp',
    'Arduino/examples/03_audio_out_no_tf/es8311.h',
    'Arduino/examples/03_audio_out_no_tf/es8311_reg.h',
    'ESP-IDF/ESP32-S3-Touch-LCD-1.85C-Test/main/Audio_Driver/Audio_Driver.c',
    'ESP-IDF/ESP32-S3-Touch-LCD-1.85C-Test/main/Audio_Driver/Audio_Driver.h',
    'ESP-IDF/ESP32-S3-Touch-LCD-1.85C-Test/components/auido_borad/boards/ESP32_S3_AUDIO_Board/bsp_board.c',
    'ESP-IDF/ESP32-S3-Touch-LCD-1.85C-Test/components/auido_borad/boards/include/ESP32_S3_AUDIO_Board.h',
]
for path in files:
    dest = Path('vendor')/path
    if dest.is_file():
        existing = dest.read_bytes()
        if hashlib.sha1(b'blob '+str(len(existing)).encode()+b'\0'+existing).hexdigest() == index[path]:
            continue
    url = 'https://raw.githubusercontent.com/waveshareteam/ESP32-S3-Touch-LCD-1.85C/' + tree['sha'] + '/' + path
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.content
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == index[path]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    print(path)
