# Room lights on the Monitor Map

The signed-in dashboard maps imported Home Assistant lights onto the four rooms
in the current sample house: Living room, Kitchen, Bedroom, and Study. Placement
is saved in ADAM and survives reloads and head-end restarts. It does not change
Home Assistant areas, device settings, or light power.

## Assign a light

1. In **Home Assistant settings**, refresh the inventory and import/enable the
   lights you want to show. This first version supports `light.*` entities.
2. On the dashboard, find **Room lights** below the 3D map.
3. Choose a light and room, then select **Assign light**. You can also click/tap
   a room in the 3D view or its status card to select it. Desktop users can drag
   a light card onto a room in the map or onto a room status card.
4. Assigning an already-placed light to another room moves it. **Remove** clears
   the placement without controlling the light. Multiple lights can share a room;
   each light belongs to one room in this layout.

The dropdown path supports touch and keyboard use, including browsers without
WebGL. The room assignments belong to this sample layout; custom model import,
editable room boundaries, other device types, and map-based light controls are
not part of this version.

## Reading status

- A room glows warm amber when at least one assigned light is on. Reported
  brightness adjusts the glow; lights without brightness data use full glow.
- A room with all assigned lights confirmed off has no illumination.
- Unknown, unavailable, removed, or disabled devices are not counted as off.
  If no assigned light is known on, the room says **Status unavailable** and
  has an amber dashed status-card border. Mixed groups show the on count and
  the unavailable count.
- Rooms with no assignments say **No lights assigned**.

While the dashboard is visible, status refreshes every five seconds. The server
shares one cached HA state read across tabs for five seconds. Failed reads clear
previously displayed on/off status, and the UI marks readings unavailable if
the head-end cannot be reached. Hidden tabs and signed-out pages stop polling.
The checked time is the HA fetch time, not a guarantee that a physical device
has recently reported to HA.

Changing the HA connection scopes the map to that origin's assignments. Returning
to the former origin restores its placements. Names may change without moving
devices because placements use entity IDs. An old browser assignment is rejected
after inventory/settings changes or a head-end restart; refresh and retry.

## Implementation and validation

`headend/map_lights.py` stores placements in the private SQLite database, keyed
by HA origin, layout ID, and entity ID. `/api/map/lights` is authenticated and
inherits the existing origin checks and no-store policy. It exposes only light
names, IDs, placements, normalized status/brightness, and room aggregates.
The endpoints never call a Home Assistant action or invoke an LLM.

| Route | Purpose |
| --- | --- |
| `GET /api/map/lights` | Current inventory choices, saved placements, room status, checked time, and opaque version |
| `PUT /api/map/lights/{entity_id}` | Save `{room_id, version}`; `room_id: null` removes a placement |

`map-lights.js` provides polling and accessible assignment controls;
`monitor-map.js` handles room picking, drop targets, and illumination with the
existing bundled Three.js. Rendering remains event-driven, with no continuous
animation loop.

Run from the repository root:

```powershell
.\headend\.venv\Scripts\python.exe -m unittest headend.test_map_lights -q
.\headend\.venv\Scripts\python.exe tools/check_map_lights_browser.py
```

The browser check uses a temporary server/database, synthetic lights, and a
separate headless Chrome profile. It covers room picking, dropdown assignment,
native mouse drag-and-drop, on/off rendering, unavailable status, reload persistence,
mobile layout, removal, failed-poll recovery, assignment after WebGL loss, and
clearing map details on logout. It asserts zero device actions. Screenshots and
logs stay in ignored `artifacts/map-lights/`. Set `CHROME_BIN` if needed; the
script requires the head-end environment plus `websockets` and Chrome/Chromium.
