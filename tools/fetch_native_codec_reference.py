"""Pin the Apache-2.0 native ES7210 driver used to check active power settings."""
import hashlib
import json
from pathlib import Path
import requests

path = 'components/esp_codec_dev/device/es7210/es7210.c'
repo = 'espressif/esp-adf'
commits = requests.get(f'https://api.github.com/repos/{repo}/commits',
                       params={'path':path,'per_page':1},timeout=20)
commits.raise_for_status()
revision = commits.json()[0]['sha']
url = f'https://raw.githubusercontent.com/{repo}/{revision}/{path}'
response = requests.get(url,timeout=20)
response.raise_for_status()
destination = Path('vendor/espressif/es7210-native.c')
destination.parent.mkdir(parents=True,exist_ok=True)
destination.write_bytes(response.content)
metadata = dict(repository=repo,revision=revision,path=path,url=url,
                sha256=hashlib.sha256(response.content).hexdigest(),license='Apache-2.0')
destination.with_suffix('.json').write_text(json.dumps(metadata,indent=2)+'\n')
print(json.dumps(metadata,indent=2))
