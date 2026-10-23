# Map provider decision

Chosen: **MapLibre GL JS** (renderer) + **OpenFreeMap** vector tiles (OpenStreetMap data, OpenMapTiles schema).

| | Yandex Maps JS API | MapLibre + OpenFreeMap |
|---|---|---|
| Free quota | free tier published (10M JS loads/yr per search results; confirm on official page) | public instance states no view/request limits, no API key, no registration |
| Public/commercial use | free tier only for sites open to everyone | allowed with attribution (shown automatically) |
| Billing details | API key tied to Yandex account | none |
| Store/copy provider data | **forbidden** on free tier (data may not be stored or altered) | ODbL: allowed with attribution/share-alike; we still store none |
| Mandatory branding | Yandex logo + "Open in Maps" button | OpenFreeMap / OpenMapTiles / OSM attribution |
| Tap built-in POIs | not confirmed for JS API v3 (only `hotspot` objects documented) | yes: `queryRenderedFeatures` on the `poi` layer, read-only in the browser |
| If quota exceeded | not verified | donation-funded, **no SLA**; may throttle or disappear |

## Behaviour implemented
- Provider POIs (`class=place_of_worship` whose name contains masjid/мечеть/…) are readable on tap. The name is shown transiently in a popup and **never stored**.
  The tile schema has no religion field, so a tapped POI may be a church/synagogue; the popup says so.
- If one of our records is within 50 m, the popup offers "open our page"; otherwise "add to our database". Adding
  starts the normal add flow at the tapped coordinates; **no name/address is prefilled from provider data**.
- Navigation: "Yandex Maps'da ochish" and "Yo'nalish" are plain links to yandex.com/maps (external app/site).
  We send Yandex only the mosque's own coordinates, via the user's click; we call no Yandex API, so no key or quota.

## Fallbacks
Set `TILE_STYLE_URL` to another style: self-hosted OpenFreeMap (MIT, needs ~1.5 TB for planet; use a regional extract),
Protomaps PMTiles file served from your own server (Uzbekistan extract is small), or a paid provider.
The OSM standard raster tiles (tile.openstreetmap.org) must NOT be used for a production app (usage policy).

## Existing mosques: what works and what does not
Checked on live tiles (central Tashkent): the OSM tile `poi` layer contains places of worship of all religions and has
**no religion field**. Names like "Umar ibn Xattob" or a misspelt "masjd" carry no mosque keyword, so a name filter in the
browser misses real mosques. It is therefore only an interim layer (`SHOW_TILE_MOSQUES=1`).
The reliable source is OSM's `amenity=place_of_worship` + `religion=muslim` tags, imported with `server/import_osm.py`
(Overpass or a saved Overpass JSON). Imported rows are `origin=imported`, `source=openstreetmap`, labelled
"OpenStreetMap ma'lumoti · tasdiqlanmagan", and the OSM-derived part is published at `/api/export/osm-derived.geojson`
(ODbL share-alike). After importing, set `SHOW_TILE_MOSQUES=0`.
**Licence note:** ODbL applies to this imported subset even though the source code stays private; attribution is
shown in the app and on each imported record.
