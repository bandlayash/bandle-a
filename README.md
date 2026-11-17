# Bandla(e)

A song-guessing game that runs entirely in the browser. Each song starts as drums alone; add bass, instruments and vocals one layer at a time and name the track from as few layers as you can.

- A run is 5 songs. Each is worth 10,000 points, minus 2,000 for every layer you add and 500 for every wrong guess. Giving up scores 0.
- On the start screen, filter by genre, artist (searchable, A–Z), release years and "hits only", and pick a colour scheme (Dark, Mono, Vivid, Ocean, Sunset, Forest, Midnight or the animated Faraz). The 👅 genre chip switches to a red bedroom-R&B Freak mode, and a secret code in the ⋯ menu unlocks Akul mode.
- In the game, type your guess in the panel left of the waveforms (results show each song's release year), click a layer's name to mute it, click or drag on the waveform to seek, press ⏮ to jump back to the start, and click an artist in the side list to browse their songs. A correct guess gets confetti (emoji confetti in Freak and Akul modes). Revealed songs link to Apple Music or Deezer, and to a Spotify search for the track.
- Songs use the explicit (uncensored) recording wherever one exists.
- Songs don't repeat: the browser remembers what you've heard and only deals unheard songs until a pool is used up. Best scores are kept per mode.
- The site asks for a password first. This is a light lock only: GitHub Pages serves every file publicly, so anyone reading the code can get past it. Only a SHA-256 hash of the password is in the page.

## How it works

There is no backend and no audio in this repository.

1. `songs.js` holds each song's title, artist credits, year, genre, artwork link and 30-second preview link from Apple's public [iTunes Search API](https://performance-partners.apple.com/search-api), plus a popularity rank from Deezer's public search that decides what counts as a hit. Songs Apple has no preview for come from Deezer instead; their preview links expire, so the page asks Deezer for a fresh one each time.
2. The page streams that preview from Apple and splits it into drums, bass, instruments and vocals in a background worker (`worker.js`), using the [Demucs](https://github.com/facebookresearch/demucs) model through [demucs-web](https://github.com/timcsy/demucs-web) and [ONNX Runtime Web](https://onnxruntime.ai/). Running it off the main thread keeps the page and the playhead smooth while upcoming songs are prepared.
3. The model (about 180 MB) is downloaded from Hugging Face on the first visit and cached by the browser.

Splitting a song takes a few seconds with WebGPU (current Chrome, Edge or Safari). Without WebGPU it still works, but slowly.

## Run it locally

Any static file server works:

```bash
python3 -m http.server 8173
```

Then open http://localhost:8173/.

## Put it on GitHub Pages

The site is plain static files with relative paths, so it works from a repository subpath as-is.

1. Push this folder to a GitHub repository.
2. In the repository, open Settings → Pages, set the source to "Deploy from a branch", and choose `main` and `/ (root)`.

To embed it in another site, copy `index.html`, `worker.js` and `songs.js` into any folder that is served over HTTPS.

## Change the songs

Edit `songs.txt` (one `Artist - Title` per line; add `#freak` or `#akul` to put a song in those modes), then rebuild `songs.js`:

```bash
python3 build_songs.py
```

The script only looks up songs that are not already in `songs.js`, and lists any it could not find a preview for. A new artist name gets its own chip on the start screen automatically.

## Files

| File | Purpose |
| --- | --- |
| `index.html` | The whole game: markup, styles and script |
| `worker.js` | Runs the stem-separation model in a background thread |
| `songs.js` | Generated song pool |
| `songs.txt` | The song list you edit |
| `build_songs.py` | Rebuilds `songs.js` from `songs.txt` (Python 3, needs `curl`) |

## A note on the music

Song previews and artwork are streamed from Apple and Deezer and remain the property of their rights holders; this project stores and redistributes none of it. Both provide previews for promoting their catalogues, so if you host this publicly, check that your use fits their terms and keep the "Listen on Apple Music" / "Listen on Deezer" links in place.
