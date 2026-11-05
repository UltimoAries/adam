# Floor plan and light controls

Open **Menu → Map** on any ADAM page. The dedicated `/map` page replaces the
old Three.js sample on Home. Home retains resource readings, commands, speakers,
timers, intercom, and paging. All nine pages share the same navigation component,
with the current page identified and a keyboard-accessible Menu.

The Map supports a private PNG with configured room boundaries, persistent
indoor/outdoor light markers, and illumination based on live light status.
Custom room names and boundaries come from the private configuration, not
household-specific backend code. Image setup currently requires manual files.

When no valid private layout is configured, the eight-space SVG fallback follows the
[map-tap-control reference](https://github.com/UltimoAries/map-tap-control): living
room, kitchen/dining, primary bedroom, hallway, bathroom, entry, guest room/office,
and patio. This is a fixed reference layout, not the user's actual floor plan.
The fallback room names/geometry and the generated Front/Back outdoor bands are
defined in code. Other device types and scenes are not supported on the map.
ADAM uses native HTML/CSS/JavaScript with locally served
assets; React, browser-only simulated state, and external CDNs are not required.

## Unfinished custom-image setup

**Image upload and guided `layout.json` creation still need to be implemented.**
Edit mode currently edits light placements only; it cannot upload an image,
draw room boundaries, rename rooms, or generate the layout file.

Remaining work:

- Add authenticated image upload with file validation and preview.
- Add a room-boundary editor that creates and validates `layout.json`, including
  room IDs, display names, polygons, and placement anchors.
- Make outdoor orientation and labels configurable; currently Back is above the
  image and Front is below it.
- Define an explicit save/update flow for preserving or remapping existing
  markers when artwork or room geometry changes.

Use the manual setup below until that workflow is available.

## Private floor-plan artwork

The head-end optionally loads `headend/data/maps/layout.json` on startup. It
names a PNG in that same directory and defines room hit areas over the image.
Transparent PNG backgrounds let the surrounding webpage show through. The
renderer preserves the image's aspect ratio while marker coordinates remain
normalized to 1000 × 700. On narrow screens the map scrolls within its panel.

Private maps include a bordered outdoor area: **Back outside** above the image
and **Front outside** below it, each wrapping halfway around the sides. Place
individual imported lights anywhere along those bands for porches, exterior
garage lighting, and backyard doors. The display viewport extends to
`(-60, -75)–(1060, 775)` without changing indoor coordinates or placement identity.
The outdoor bands cannot accept placements inside the original image.

The JSON contains `name`, `image` (a simple PNG filename), and `rooms`. Each room
has a stable `id`, a display `name`, a `points` polygon of `[x, y]` pairs, and an
`anchor` point inside that polygon for placement without dragging. Rectangular
bounds are derived from the polygon; placement rejects points outside it,
including cutouts within an L-shaped room. Configuration is limited to 40 rooms,
40 points per room, and a 20 MiB PNG. Invalid configuration leaves the reference
map available with an error message, without stopping voice services.

### Prepare and install a custom image manually

1. Prepare a top-down **PNG** with final cropping and orientation. Convert other
   image formats to PNG first. Maximum size is 20 MiB, with each dimension from
   1 to 8192 pixels. Remove unwanted title boxes, scales, or area text in an image
   editor; ADAM does not remove them. Transparency outside the building is
   optional and allows the page background to show through.
2. Save it in `headend/data/maps/`, for example `floor.png`. Use a filename made
   from letters, digits, underscores, and hyphens with a lowercase `.png` extension.
3. Trace each room's interior boundary on the final image. Convert pixel
   positions to layout coordinates: `x = pixel_x / image_width * 1000` and
   `y = pixel_y / image_height * 700`, measured from the upper-left corner.
   List polygon vertices in perimeter order without crossing edges. Keep room
   polygons separate so illumination does not cover neighboring rooms.
4. Create `layout.json` in the same directory. Give each room a unique lowercase
   ID starting with a letter (up to 40 letters, digits, underscores, or hyphens),
   a display name, 3–40 polygon points, and an anchor inside it. Define 1–40
   rooms. Leave `outdoor_front` and `outdoor_back` for the generated outdoor bands.
5. Copy both files to the running head-end's `headend/data/maps/` directory.
   On the Pi this is the host data directory mounted into the container, not
   the public web assets directory. Ensure the head-end user can read the files,
   then restart the head-end using the installation's existing Compose setup.
   The source packager does not include these private files.
6. Reload **Menu → Map**, check that boundaries align with the image, then use
   **Edit mode** to place imported lights. If configuration is invalid, the map
   shows an error and the fallback layout.

Minimal illustrative `layout.json` (replace the example polygon with your own):

```json
{
  "name": "Ground floor",
  "image": "floor.png",
  "rooms": [
    {
      "id": "room_a",
      "name": "Room A",
      "points": [[100, 100], [450, 100], [450, 400], [100, 400]],
      "anchor": [275, 250]
    }
  ]
}
```

Image labels are pixels: changing a room's JSON name does not change text printed
in the PNG. Room boundaries and names are not detected automatically.

Images are served through authenticated `GET /api/map/background` with no-store
caching, never through public `/assets`. The private image, room geometry, and
edit prompt stay in ignored `headend/data/maps/`. They are excluded from source
archives and public GitHub commits. Copy this private directory separately into
the Pi's data volume when deploying a custom map, then restart the head-end.

Each configuration has its own placement layout, keyed by the exact JSON file
bytes. Any JSON edit, including formatting or a room rename, selects a new
placement set; old placements remain stored. Restoring the original JSON bytes
restores its markers. Replacing PNG contents under the same filename without
editing JSON preserves positions, so keep the image framing and geometry aligned.
Back up both files before changes. A friendly layout-update/migration workflow
is still unfinished.

To preview private artwork with synthetic lights, set
`ADAM_MAP_PREVIEW_DIRECTORY` to the absolute path of the head-end data directory
before running the browser check below. It copies only the map configuration
and image into the temporary test directory; it never uses the real database
or HA connection. Screenshots remain in ignored `artifacts/map-lights/`.

## Place and move lights

1. Import and enable lights in **Menu → Device inventory**.
2. Open **Map**, turn **Edit mode** on, select a room, then choose a light in the inspector.
3. Select **Place in this room**. Adding lights uses free grid positions where
   possible. Multiple lights can share a room, with one marker per light.
4. With **Edit mode** on, drag a marker using mouse or touch. With a marker
   focused, arrow keys move it; Shift + arrow moves it in smaller steps. A
   selected light can also be placed by tapping a room in this mode.
5. The room selector and **Move to selected room** provide a non-drag alternative.
   **Remove marker** clears only its placement.

In Edit mode, selecting or moving a marker never operates the light, and the
inspector's device controls are disabled. Adding, moving, and removing markers
are available only in Edit mode. Turn Edit mode off when finished; clicking or
keyboard-activating a marker then switches that light on/off. The inspector is
hidden outside Edit mode and the map expands to fill the available width while
preserving the artwork's proportions. Room clicks do nothing in this view;
only markers operate lights. Command feedback appears above the map.
Positions are stored in
SQLite on the head-end, not browser storage, and survive reloads and restarts.
Indoor coordinates use the reference's 1000 × 700 plane; outdoor coordinates
extend into the border described above. Placements must remain within their
selected room or outdoor band. Existing four-room assignments migrate once: Living room
and Kitchen retain their rooms; Bedroom becomes Primary bedroom and Study
becomes Guest room / office. Their light identities are preserved. Removing a
migrated marker does not recreate it on restart.

## Status and control

- Gold markers indicate on; gray markers indicate off; dashed amber markers
  indicate unavailable. Labels and accessible names also expose these states.
- With Edit mode off, clicking a marker sends an explicit on/off command and
  updates its reported state. While the request and status refresh are pending, further
  commands are disabled. The map never assumes success before reading status.
  The notice above the map shows the server's confirmed, sent, or unconfirmed
  result. Detailed device controls remain available from Device inventory.
- Unknown, missing, disabled, or disconnected lights are never presented as off.
  Their controls are unavailable. Retained placements remain removable.
- Rooms with assigned lights darken when all are confirmed off. At least one
  confirmed-on light adds a warm radial glow centered on the on-light markers;
  reported brightness changes its intensity. The overlay follows each room's
  polygon, preserving walls and neighboring rooms. Unassigned rooms receive
  lighter shading; unavailable readings clear stale illumination and use a dashed border.
- Status refreshes every five seconds while visible. Tabs share the server's
  five-second HA state cache. The checked time describes the HA fetch, not the
  time the physical light last reported. Failed reads discard stale on/off
  status. Hidden tabs stop polling; session expiry clears the map and inspector.
- Placement and action requests include an opaque settings version. A changed
  HA connection or inventory invalidates stale requests; marker data is scoped
  to the HA origin. Actions cannot silently target the same entity ID on a new
  HA server.

## Implementation

`headend/map_layout.py` loads and validates private PNG/layout files.
`headend/map_lights.py` supplies fallback geometry and outdoor bands, validates
placements, stores markers, and caches live status. `floor-plan.js` renders SVG
rooms and HTML markers, handles touch/keyboard placement, and sends marker toggles.
`navigation.js` and `navigation.css` define the shared navigation.

| Route | Purpose |
| --- | --- |
| `GET /api/map/lights` | Layout geometry, light capabilities, placements, live status, and settings version |
| `GET /api/map/background` | Optional private PNG; requires sign-in and is not browser-cached |
| `PUT /api/map/lights/{entity_id}` | Save `{room_id, x, y, version}`; `room_id: null` removes the marker |
| `POST /api/map/lights/{entity_id}/actions` | Send `{action, value, version}` through existing validated light controls |

All API routes require the existing administrator session and origin checks.
The action route permits only lights and bounded on/off, brightness, color
temperature, and color actions. It delegates to `HomeAssistantService`, which
validates the enabled inventory, rebuilds the service call from live
capabilities, and checks the settings revision before sending. It never retries
a service POST. Tokens and HA origin URLs are not returned by the map API.

Run from the repository root:

```powershell
.\headend\.venv\Scripts\python.exe -m unittest headend.test_map_lights -q
.\headend\.venv\Scripts\python.exe tools/check_map_lights_browser.py
```

The browser check uses a temporary server/database and synthetic HA lights.
It exercises marker toggles, edit/view separation, mouse/touch/keyboard movement,
persistence, unavailable-state recovery, mobile layout, removal, session expiry,
and shared navigation across nine pages. It sends commands only to the mocked
HA transport. Screenshots and logs stay in ignored `artifacts/map-lights/`.
Set `CHROME_BIN` if needed; the check requires Chrome/Chromium and `websockets`.
The private-artwork mode additionally checks outdoor dragging, image alignment,
saved outdoor positions, and expansion when the inspector is hidden.
