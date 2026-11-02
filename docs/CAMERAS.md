# Cameras and gesture actions

Open **Cameras & gesture actions** on the ADAM dashboard, or `/cameras`.
Add an RTSP URL using the camera's private LAN IPv4 address, preferably its
low-resolution substream. Saved URLs and credentials are never returned by
the administrator API. Leave the connection field blank when editing to keep
the saved URL. Credentials remain in the private SQLite data volume, like
other ADAM service credentials; they are not encrypted at rest.

Up to eight camera connections may be saved. This initial Pi deployment runs
one camera at a time. **Test connection** temporarily opens a paused camera
for 20 seconds without enabling actions; pause another active camera first.
An annotated preview updates approximately every two seconds. Only the latest
JPEG preview is held in RAM, expires after ten seconds, and requires an administrator
session to view. Optional event snapshots are described below. The service does
not record continuous video or camera audio.

## Event snapshots and debugging

Under **Edit**, enable **Save a snapshot when a gesture triggers**. This is off
by default for new cameras. A sustained gesture event saves the exact annotated
inference frame, including detection-only events and failed Home Assistant
actions. It does not save every hand detection or reuse a stale preview.

Expand an event's **View snapshot & debugging details** to view/download the
JPEG and download event JSON. Details include camera name, capture/event time,
gesture/confidence, threshold/hold/cooldown settings, source resolution, frame
age, inference time, model, configuration revision, and the rule/action outcome.
RTSP addresses and credentials are excluded. Existing events created before
this feature cannot acquire a historical image retroactively.

Snapshots are stored privately in SQLite alongside event metadata. They require
an administrator session to read and use `Cache-Control: no-store`. Turning
capture off stops new images; use **Delete snapshot** to remove an existing
image while retaining event details. Deleting a camera removes its events and
images. Images expire after seven days on the next camera API activity and
are also bounded by the existing latest-200-events limit. Each JPEG is capped
at 200,000 bytes (at most 40 MB of image payload globally). SQLite reuses freed
pages rather than shrinking the database file on each deletion. A 256 MiB
disk reserve prevents new snapshot writes on a nearly full volume.

Only matching candidate frames are attached to worker observations; the server
persists an image only when the hold/rearm gate emits an event. Image encoding,
validation, or storage failures do not cancel or retry the mapped action. The
event reports why an image is missing instead. These downscaled, annotated
frames are debugging evidence, not a continuous recording or raw training set.

## Gesture rules

Seven static gestures are available: open palm, closed fist, thumbs up,
thumbs down, pointing up, victory/V, and the I-love-you hand sign. Waving and
other motion sequences are not implemented. This is hand-gesture recognition,
not general person/object detection.

Each camera can have one rule per gesture. Start with **Detection only**.
To trigger an action, choose **Run a device action**, select an imported,
enabled Home Assistant device, and select one of its supported actions.
Numeric values, choices, colors, and temperature ranges appear when required.
Existing Home Assistant `controls()` / `action_payload()` validation and live
state checks still apply; there is no arbitrary service-call interface.

Default settings are 3 processed frames/second, confidence 0.75, a one-second
hold, and a five-second cooldown. Remove the gesture for one second to arm
recognition after startup, configuration changes, voice pauses, or interrupted
frames, and between repetitions. A held gesture fires once. Observations older
than one second, duplicate sequence numbers, and old camera revisions cannot
dispatch actions. Failed/uncertain Home Assistant calls are never retried.
The latest 200 event records are retained; the page shows 30.

## Pi deployment

The head-end owns configuration, authorization, event gating, and action
dispatch. `headend/camera_worker.py` runs in a separate container, decodes RTSP
over TCP with PyAV, and processes the latest frame with MediaPipe's gesture
recognizer. Input is limited to a 1080p substream and scaled to at most 640 pixels
wide for inference. Frames are dropped instead of queuing behind processing.

The worker polls an authenticated loopback-only configuration endpoint. Its
random credential is created at `headend/data/camera-worker.token` with private
permissions and mounted read-only into the worker. The camera container does
not have access to the database or Home Assistant token. The head-end rejects
gesture actions during active voice capture/transcription/reply processing;
the worker polls this state every second and closes capture while paused.

```sh
cd ~/adam/deploy/pi5
docker compose -f compose.yaml -f compose.llm.yaml -f compose.camera.yaml up -d --build
```

The worker has a one-CPU quota, a requested 1 GiB memory limit, read-only root
filesystem, bounded temporary storage, and no additional published port.
The initial Pi kernel did **not** support Docker memory limits, so the memory
limit was not enforced there; the CPU quota was verified at `100000 100000`.
Check host support before relying on the memory cap. Deleting a camera clears
its rules, runtime preview, and event history. Pausing or deleting it stops
capture on the next worker configuration refresh.

## Initial validation

The supplied camera was registered privately and decoded at 1280×720, 15 FPS.
At 3 sampled frames/second, an initial live sample used approximately 15% of
one CPU core, about 245 MiB resident memory, and 31–39 ms inference per frame.
The host remained 94–96% idle during that sample. These are observations, not
guarantees for every camera, lighting condition, or gesture.

The model loaded on ARM64, the 173-test head-end suite passed, and browser
checks covered saved-credential redaction, live previews, creating a disabled
camera, seven gesture choices, device-dependent actions/brightness fields,
and desktop/mobile layout. The user confirmed live gesture recognition.
For the initial camera angle, open-palm confidence was approximately 0.60–0.69;
the private camera setting was calibrated to 0.60. A sustained real open palm
then produced one event with no duplicates while it remained held. Other
camera angles and gestures should be calibrated in detection-only mode first.
Physical Home Assistant actuation still requires a selected, enabled rule;
initial camera rules are detection-only.

After adding event snapshots, the 181-test head-end suite passed. A live
closed-fist test saved the triggering frame at approximately 75% confidence
with a detection-only outcome. Browser checks verified the saved-image viewer,
event JSON download, snapshot setting persistence, and desktop/mobile layout.

The gesture model is Google's MediaPipe float16 version 1, verified during
the worker build with SHA-256
`97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482`.
See the [MediaPipe gesture guide](https://developers.google.com/edge/mediapipe/solutions/vision/gesture_recognizer/python)
and [Raspberry Pi example](https://github.com/google-ai-edge/mediapipe-samples/tree/main/examples/gesture_recognizer/raspberry_pi).
