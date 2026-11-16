#!/usr/bin/env python3
"""Turn songs.txt ("Artist - Title" per line, optional "| year") into songs.js.

Looks the songs up in Apple's public iTunes Search API (one request per artist,
plus one per song it couldn't find that way) and keeps each track's id, year,
artwork and 30-second preview URL. Songs already in songs.js are kept, so
re-running after adding a few lines is quick.

    python3 build_songs.py
"""
import hashlib, json, re, subprocess, sys, time, unicodedata, urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
SRC, OUT, PAGE = HERE / "songs.txt", HERE / "songs.js", HERE / "index.html"
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


SPLIT = re.compile(r",|&|\band\b|\bfeat\.?|\bft\.?|\bwith\b", re.I)


def performers(artist_name, track_name):
    """Credited artists plus anyone featured in the track title, e.g. 'Song (feat. A & B)'."""
    feats = re.findall(r"[\(\[](?:feat\.?|ft\.?|with)\s+([^\)\]]+)", track_name, re.I)
    names = []
    for part in SPLIT.split(artist_name) + [p for f in feats for p in SPLIT.split(f)]:
        part = part.strip()
        if part and norm(part) not in {norm(n) for n in names}:
            names.append(part)
    return names


def credit_line(artist_name, track_name):
    """Apple's artist credit, with title-only features appended: 'Drake feat. Lil Baby'."""
    extra = [n for n in performers("", track_name) if norm(n) not in norm(artist_name)]
    return artist_name + (" feat. " + " & ".join(extra) if extra else "")


def credited(want_a, artist_name, track_name):
    """True if the artist is a named performer or is featured in the track title."""
    return want_a == norm(artist_name) or want_a in {norm(n) for n in performers(artist_name, track_name)}


def refresh_credits(found):
    """Fill in full credits for songs saved before credits included features."""
    stale = [s for s in found.values() if "artists" not in s]
    for i in range(0, len(stale), 150):
        batch = stale[i:i + 150]
        url = "https://itunes.apple.com/lookup?country=US&id=" + ",".join(str(s["id"]) for s in batch)
        out = subprocess.run(["curl", "-s", "-m", "30", url], capture_output=True, text=True).stdout
        by_id = {r["trackId"]: r for r in json.loads(out).get("results", []) if "trackId" in r}
        for s in batch:
            r = by_id.get(s["id"])
            if r:
                s["credit"] = credit_line(r["artistName"], r["trackName"])
                s["artists"] = performers(r["artistName"], r["trackName"])
        time.sleep(DELAY)


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
            "credit": credit_line(best["artistName"], best["trackName"]),
            "artists": performers(best["artistName"], best["trackName"]),
            "year": int(best["releaseDate"][:4]),
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

    refresh_credits(found)
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
    # Point index.html at this exact version so browsers don't keep a stale cached copy.
    version = hashlib.sha1(OUT.read_bytes()).hexdigest()[:8]
    page = PAGE.read_text(encoding="utf-8")
    PAGE.write_text(re.sub(r'src="songs\.js(\?v=\w+)?"', f'src="songs.js?v={version}"', page), encoding="utf-8")
    missing = [k for k in wanted if k not in found]
    if missing:
        print("Not found (check spelling, or Apple has no preview):")
        for a, t in missing:
            print(f"  {a} - {t}")


if __name__ == "__main__":
    sys.exit(main())
