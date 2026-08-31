#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
registry_report.py -- coordinator pull tool for the facility registry.

    python3 analysis/registry_report.py [--out DIR] [--endpoint URL] [--token T]

Pulls the sign-ups stored by the Worker's facility registry (POST /registry,
fed by the Google-Form forwarder in docs/forms_forwarder.gs) and renders:

  (a) a terminal table of the registered facilities -- the core output;
  (b) registry_facilities.json for downstream tools (coverage map, mailers);
  (c) best-effort sidereal-coverage numbers for the facilities whose city
      resolves in a small built-in gazetteer -- the same variance-inflation
      metric as proposals/geo_coverage.py (needs numpy; silently skipped
      without it, or when no city resolves);
  (d) with --update-map PATH: rewrites the facility-map sentinel blocks
      baked into the website's index.html -- consent_map == true facilities
      ONLY, institution + city only (never contact names/emails), city-level
      gazetteer coordinates. Idempotent; refuses if the sentinels are
      missing; --dry-run prints the would-be blocks instead of writing.

Endpoint + token resolution, in order:
  1. --endpoint / --token flags;
  2. environment: SPIN_NOISE_ENDPOINT, SPIN_NOISE_REGISTRY_TOKEN
     (or SPIN_NOISE_TOKEN);
  3. uploader/config.json: "endpoint_url" (the /ingest URL or bare origin),
     plus the optional "registry_token" key, falling back to "token"
     (GET /registry/list accepts either secret -- the coordinator holds both).

Python 3 stdlib only (urllib); numpy is optional and only enables (c).

