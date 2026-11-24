#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_shared.py -- list and download the spin-noise bundles shared with the
CASPEr collaboration (DATA_POLICY.md v1.1, "Access by the CASPEr
collaboration").  Python 3.6+, standard library only.

You need the personal access key the coordinators gave you.  It is yours
alone: do not pass it on.  Put it in the environment (never on the command
line, where it would land in your shell history):

    export SPIN_NOISE_SHARED_KEY=...          # or: --key-file ~/.spin_noise_key

    python3 analysis/fetch_shared.py list
    python3 analysis/fetch_shared.py get uni-oulu            # every bundle whose name contains it
    python3 analysis/fetch_shared.py get 20261006 --out data # into ./data

Each download is checked against the sha256 the facility's uploader
declared.  Then:  python3 analysis/facility_report.py <bundle.zip>

What the key allows: listing and downloading the bundles of the facilities
that agreed to share them.  Nothing else, and nothing of other facilities.

By using the key you accept the data policy: the bundles are for quality
assurance, analysis and aggregation; they and any per-facility numbers
derived from them are not passed on outside the collaboration; the data
remain the facilities'; if they enter a paper, the facility's people are
offered authorship and see it before submission.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_ENDPOINT = "https://spin-noise-ingest.johnwblanchard.workers.dev"
USER_AGENT = "spin-noise-fetch-shared/1.0"
CHUNK = 1 << 20


def die(msg):
    sys.stderr.write("ERROR: %s\n" % msg)
    sys.exit(1)


def read_key(args):
    key = os.environ.get("SPIN_NOISE_SHARED_KEY", "").strip()
    if args.key_file:
        try:
            with open(os.path.expanduser(args.key_file), "r") as fh:
                key = fh.read().strip()
        except OSError as exc:
            die("cannot read the key file: %s" % exc)
    if not key:
        die("no access key: set SPIN_NOISE_SHARED_KEY or pass --key-file "
            "(the coordinators send each collaboration member a personal key)")
    return key


def request(base, path, key):
    req = urllib.request.Request(base.rstrip("/") + path)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("User-Agent", USER_AGENT)
    try:
        return urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = json.loads(exc.read().decode("utf-8", "replace")).get("error", "")
        except Exception:
            pass
        if exc.code == 401:
            die("the server did not accept this key (401). Keys are personal; "
                "ask the coordinators if yours should still work.")
        if exc.code == 404:
            die("no such shared bundle (404).")
        die("server answered %d %s" % (exc.code, body))
    except urllib.error.URLError as exc:
        die("cannot reach %s: %s" % (base, exc.reason))


def listing(base, key):
    with request(base, "/shared/list", key) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_list(args, key):
    d = listing(args.endpoint, key)
    print("shared with the CASPEr collaboration: %d bundle(s) from %s"
          % (d.get("count", 0), ", ".join(d.get("facilities") or []) or "no facility yet"))
    for b in sorted(d.get("bundles") or [], key=lambda b: b.get("uploaded") or ""):
        print("  %9.1f MB  %s  %s" % (b["size"] / 1e6, str(b.get("uploaded"))[:19], b["name"]))
    if d.get("terms"):
        print(d["terms"])


def cmd_get(args, key):
    d = listing(args.endpoint, key)
    picked = [b for b in d.get("bundles") or [] if args.match in b["name"]]
    if not picked:
        die("no shared bundle name contains %r (try: list)" % args.match)
    os.makedirs(args.out, exist_ok=True)
    for b in picked:
        dest = os.path.join(args.out, b["name"])
        if os.path.exists(dest) and os.path.getsize(dest) == b["size"]:
            print("have  %s" % dest)
            continue
        part = dest + ".part"
        h = hashlib.sha256()
        with request(args.endpoint, "/shared/object?key=" + urllib.parse.quote(b["name"]), key) as resp, \
                open(part, "wb") as out:
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
        want = b.get("sha256")
        if want and h.hexdigest() != want:
            os.remove(part)
            die("%s: sha256 mismatch after download (got %s, the facility declared %s)"
                % (b["name"], h.hexdigest(), want))
        os.replace(part, dest)
        print("saved %s (%.1f MB, %s)" % (dest, b["size"] / 1e6,
                                          "sha256 verified" if want else "no sha256 on record"))


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="List and download the bundles shared with the CASPEr collaboration.")
    ap.add_argument("--endpoint", default=os.environ.get("SPIN_NOISE_ENDPOINT", DEFAULT_ENDPOINT),
                    help="server origin (default: the network's ingest server)")
    ap.add_argument("--key-file", default=None,
                    help="file holding your personal access key (else SPIN_NOISE_SHARED_KEY)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list", help="show the shared bundles")
    g = sub.add_parser("get", help="download every shared bundle whose name contains MATCH")
    g.add_argument("match")
    g.add_argument("--out", default=".", help="directory to save into (default: here)")
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 2
    key = read_key(args)
    if args.cmd == "list":
        cmd_list(args, key)
    else:
        cmd_get(args, key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
