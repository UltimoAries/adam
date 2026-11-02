"""Optional CPU-limited RTSP/MediaPipe sidecar. No camera credentials in logs."""
import json
import base64
import os
from pathlib import Path
import threading
import time
import urllib.request

os.environ.setdefault('OPENCV_LOG_LEVEL', 'SILENT')
os.environ.setdefault('GLOG_minloglevel', '2')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')


class Client:
    def __init__(self):
        self.url = os.environ.get('ADAM_CAMERA_SERVER', 'http://127.0.0.1:8765')
        self.token_file = Path(os.environ.get('ADAM_CAMERA_TOKEN', '/data/camera-worker.token'))
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, data=None, method=None, revision=None):
        headers = {'Authorization': 'Bearer '+self.token_file.read_text().strip()}
        if isinstance(data, dict):
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        elif data is not None:
            headers['Content-Type'] = 'image/jpeg'
        if revision:
            headers['X-Camera-Revision'] = revision
        request = urllib.request.Request(self.url+'/api/camera-worker/'+path, data=data, headers=headers, method=method)
        with self.opener.open(request, timeout=5) as response:
            return json.load(response)


class Capture:
    def __init__(self, url):
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.latest = None
        self.status = 'connecting'
        self.thread = threading.Thread(target=self.run, args=(url,), daemon=True)
        self.thread.start()

    def run(self, url):
        import av
        av.logging.set_level(av.logging.PANIC)
        while not self.stop.is_set():
            try:
                with av.open(url, options={'rtsp_transport':'tcp', 'fflags':'nobuffer'}, timeout=(5,5)) as container:
                    stream = container.streams.video[0]
                    stream.thread_type = 'SLICE'
                    stream.codec_context.thread_count = 1
                    if stream.width > 1920 or stream.height > 1080:
                        raise ValueError('Use a camera substream')
                    for frame in container.decode(stream):
                        if self.stop.is_set():
                            return
                        with self.lock:
                            self.latest = (time.monotonic(), frame)
                            self.status = 'streaming'
            except Exception:
                with self.lock:
                    self.latest = None
                    self.status = 'stream_error'
            self.stop.wait(3)

    def read(self):
        with self.lock:
            return self.latest, self.status

    def close(self):
        self.stop.set()
        self.thread.join(timeout=6)
        if self.thread.is_alive():
            # Recover a stuck decoder through the container's restart policy.
            os._exit(1)


def annotated_jpeg(rgb, result, gesture, confidence):
    import cv2
    height, width = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    for hand in result.hand_landmarks:
        for point in hand:
            cv2.circle(bgr,(int(point.x*width),int(point.y*height)),3,(70,240,140),-1)
    label = f'{gesture.replace("_", " ")} {confidence:.0%}' if gesture != 'None' else 'No hand gesture'
    cv2.putText(bgr,label,(12,25),cv2.FONT_HERSHEY_SIMPLEX,.6,(70,240,140),2)
    ok, jpeg = cv2.imencode('.jpg',bgr,[cv2.IMWRITE_JPEG_QUALITY,75])
    return jpeg.tobytes() if ok and len(jpeg) <= 200000 else None


def run():
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import vision
    cv2.setNumThreads(1)
    model = os.environ.get('ADAM_GESTURE_MODEL', '/models/gesture_recognizer.task')
    client = Client()
    recognizer = None
    capture = None
    selected = None
    next_config = next_sample = next_preview = 0
    config = {}
    last_frame = 0
    last_timestamp = 0
    print('ADAM camera worker starting', flush=True)
    while True:
        now = time.monotonic()
        try:
            if now >= next_config:
                config = client.request('config')
                next_config = now+1
            camera = config.get('camera')
            key = (camera['id'], camera['revision']) if camera and not config.get('paused') else None
            if key != selected:
                if capture:
                    capture.close()
                    capture = None
                if recognizer:
                    recognizer.close()
                    recognizer = None
                selected = key
                last_frame = 0
            if not camera:
                time.sleep(.2)
                continue
            if now < next_sample:
                time.sleep(.02)
                continue
            next_sample = now+1/camera['fps']
            observation = dict(revision=camera['revision'], sequence=time.time_ns(), status='paused')
            if key:
                if capture is None:
                    capture = Capture(camera['rtsp_url'])
                if recognizer is None:
                    try:
                        recognizer = vision.GestureRecognizer.create_from_options(vision.GestureRecognizerOptions(
                            base_options=mp.tasks.BaseOptions(model_asset_path=model),
                            running_mode=vision.RunningMode.VIDEO, num_hands=1,
                            min_hand_detection_confidence=.6, min_hand_presence_confidence=.6,
                            min_tracking_confidence=.6))
                    except Exception:
                        observation['status'] = 'model_error'
                        client.request(camera['id']+'/observation', observation)
                        time.sleep(3)
                        continue
                latest, status = capture.read()
                observation['status'] = status
                if latest:
                    captured, frame = latest
                    age = (time.monotonic()-captured)*1000
                    if age > 1000:
                        observation['status'] = 'stream_error'
                    elif captured == last_frame:
                        continue
                    else:
                        last_frame = captured
                        # Decoder stores only the newest frame; inference never builds a backlog.
                        height = max(1, round(frame.height*min(1,640/frame.width)))
                        width = min(frame.width,640)
                        rgb = frame.reformat(width=width,height=height,format='rgb24').to_ndarray()
                        timestamp = max(last_timestamp+1, int(captured*1000))
                        last_timestamp = timestamp
                        started = time.monotonic()
                        result = recognizer.recognize_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), timestamp)
                        category = result.gestures[0][0] if result.gestures and result.gestures[0] else None
                        gesture = category.category_name if category else 'None'
                        inference_ms = (time.monotonic()-started)*1000
                        observation.update(gesture=gesture, confidence=float(category.score) if category else 0,
                            age_ms=(time.monotonic()-captured)*1000, inference_ms=inference_ms,
                            width=frame.width, height=frame.height)
                        snapshot_candidate = camera.get('snapshot_events',False) and observation['confidence'] >= camera['confidence'] and any(r['gesture'] == gesture for r in camera['rules'])
                        if now >= next_preview or snapshot_candidate:
                            # This exact inference frame travels with its observation. Never
                            # substitute the periodically refreshed preview for event evidence.
                            try:
                                jpeg = annotated_jpeg(rgb,result,gesture,observation['confidence'])
                            except Exception:
                                jpeg = None
                            if jpeg and snapshot_candidate:
                                observation['snapshot_jpeg'] = base64.b64encode(jpeg).decode('ascii')
                            if jpeg and now >= next_preview:
                                client.request(camera['id']+'/preview',jpeg,'PUT',camera['revision'])
                                next_preview = now+2
                        observation['age_ms'] = (time.monotonic()-captured)*1000
                        observation['captured_unix'] = time.time()-(time.monotonic()-captured)
            client.request(camera['id']+'/observation', observation)
        except Exception:
            # Do not retain camera capture on authentication/server failure.
            if capture:
                capture.close()
                capture = None
            config = {}
            next_config = 0
            time.sleep(2)


if __name__ == '__main__':
    run()
