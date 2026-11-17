#!/usr/bin/env python3
"""Turn songs.txt ("Artist - Title" per line, optional "| year" and "#tags") into songs.js.

Looks the songs up in Apple's public iTunes Search API (one request per artist,
plus one per song it couldn't find that way) and keeps each track's id, year,
genre, artwork and 30-second preview URL. Songs Apple has no preview for are
looked up on Deezer instead (marked "source": "deezer"); their preview links
expire, so the page fetches a fresh one at play time. A popularity rank from Deezer's public
search decides which songs count as hits. Songs already in songs.js are kept, so
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


# A song counts as a hit if its Deezer rank (0 to 1,000,000) is at least HIT_RANK, or it is in the top
# HIT_SHARE of its artist's songs by rank (Deezer's audience skews European, so a flat cutoff alone would
# drop most of some US rappers' biggest songs). Songs Deezer can't find count as hits.
HIT_RANK = 650_000
HIT_SHARE = 0.34

# Apple's genre names, folded into the genre chips on the start screen.
GENRES = [("hip-hop", "Rap"), ("rap", "Rap"), ("r&b", "R&B"), ("soul", "R&B"), ("funk", "R&B"),
          ("dance", "House/EDM"), ("electronic", "House/EDM"), ("house", "House/EDM"),
          ("techno", "House/EDM"), ("electro", "House/EDM"), ("rock", "Rock"), ("alternative", "Rock"), ("metal", "Rock"),
          ("punk", "Rock"), ("pop", "Pop"), ("latin", "Pop"), ("country", "Pop")]


def genre_of(apple_genre):
    low = (apple_genre or "").lower()
    return next((label for key, label in GENRES if key in low), "Pop")


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
    """Fill in credits and genre for songs saved before those fields existed."""
    stale = [s for s in found.values() if s.get("source") != "deezer" and ("artists" not in s or "genre" not in s)]
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
                s["genre"] = genre_of(r.get("primaryGenreName"))
        time.sleep(DELAY)


def deezer_rank(song):
    """Deezer's popularity rank for this recording (0 if not found)."""
    url = "https://api.deezer.com/search?" + urllib.parse.urlencode(
        {"q": f"{song['artist']} {core(song['title'])}", "limit": 25})
    out = subprocess.run(["curl", "-s", "-m", "20", url], capture_output=True, text=True).stdout
    try:
        results = json.loads(out).get("data", [])
    except ValueError:
        return None
    who = {norm(a) for a in song.get("artists", [song["artist"]])} | {norm(song["artist"])}
    want = norm(core(song["title"]))
    ranks = [r.get("rank", 0) for r in results
             if norm(core(r.get("title", ""))) == want and norm(r.get("artist", {}).get("name", "")) in who]
    return max(ranks, default=0)


def refresh_ranks(found):
    for song in [s for s in found.values() if not s.get("rank") and s.get("source") != "deezer"]:  # retry misses too
        rank = deezer_rank(song)
        if rank is not None:
            song["rank"] = rank
        time.sleep(0.4)  # Deezer allows 50 requests per 5 seconds; stay well under it


def apple_lookup(ids, **params):
    url = "https://itunes.apple.com/lookup?" + urllib.parse.urlencode(
        {"id": ",".join(map(str, ids)), "country": "US", **params})
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


def refresh_explicit(found):
    """Apple's search API now returns only clean edits, so swap each one for the explicit recording:
    first among the artist's songs, then on the explicit version of the same album."""
    apple = [s for s in found.values() if s.get("source") != "deezer" and s.get("explicitness") in (None, "cleaned")]
    info = {}
    for i in range(0, len(apple), 150):
        for r in apple_lookup([s["id"] for s in apple[i:i + 150]]):
            if r.get("wrapperType") == "track":
                info[r["trackId"]] = r
    songs_of, albums_of, swapped, still_clean = {}, {}, 0, []
    for s in apple:
        r = info.get(s["id"])
        if not r:
            continue
        s["explicitness"] = r.get("trackExplicitness")
        if s["explicitness"] != "cleaned":
            continue
        want, aid = norm(core(r["trackName"])), r["artistId"]

        def match(tracks):
            return next((t for t in tracks if t.get("wrapperType") == "track" and t.get("previewUrl")
                         and t.get("trackExplicitness") == "explicit" and norm(core(t["trackName"])) == want
                         and not (BAD.search(t["trackName"]) and not BAD.search(r["trackName"]))), None)

        if aid not in songs_of:
            songs_of[aid] = apple_lookup([aid], entity="song", limit=200)
        t = match(songs_of[aid])
        if not t:
            if aid not in albums_of:
                albums_of[aid] = [c for c in apple_lookup([aid], entity="album", limit=200)
                                  if c.get("wrapperType") == "collection" and c.get("collectionExplicitness") == "explicit"]
            album = norm(core(r.get("collectionName", "")))
            ids = [c["collectionId"] for c in albums_of[aid] if norm(core(c["collectionName"])) == album]
            if ids:
                t = match(apple_lookup(ids[:10], entity="song", limit=200))
        if t:
            s.update(id=t["trackId"], preview=t["previewUrl"], url=t.get("trackViewUrl", s["url"]),
                     art=t.get("artworkUrl100", "").replace("100x100", "300x300") or s["art"],
                     explicitness="explicit")
            swapped += 1
        else:
            still_clean.append(f"{s['artist']} - {s['title']}")
    print(f"Explicit versions: swapped {swapped} clean edits; {len(still_clean)} have no explicit version on Apple")
    for name in still_clean:
        print("   clean only:", name)


