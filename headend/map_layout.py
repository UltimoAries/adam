"""Optional private map artwork and normalized room hit areas."""
import hashlib
import json
import math
import re


def contains(points, x, y):
    """Boundary-inclusive point-in-polygon for marker placement."""
    inside = False
    for a, b in zip(points, points[1:] + points[:1]):
        ax, ay = a
        bx, by = b
        cross = (x-ax)*(by-ay) - (y-ay)*(bx-ax)
        if abs(cross) < 1e-7 and min(ax, bx) <= x <= max(ax, bx) and min(ay, by) <= y <= max(ay, by):
            return True
        if (ay > y) != (by > y) and x < (bx-ax)*(y-ay)/(by-ay)+ax:
            inside = not inside
    return inside


def load_private_layout(directory):
    path = directory / 'maps' / 'layout.json'
    if not path.is_file():
        return None
    try:
        if path.stat().st_size > 65536:
            raise ValueError()
        raw = path.read_bytes()
        data = json.loads(raw)
        name, filename = data['name'], data['image']
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise ValueError()
        if not isinstance(filename, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+\.png', filename):
            raise ValueError()
        image = path.parent / filename
        if image.is_symlink() or not image.is_file() or not 0 < image.stat().st_size <= 20*1024*1024:
            raise ValueError()
        with image.open('rb') as source:
            header = source.read(24)
        if header[:8] != b'\x89PNG\r\n\x1a\n' or header[12:16] != b'IHDR':
            raise ValueError()
        width, height = int.from_bytes(header[16:20], 'big'), int.from_bytes(header[20:24], 'big')
        if not 1 <= width <= 8192 or not 1 <= height <= 8192:
            raise ValueError()
        rows, ids = data['rooms'], set()
        if not isinstance(rows, list) or not 1 <= len(rows) <= 40:
            raise ValueError()
        rooms = []
        for row in rows:
            key, label, points = row['id'], row['name'], row['points']
            if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', key) or key in ids:
                raise ValueError()
            if not isinstance(label, str) or not 1 <= len(label) <= 80 or not isinstance(points, list) or not 3 <= len(points) <= 40:
                raise ValueError()
            for p in points:
                if not isinstance(p, list) or len(p) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in p):
                    raise ValueError()
                if not 0 <= p[0] <= 1000 or not 0 <= p[1] <= 700:
                    raise ValueError()
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            if min(xs) == max(xs) or min(ys) == max(ys):
                raise ValueError()
            anchor = row['anchor']
            if not isinstance(anchor, list) or len(anchor) != 2 or any(type(v) not in (int, float) or not math.isfinite(v) for v in anchor) or not contains(points, *anchor):
                raise ValueError()
            ids.add(key)
            rooms.append(dict(id=key, name=label, points=points, anchor=anchor,
                              bounds=dict(x=min(xs), y=min(ys), width=max(xs)-min(xs), height=max(ys)-min(ys))))
        # Geometry changes start a separate placement layout; old coordinates are
        # retained in the database rather than silently mapped onto other rooms.
        key = 'private-' + hashlib.sha256(raw).hexdigest()[:20]
        return dict(id=key, name=name, rooms=rooms, image=image, aspect_ratio=width/height)
    except (ValueError, KeyError, TypeError, OSError):
        raise ValueError('Invalid private map layout. Check maps/layout.json and its PNG image.') from None
