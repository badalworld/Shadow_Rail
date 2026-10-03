# Drop-in slot: licensed Renderpeople scans

The agents in the 3D headquarters are **procedural** bodies modelled on the
[Renderpeople](https://renderpeople.com/3d-people) office-people catalogue
(sitting · typing · standing · walking, business / smart-casual clothing, the
full range of builds, ages and ethnicities).  Their scans are licensed, paid
assets, so none of that geometry ships with this repository.

If you own a Renderpeople licence, put the GLB files here and the room will use
them instead of the procedural cast:

```
frontend/public/models/people/<bot_id>.glb      # e.g. ceo-bot.glb
```

then build (or run the dev server) with `VITE_HQ_SCANS=on`:

```bash
VITE_HQ_SCANS=on npm run build
```

Vite copies this folder to `backend/web/models/`, and FastAPI serves it with
`Cache-Control: immutable` (see `_asset_cache_headers` in `backend/app/api.py`).
Scans are expected to be Y-up, ~1.75 m tall, with their feet at y = 0 and facing
+z; `buildLook()` in `frontend/src/components/hq/layout.ts` supplies the height
and build fallback for any agent without a scan.

Nothing here is downloaded automatically — the dashboard never reaches out to
renderpeople.com at runtime.
