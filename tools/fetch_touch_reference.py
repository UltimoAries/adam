from pathlib import Path
import hashlib
import json
import requests

tree = json.loads(Path('vendor/waveshare-tree.json').read_text())
index = {item['path']: item['sha'] for item in tree['tree']}
root = 'ESP-IDF/ESP32-S3-Touch-LCD-1.85C-Test/main/Touch_Driver/'
for name in ['CST816.c', 'CST816.h']:
    path = root + name
    url = 'https://raw.githubusercontent.com/waveshareteam/ESP32-S3-Touch-LCD-1.85C/' + tree['sha'] + '/' + path
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.content
    assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == index[path]
    dest = Path('vendor') / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    print(name)
