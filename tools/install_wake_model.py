"""Install actual local microWakeWord v2 bytes and their matching configuration.

Rebuild/flash afterward. This does not train a model or change its wake phrase
by renaming a stock model. The native runner checks input/output tensor shapes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('manifest',type=Path)
parser.add_argument('--id',required=True)
parser.add_argument('--development',action='store_true')
args=parser.parse_args()
if not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}',args.id):parser.error('Invalid model ID')
manifest=json.loads(args.manifest.read_text())
if manifest.get('version')!=2 or manifest.get('type')!='micro':parser.error('Use a microWakeWord v2 manifest')
micro=manifest['micro']
if not 0<float(micro['probability_cutoff'])<1:parser.error('Invalid detection threshold')
if not 1<=micro['feature_step_size']<=30:parser.error('Invalid feature step')
if not 1<=micro['sliding_window_size']<=100:parser.error('Invalid sliding window')
if not 16384<=micro['tensor_arena_size']<=1048576:parser.error('Invalid tensor arena')
model=(args.manifest.parent/manifest['model']).read_bytes()
if model[4:8]!=b'TFL3' or len(model)>1048576:parser.error('Invalid or oversized TFLite model')
output=Path('firmware/main');(output/'wake_model.tflite').write_bytes(model)
record=dict(manifest,model_id=args.id,development=args.development,sha256=hashlib.sha256(model).hexdigest())
(output/'wake_model.json').write_text(json.dumps(record,indent=2)+'\n')
header='// Generated from the matching TFLite model by tools/install_wake_model.py.\n#pragma once\n'
for key,value in dict(WAKE_MODEL_ID=args.id,WAKE_PHRASE=manifest['wake_word'],WAKE_THRESHOLD=float(micro['probability_cutoff']),
    WAKE_STEP_MS=micro['feature_step_size'],WAKE_WINDOW=micro['sliding_window_size'],WAKE_ARENA=micro['tensor_arena_size'],WAKE_DEVELOPMENT=int(args.development)).items():
    literal=json.dumps(value) if isinstance(value,str) else str(value)+('f' if isinstance(value,float) else '')
    header+=f'#define {key} {literal}\n'
(output/'wake_model_config.h').write_text(header)
print(f'Installed {manifest["wake_word"]}: {len(model)} bytes, ID {args.id}. Rebuild and flash to activate.')
