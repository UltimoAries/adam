"""Install an actual audio asset as the device cue; build/flash afterward."""
import argparse
import json
from pathlib import Path
from headend.cue import decode_cue

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('audio',type=Path)
parser.add_argument('--source',default='User-selected audio')
parser.add_argument('--attribution',default='')
parser.add_argument('--text',default='Huh?',help='Words spoken by this cue, for provenance')
args=parser.parse_args()
pcm,wav,metadata=decode_cue(args.audio.read_bytes())
metadata.update(text=args.text,source=args.source,attribution=args.attribution)
root=Path(__file__).resolve().parent.parent
(root/'firmware/main/listen_cue.pcm').write_bytes(pcm)
(root/'firmware/main/listen_cue.json').write_text(json.dumps(metadata,indent=2)+'\n')
print(json.dumps(metadata))
