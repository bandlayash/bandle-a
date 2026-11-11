#!/usr/bin/env python3
"""Turn songs.txt ("Artist - Title" per line, optional "| year") into songs.js.

Looks the songs up in Apple's public iTunes Search API (one request per artist,
plus one per song it couldn't find that way) and keeps each track's id, year,
artwork and 30-second preview URL. Songs already in songs.js are kept, so
re-running after adding a few lines is quick.

    python3 build_songs.py
"""
import json, re, subprocess, sys, time, unicodedata, urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
SRC, OUT = HERE / "songs.txt", HERE / "songs.js"
DELAY = 3.2  # Apple allows roughly 20 searches a minute
BAD = re.compile(r"karaoke|tribute|\blive\b|remix|re-?recorded|instrumental|cover|"
                 r"made famous|acoustic|demo|\bedit\b|mix\)|version\)|sped up|slowed|a cappella",
                 re.I)


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s.replace("&", "and"))


def core(title):
    """Title without trailing (...) / [...] / ' - Remastered' decorations."""
    t = re.sub(r"\s*[\(\[].*?[\)\]]", "", title)
    return re.sub(r"\s+-\s+.*$", "", t).strip() or title


def search(**params):
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {"entity": "song", "country": "US", **params})
    for _ in range(4):
        out = subprocess.run(["curl", "-s", "-m", "30", "-w", "\n%{http_code}", url],
                             capture_output=True, text=True).stdout
        body, _, code = out.rpartition("\n")
        time.sleep(DELAY)
        if code == "200":
            return json.loads(body).get("results", [])
        print(f"   (HTTP {code}, waiting 60s)", flush=True)
        time.sleep(60)
    return []


def credited(want_a, artist_name, track_name):
    """True if the artist is a named performer, or is featured in the track title."""
    names = re.split(r",|&|\band\b|\bx\b|\bfeat\.?|\bwith\b", artist_name, flags=re.I)
    if want_a == norm(artist_name) or want_a in {norm(n) for n in names}:
        return True
    return any(want_a in norm(g) for g in re.findall(r"[\(\[](?:feat|with)[^\)\]]*", track_name, re.I))


def pick(results, artist, title):
    """Best result that is this artist's (or features them) studio recording of this title."""
    want_a, want_t = norm(artist), norm(core(title))
    hits = []
    for r in results:
        name = r.get("trackName", "")
        if not r.get("previewUrl") or r.get("kind") != "song":
            continue
        if not credited(want_a, r.get("artistName", ""), name):
            continue
        if norm(core(name)) != want_t and norm(name) != norm(title):
            continue
        if BAD.search(name) and not BAD.search(title):
            continue
        if re.search(r"karaoke|tribute|\blive\b", r.get("collectionName", ""), re.I):
            continue
        hits.append(r)
    hits.sort(key=lambda r: r.get("trackExplicitness") == "cleaned")  # stable: uncensored first
    if not hits:
        return None
    best = hits[0]
    return {"id": best["trackId"], "title": title, "artist": artist,
            "credit": best["artistName"], "year": int(best["releaseDate"][:4]),
            "art": best.get("artworkUrl100", "").replace("100x100", "300x300"),
            "url": best.get("trackViewUrl", ""), "preview": best["previewUrl"]}


def main():
    wanted, years = [], {}
    for line in SRC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and " - " in line:
            line, _, year = line.partition("|")
            artist, title = line.split(" - ", 1)
            key = (artist.strip(), title.strip())
            wanted.append(key)
            if year.strip():
                years[key] = int(year)

    found = {}
    if OUT.exists():
        m = re.search(r"=\s*(\[.*\]);?\s*$", OUT.read_text(encoding="utf-8"), re.S)
        if m:
            found = {(s["artist"], s["title"]): s for s in json.loads(m.group(1))}

    def save():
        songs, seen = [], set()
        for key in wanted:
            song = found.get(key)
            if song and song["id"] not in seen:
                seen.add(song["id"])
                songs.append({**song, "year": years.get(key, song["year"])})
        OUT.write_text("window.SONGS = " + json.dumps(songs, ensure_ascii=False, indent=1)
                       + ";\n", encoding="utf-8")
        return len(songs)

    for artist in dict.fromkeys(a for a, _ in wanted):
        todo = [k for k in wanted if k[0] == artist and k not in found]
        if not todo:
            continue
        catalogue = search(term=artist, attribute="artistTerm", limit=200)
        for key in todo:
            song = pick(catalogue, *key) or pick(search(term=f"{artist} {core(key[1])}", limit=25), *key)
            if song:
                found[key] = song
        got = sum(k in found for k in todo)
        print(f"{artist}: {got}/{len(todo)} new", flush=True)
        save()

    print(f"\n{save()} songs written to songs.js")
    missing = [k for k in wanted if k not in found]
    if missing:
        print("Not found (check spelling, or Apple has no preview):")
        for a, t in missing:
            print(f"  {a} - {t}")


if __name__ == "__main__":
    sys.exit(main())