Authors: Blanchard, Claude (Anthropic).
Contact: John W. Blanchard <jwbquantum@gmail.com>.
"""

import argparse
import datetime
import html as html_escape
import json
import os
import re
import sys
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
DEFAULT_CONFIG = os.path.join(REPO, "uploader", "config.json")

# ---------------------------------------------------------------------------
# Built-in gazetteer (city-level coordinates, coverage math only).
# Plausibly-expected NMR-hub cities; unresolved cities are simply skipped --
# coverage output is best-effort, the table is the deliverable. Keys are
# lowercase; lookup is by normalized city, with a country-qualified form
# ("cambridge, usa") tried first so transatlantic twins resolve sanely.
# ---------------------------------------------------------------------------

GAZETTEER = {
    # (lat_deg, lon_east_deg)
    "lausanne": (46.5, 6.6), "zurich": (47.4, 8.5), "geneva": (46.2, 6.1),
    "basel": (47.6, 7.6), "villigen": (47.5, 8.2),
    "mainz": (50.0, 8.3), "frankfurt": (50.1, 8.7), "munich": (48.1, 11.6),
    "berlin": (52.5, 13.4), "karlsruhe": (49.0, 8.4), "gottingen": (51.5, 9.9),
    "goettingen": (51.5, 9.9), "julich": (50.9, 6.4), "aachen": (50.8, 6.1),
    "leipzig": (51.3, 12.4), "bayreuth": (49.9, 11.6), "darmstadt": (49.9, 8.7),
    "paris": (48.9, 2.4), "lyon": (45.8, 4.8), "grenoble": (45.2, 5.7),
    "gif-sur-yvette": (48.7, 2.1), "orleans": (47.9, 1.9), "lille": (50.6, 3.1),
    "oxford": (51.8, -1.3), "london": (51.5, -0.1), "birmingham": (52.5, -1.9),
    "nottingham": (52.9, -1.2), "warwick": (52.4, -1.6), "coventry": (52.4, -1.5),
    "st andrews": (56.3, -2.8), "edinburgh": (55.9, -3.2),
    "cambridge, uk": (52.2, 0.1), "cambridge, united kingdom": (52.2, 0.1),
    "cambridge, usa": (42.4, -71.1), "cambridge, united states": (42.4, -71.1),
    "cambridge": (52.2, 0.1),          # bare "Cambridge" defaults to the UK one
    "boston": (42.4, -71.1), "new york": (40.7, -74.0), "new haven": (41.3, -72.9),
    "berkeley": (37.9, -122.3), "san francisco": (37.8, -122.4),
    "stanford": (37.4, -122.2), "la jolla": (32.9, -117.2),
    "chicago": (41.9, -87.6), "urbana": (40.1, -88.2), "champaign": (40.1, -88.2),
    "madison": (43.1, -89.4), "minneapolis": (45.0, -93.3),
    "tallahassee": (30.4, -84.3), "gainesville": (29.7, -82.3),
    "carbondale": (37.7, -89.2),       # Southern Illinois University
    "davis": (38.5, -121.7), "miami": (25.8, -80.2),
    "college station": (30.6, -96.3), "houston": (29.8, -95.4),
    "ithaca": (42.4, -76.5), "baltimore": (39.3, -76.6), "bethesda": (39.0, -77.1),
    "ann arbor": (42.3, -83.7), "columbus": (40.0, -83.0),
    "pittsburgh": (40.4, -80.0), "philadelphia": (40.0, -75.2),
    "seattle": (47.6, -122.3), "portland": (45.5, -122.7),
    "los angeles": (34.1, -118.2), "pasadena": (34.1, -118.1),
    "boulder": (40.0, -105.3), "denver": (39.7, -105.0),
    "toronto": (43.7, -79.4), "montreal": (45.5, -73.6), "vancouver": (49.3, -123.1),
    "ottawa": (45.4, -75.7), "guelph": (43.5, -80.2),
    "tokyo": (35.7, 139.7), "kyoto": (35.0, 135.8), "osaka": (34.7, 135.5),
    "yokohama": (35.4, 139.6), "sendai": (38.3, 140.9), "nagoya": (35.2, 136.9),
    "seoul": (37.6, 127.0), "daejeon": (36.4, 127.4),
    "beijing": (39.9, 116.4), "shanghai": (31.2, 121.5), "wuhan": (30.6, 114.3),
    "hefei": (31.8, 117.2), "hong kong": (22.3, 114.2), "taipei": (25.0, 121.6),
    "singapore": (1.35, 103.8),
    "hyderabad": (17.4, 78.5), "bangalore": (13.0, 77.6), "bengaluru": (13.0, 77.6),
    "mumbai": (19.1, 72.9), "delhi": (28.6, 77.2), "new delhi": (28.6, 77.2),
    "pune": (18.5, 73.9), "kolkata": (22.6, 88.4), "chennai": (13.1, 80.3),
    "melbourne": (-37.8, 145.0), "sydney": (-33.9, 151.2), "brisbane": (-27.5, 153.0),
    "perth": (-31.9, 115.9), "adelaide": (-34.9, 138.6), "auckland": (-36.8, 174.8),
    "sao paulo": (-23.5, -46.6), "campinas": (-22.9, -47.1),
    "rio de janeiro": (-22.9, -43.2), "buenos aires": (-34.6, -58.4),
    "santiago": (-33.4, -70.7), "mexico city": (19.4, -99.1),
    "cape town": (-33.9, 18.4), "johannesburg": (-26.2, 28.0),
    "stellenbosch": (-33.9, 18.9), "cairo": (30.0, 31.2),
    "tel aviv": (32.1, 34.8), "jerusalem": (31.8, 35.2), "rehovot": (31.9, 34.8),
    "haifa": (32.8, 35.0), "istanbul": (41.0, 28.9), "ankara": (39.9, 32.9),
    "moscow": (55.8, 37.6), "novosibirsk": (55.0, 82.9), "kazan": (55.8, 49.1),
    "warsaw": (52.2, 21.0), "krakow": (50.1, 19.9), "prague": (50.1, 14.4),
    "brno": (49.2, 16.6), "vienna": (48.2, 16.4), "budapest": (47.5, 19.0),
    "ljubljana": (46.1, 14.5), "bratislava": (48.1, 17.1),
    "stockholm": (59.3, 18.1), "gothenburg": (57.7, 12.0), "goteborg": (57.7, 12.0),
    "umea": (63.8, 20.3), "lund": (55.7, 13.2), "copenhagen": (55.7, 12.6),
    "aarhus": (56.2, 10.2), "helsinki": (60.2, 24.9), "oulu": (65.0, 25.5),
    "oslo": (59.9, 10.8), "trondheim": (63.4, 10.4),
    "amsterdam": (52.4, 4.9), "utrecht": (52.1, 5.1), "nijmegen": (51.8, 5.9),
    "leiden": (52.2, 4.5), "groningen": (53.2, 6.6), "eindhoven": (51.4, 5.5),
    "wageningen": (52.0, 5.7), "delft": (52.0, 4.4),
    "brussels": (50.8, 4.4), "leuven": (50.9, 4.7), "ghent": (51.1, 3.7),
    "gent": (51.1, 3.7), "liege": (50.6, 5.6),
    "madrid": (40.4, -3.7), "barcelona": (41.4, 2.2), "bilbao": (43.3, -2.9),
    "valencia": (39.5, -0.4), "seville": (37.4, -6.0), "santiago de compostela": (42.9, -8.5),
    "lisbon": (38.7, -9.1), "lisboa": (38.7, -9.1), "porto": (41.1, -8.6),
    "coimbra": (40.2, -8.4), "aveiro": (40.6, -8.7),
    "milan": (45.5, 9.2), "milano": (45.5, 9.2), "florence": (43.8, 11.2),
    "firenze": (43.8, 11.2), "rome": (41.9, 12.5), "roma": (41.9, 12.5),
    "naples": (40.9, 14.3), "napoli": (40.9, 14.3), "bologna": (44.5, 11.3),
    "turin": (45.1, 7.7), "torino": (45.1, 7.7), "padua": (45.4, 11.9),
    "athens": (38.0, 23.7), "thessaloniki": (40.6, 23.0),
    "dublin": (53.3, -6.3), "cork": (51.9, -8.5), "galway": (53.3, -9.0),
    "reykjavik": (64.1, -21.9),
}

SID = 1.00273781191135448  # sidereal days per solar day (geo_coverage.py)


# ---------------------------------------------------------------------------
# Config / fetch
# ---------------------------------------------------------------------------

def endpoint_base(url):
    """Accept the /ingest URL, the /registry/list URL, or a bare origin;
    return the bare origin (same forgiveness as the uploader)."""
    url = url.strip().rstrip("/")
    for suffix in ("/ingest", "/registry/list", "/registry"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url.rstrip("/")


def resolve_endpoint_token(args):
    endpoint = args.endpoint or os.environ.get("SPIN_NOISE_ENDPOINT") or ""
    token = (args.token or os.environ.get("SPIN_NOISE_REGISTRY_TOKEN")
             or os.environ.get("SPIN_NOISE_TOKEN") or "")
    if endpoint and token:
        return endpoint_base(endpoint), token
    cfg_path = args.config or DEFAULT_CONFIG
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as exc:
            sys.exit("ERROR: cannot read config %s: %s" % (cfg_path, exc))
        endpoint = endpoint or cfg.get("endpoint_url", "")
        token = token or cfg.get("registry_token") or cfg.get("token") or ""
    if not endpoint or not token:
        sys.exit(
            "ERROR: no endpoint/token. Provide --endpoint/--token, set "
            "SPIN_NOISE_ENDPOINT + SPIN_NOISE_REGISTRY_TOKEN, or fill "
            "uploader/config.json (optional key: registry_token)."
        )
    return endpoint_base(endpoint), token


def fetch_registry(base, token):
    req = urllib.request.Request(
        base + "/registry/list",
        headers={"Authorization": "Bearer " + token,
                 "User-Agent": "spin-noise-registry-report/0.5.2"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        sys.exit("ERROR: %s /registry/list -> HTTP %d\n%s" % (base, exc.code, body))
    except (urllib.error.URLError, OSError) as exc:
        sys.exit("ERROR: cannot reach %s: %s" % (base, exc))
    if not data.get("ok"):
        sys.exit("ERROR: server answered ok=false: %s" % data.get("error"))
    return data


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

def first_line(text, maxlen):
    s = str(text or "").splitlines()[0] if text else ""
    return s if len(s) <= maxlen else s[: maxlen - 1] + "…"


def render_table(facilities):
    cols = [
        ("institution", "Institution", 28),
        ("contact_name", "Contact", 18),
        ("contact_email", "Email", 26),
        ("city", "City", 16),
        ("country", "Country", 14),
        ("spectrometers", "Spectrometer(s)", 30),
        ("_consent", "Consent", 11),
        ("submitted_at", "Submitted", 20),
    ]
    rows = []
    for f in facilities:
        consent = ("contact" if f.get("consent_contact") else "-") + "/" + \
                  ("map" if f.get("consent_map") else "-")
        row = []
        for key, _, width in cols:
            if key == "_consent":
                row.append(first_line(consent, width))
            elif key == "submitted_at":
                row.append(first_line(str(f.get(key, ""))[:19], width))
            else:
                row.append(first_line(f.get(key, ""), width))
        rows.append(row)
    widths = [max(len(hdr), max((len(r[i]) for r in rows), default=0))
              for i, (_, hdr, _) in enumerate(cols)]
    fmt = "  ".join("%%-%ds" % w for w in widths)
    lines = [fmt % tuple(hdr for _, hdr, _ in cols),
             fmt % tuple("-" * w for w in widths)]
    lines += [fmt % tuple(r) for r in rows]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Best-effort sidereal coverage (geo_coverage.py math, minimal reimplementation)
# ---------------------------------------------------------------------------

def resolve_coords(facility):
    """(lat, lon) from the gazetteer, or None. Tries 'city, country' first,
    then the bare city, then the raw combined field, then the city's first
    comma-component (the forwarder splits 'Carbondale, Illinois, USA' on the
    LAST comma, so city arrives as 'Carbondale, Illinois' -- the gazetteer
    keys plain city names)."""
    city = str(facility.get("city", "")).strip().lower()
    country = str(facility.get("country", "")).strip().lower()
    raw = str(facility.get("city_country_raw", "")).strip().lower()
    city0 = city.split(",")[0].strip()
    for key in ("%s, %s" % (city, country), city, raw,
                "%s, %s" % (city0, country), city0):
        key = key.strip(" ,")
        if key in GAZETTEER:
            return GAZETTEER[key]
    return None


def sidereal_coverage(sites, np, n_days=14, start_h=20.0, end_h=30.0):
    """VIF for fitting [1, sin, cos] of Earth Rotation Angle at fixed total
    row budget, night-only observing -- identical math to
    proposals/geo_coverage.py sidereal_vif() (full 10 h nights)."""
    ts, step_min = [], 10.0
    for _, lon in sites:
        for d in range(n_days):
            t0 = d + (start_h - lon / 15.0) / 24.0
            t1 = d + (end_h - lon / 15.0) / 24.0
            ts.append(np.arange(t0, t1, step_min / 60.0 / 24.0))
    t = np.concatenate(ts)
    n = len(t)
    era = (0.779 + SID * t) % 1.0
    ph = 2 * np.pi * era
    X = np.column_stack([np.ones(n), np.sin(ph), np.cos(ph)])
    C = np.linalg.inv(X.T @ X) * n
    vif_amp = float(np.sqrt(max(C[1, 1], C[2, 2]) / 2.0))
    vif_const = float(np.sqrt(C[0, 0]))
    bins = np.histogram(era, bins=24, range=(0, 1))[0]
    coverage = float((bins > 0).mean())
    return {"coverage_fraction": coverage, "vif_const": vif_const,
            "vif_sidereal_amp": vif_amp, "n_rows": int(n),
            "n_days": n_days, "night_hours": end_h - start_h}


def coverage_section(facilities):
    """Returns (report_dict_or_None, printable_lines)."""
    try:
        import numpy as np
    except ImportError:
        return None, ["(sidereal coverage skipped: numpy not available)"]
    resolved, unresolved = [], []
    for f in facilities:
        coords = resolve_coords(f)
        if coords:
            resolved.append((f, coords))
        else:
            unresolved.append(f)
    if not resolved:
        return None, ["(sidereal coverage skipped: no facility city resolved "
                      "in the built-in gazetteer)"]
    sites = [c for _, c in resolved]
    try:
        cov = sidereal_coverage(sites, np)
    except Exception as exc:  # best-effort by design
        return None, ["(sidereal coverage skipped: %s)" % exc]
    lines = [
        "Sidereal-phase coverage (night-only observing, %d nights of %g h, "
        "fixed total row budget;" % (cov["n_days"], cov["night_hours"]),
        "same variance-inflation metric as proposals/geo_coverage.py):",
        "",
        "  sites with resolved coordinates : %d of %d"
        % (len(resolved), len(facilities)),
        "  sidereal-circle coverage        : %.0f%% (24 x 15-deg ERA bins hit)"
        % (100 * cov["coverage_fraction"]),
        "  VIF, constant term              : %.2f" % cov["vif_const"],
        "  VIF, sin/cos amplitude          : %.2f  (1.00 = uniform sampling)"
        % cov["vif_sidereal_amp"],
    ]
    for f, (lat, lon) in resolved:
        lines.append("    %-28s lat %+6.1f  lon %+7.1f"
                     % (first_line(f.get("institution", "?"), 28), lat, lon))
    for f in unresolved:
        lines.append("    %-28s city %r not in the gazetteer -- skipped"
                     % (first_line(f.get("institution", "?"), 28),
                        first_line(f.get("city", ""), 24)))
    cov["sites"] = [
        {"institution": f.get("institution"), "lat": lat, "lon_east": lon}
        for f, (lat, lon) in resolved
    ]
    return cov, lines


# ---------------------------------------------------------------------------
# Website facility map (--update-map): rewrite the sentinel blocks baked into
# website/index.html. Static generation, privacy by construction:
#   * only facilities with consent_map == True (strict boolean) are placed;
#   * only institution + city ever reach the page -- never contact names or
#     emails, which exist in the registry but are filtered out here;
#   * coordinates are the gazetteer's city-level 1-decimal-degree values.
# The page's SVG is equirectangular with viewBox "0 14 1000 403":
#   x = (lon + 180) / 360 * 1000 ;  y = (90 - lat) / 180 * 500.
# ---------------------------------------------------------------------------

MAP_DATA_START = "<!-- REGISTRY-MAP-DATA-START -->"
MAP_DATA_END = "<!-- REGISTRY-MAP-DATA-END -->"
MAP_CAPTION_START = "<!-- REGISTRY-MAP-CAPTION-START -->"
MAP_CAPTION_END = "<!-- REGISTRY-MAP-CAPTION-END -->"


def latlon_to_svg(lat, lon):
    return (round((lon + 180.0) / 360.0 * 1000.0, 1),
            round((90.0 - lat) / 180.0 * 500.0, 1))


def plural(n, noun):
    return "%d %s%s" % (n, noun, "" if n == 1 else "s")


def build_map_markers(facilities):
    """(marker_lines, caption_text, n_placed, skipped_institutions).
    Filters to consent_map == True ONLY and emits institution + city only."""
    consented = [f for f in facilities if f.get("consent_map") is True]
    markers, skipped, fields = [], [], set()
    for f in sorted(consented, key=lambda f: str(f.get("institution", ""))):
        coords = resolve_coords(f)
        if coords is None:
            skipped.append(str(f.get("institution", "?")))
            continue
        x, y = latlon_to_svg(*coords)
        label = html_escape.escape(
            "%s — %s" % (str(f.get("institution", "")).strip(),
                              str(f.get("city", "")).strip()))
        markers.append(
            '      <g class="fac"><title>%s</title>'
            '<circle class="fac-glow" cx="%g" cy="%g" r="7"/>'
            '<circle class="fac-dot" cx="%g" cy="%g" r="2.5"/></g>'
            % (label, x, y, x, y))
        for m in re.findall(r"(\d+(?:\.\d+)?)\s*mhz", str(f.get("spectrometers", "")),
                            re.IGNORECASE):
            fields.add(float(m))
    n_placed = len(markers)
    caption = "%s &#183; %s &#183; updated %s" % (
        "1 facility" if n_placed == 1 else "%d facilities" % n_placed,
        plural(len(fields), "distinct field"),
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d"))
    return markers, caption, n_placed, skipped


def splice_sentinels(text, start, end, replacement, path):
    """Replace what sits between one start/end sentinel pair (sentinels kept).
    Refuses (exits) unless exactly one well-ordered pair exists."""
    if text.count(start) != 1 or text.count(end) != 1:
        sys.exit("ERROR: %s: expected exactly one %s / %s sentinel pair -- "
                 "refusing to touch the file." % (path, start, end))
    i = text.index(start) + len(start)
    j = text.index(end)
    if j < i:
        sys.exit("ERROR: %s: sentinel %s precedes %s -- refusing." % (path, end, start))
    return text[:i] + replacement + text[j:]


def update_map(html_path, facilities, dry_run=False):
    markers, caption, n_placed, skipped = build_map_markers(facilities)
    data_block = ("\n" + "\n".join(markers) + "\n      ") if markers else "\n      "
    try:
        with open(html_path, "r", encoding="utf-8") as fh:
            page = fh.read()
    except OSError as exc:
        sys.exit("ERROR: cannot read %s: %s" % (html_path, exc))
    page = splice_sentinels(page, MAP_DATA_START, MAP_DATA_END, data_block, html_path)
    page = splice_sentinels(page, MAP_CAPTION_START, MAP_CAPTION_END, caption, html_path)
    print("\nFacility map: %d marker(s) (consent_map only; institution + city only)"
          % n_placed)
    for inst in skipped:
        print("  %s: city not in the gazetteer -- consented but NOT placed"
              % first_line(inst, 40))
    if dry_run:
        print("--dry-run: would write to %s:" % html_path)
        print(MAP_DATA_START + data_block + MAP_DATA_END)
        print(MAP_CAPTION_START + caption + MAP_CAPTION_END)
        return
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(page)
    print("rewrote sentinel blocks in %s (caption: %s)"
          % (html_path, caption.replace("&#183;", "·")))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Pull the facility registry and render the coordinator report.")
    ap.add_argument("--config", help="path to uploader-style config.json "
                    "(default: uploader/config.json)")
    ap.add_argument("--endpoint", help="Worker origin or /ingest URL "
                    "(overrides config/env)")
    ap.add_argument("--token", help="REGISTRY_TOKEN or INGEST_TOKEN "
                    "(overrides config/env)")
    ap.add_argument("--out", default=".", help="directory for "
                    "registry_facilities.json (default: current directory)")
    ap.add_argument("--update-map", metavar="INDEX_HTML",
                    help="rewrite the facility-map sentinel blocks in the "
                    "given website index.html (consent_map facilities only; "
                    "institution + city only, never contact details)")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --update-map: print the would-be sentinel "
                    "blocks instead of writing the file")
    args = ap.parse_args()

    base, token = resolve_endpoint_token(args)
    data = fetch_registry(base, token)

    facilities = []
    for entry in data.get("entries", []):
        sub = entry.get("submission")
        f = dict(sub) if isinstance(sub, dict) else {"_unparseable": True}
        f["_registry_key"] = entry.get("key")
        f["_stored_at"] = entry.get("uploaded")
        facilities.append(f)

    print("Facility registry @ %s" % base)
    print("%d sign-up(s) stored%s\n"
          % (len(facilities), " (listing truncated)" if data.get("truncated") else ""))
    if facilities:
        print(render_table(facilities))
    else:
        print("(registry is empty)")

    out_path = os.path.join(args.out, "registry_facilities.json")
    cov, cov_lines = coverage_section(facilities)
    print()
    for line in cov_lines:
        print(line)

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds"),
        "endpoint": base,
        "count": len(facilities),
        "facilities": facilities,
        "sidereal_coverage": cov,
    }
    os.makedirs(args.out or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print("\nwrote %s" % out_path)

    if args.update_map:
        update_map(args.update_map, facilities, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