def refresh_deezer_explicit(found):
    """Re-pick Deezer songs chosen before explicit versions were preferred."""
    for key, s in list(found.items()):
        if s.get("source") == "deezer" and "explicitness" not in s:
            found[key] = deezer_find(s["artist"], s["title"]) or {**s, "explicitness": "unknown"}


def deezer_get(path, **params):
    url = f"https://api.deezer.com/{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    out = subprocess.run(["curl", "-s", "-m", "20", url], capture_output=True, text=True).stdout
    time.sleep(0.4)  # Deezer allows 50 requests per 5 seconds; stay well under it
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    return {} if "error" in data else data


def deezer_find(artist, title):
    """Fallback for songs Apple has no preview for: the most popular matching Deezer recording."""
    want_a, want_t = norm(artist), norm(core(title))
    results = deezer_get("search", q=f"{artist} {core(title)}", limit=25).get("data", [])
    hits = [r for r in results
            if r.get("preview") and norm(core(r.get("title", ""))) in (want_t, norm(title))
            and credited(want_a, r.get("artist", {}).get("name", ""), r.get("title", ""))
            and not (BAD.search(r.get("title", "")) and not BAD.search(title))]
    if not hits:
        return None
    best = max(hits, key=lambda r: (bool(r.get("explicit_lyrics")), r.get("rank", 0)))  # uncensored first
    track = deezer_get(f"track/{best['id']}") or best
    names = [c["name"] for c in track.get("contributors", [])] or [best["artist"]["name"]]
    main = best["artist"]["name"]
    album = track.get("album", best.get("album", {}))
    album_info = deezer_get(f"album/{album['id']}") if album.get("id") else {}
    genres = album_info.get("genres", {}).get("data", [])
    date = track.get("release_date") or album_info.get("release_date") or ""
    if not date[:4].isdigit() or date[:4] == "0000":
        return None  # no usable release year
    return {"id": f"dz{best['id']}", "source": "deezer", "dz": best["id"], "title": title, "artist": artist,
            "credit": main + (" feat. " + " & ".join(n for n in names if norm(n) != norm(main)) if len(names) > 1 else ""),
            "artists": performers(", ".join(names), best.get("title", "")),
            "year": int(date[:4]) if date[:4].isdigit() else 0,
            "genre": genre_of(genres[0]["name"] if genres else ""),
            "rank": best.get("rank", 0),
            "art": album.get("cover_big") or album.get("cover_medium", ""),
            "url": track.get("link", ""), "preview": best["preview"],
            "explicitness": "explicit" if best.get("explicit_lyrics") else "notExplicit"}


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
            "genre": genre_of(best.get("primaryGenreName")),
            "art": best.get("artworkUrl100", "").replace("100x100", "300x300"),
            "url": best.get("trackViewUrl", ""), "preview": best["previewUrl"]}


def main():
    wanted, years, tags = [], {}, {}
    for line in SRC.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and " - " in line:
            line_tags = re.findall(r"\s#(\w+)", line)
            line = re.sub(r"\s#\w+", "", line)
            line, _, year = line.partition("|")
            artist, title = line.split(" - ", 1)
            key = (artist.strip(), title.strip())
            if key not in tags:
                wanted.append(key)
                tags[key] = set()
            tags[key].update(line_tags)
            if year.strip():
                years[key] = int(year)

    found = {}
    if OUT.exists():
        m = re.search(r"=\s*(\[.*\]);?\s*$", OUT.read_text(encoding="utf-8"), re.S)
        if m:
            found = {(s["artist"], s["title"]): s for s in json.loads(m.group(1))}

    def is_hit(song, ranks_by_artist):
        rank = song.get("rank", 0)
        if not rank:
            return True
        ranks = ranks_by_artist[song["artist"]]  # sorted high to low
        return rank >= HIT_RANK or rank >= ranks[max(1, round(len(ranks) * HIT_SHARE)) - 1]

    def save():
        ranks_by_artist = {}
        for s in found.values():
            if s.get("rank"):
                ranks_by_artist.setdefault(s["artist"], []).append(s["rank"])
        for r in ranks_by_artist.values():
            r.sort(reverse=True)
        def who(song):
            return {norm(a) for a in song.get("artists", [])} | {norm(song["artist"])}

        songs, seen, by_title = [], set(), {}
        for key in wanted:
            song = found.get(key)
            # The same song listed under two artists (e.g. a feature) can resolve to two releases,
            # such as a single and an album version. Keep the first and merge the other's tags into it.
            twin = next((t for t in by_title.get(norm(core(song["title"])), []) if who(t) & who(song)), None) if song else None
            if twin is not None:
                if tags[key]:
                    twin["tags"] = sorted(set(twin.get("tags", [])) | tags[key])
                continue
            if song and song["id"] not in seen:
                seen.add(song["id"])
                out = {**song, "year": years.get(key, song["year"]), "hit": is_hit(song, ranks_by_artist)}
                out.pop("tags", None)
                if tags[key]:
                    out["tags"] = sorted(tags[key])
                songs.append(out)
                by_title.setdefault(norm(core(out["title"])), []).append(out)
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
            song = (pick(catalogue, *key) or pick(search(term=f"{artist} {core(key[1])}", limit=25), *key)
                    or deezer_find(*key))
            if song:
                found[key] = song
        got = sum(k in found for k in todo)
        print(f"{artist}: {got}/{len(todo)} new", flush=True)
        save()
    refresh_credits(found)
    refresh_explicit(found)
    refresh_deezer_explicit(found)
    refresh_ranks(found)

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
