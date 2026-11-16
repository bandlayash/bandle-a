# Unmix

A song-guessing game that runs entirely in the browser. Each song starts as drums alone; add bass, instruments and vocals one layer at a time and name the track from as few layers as you can.

- A run is 5 songs. Each is worth 10,000 points, minus 2,000 for every layer you add and 500 for every wrong guess. Giving up scores 0.
- Pick which artists are in play on the start screen. In the game, click a layer's name to mute it, click or drag on the waveform to seek, press ⏮ to jump back to the start, and click an artist in the side list to browse their songs.

## How it works

There is no backend and no audio in this repository.

1. `songs.js` holds each song's title, artist, year, artwork link and 30-second preview link from Apple's public [iTunes Search API](https://performance-partners.apple.com/search-api).
2. The page streams that preview from Apple and splits it into drums, bass, instruments and vocals in the browser, using the [Demucs](https://github.com/facebookresearch/demucs) model through [demucs-web](https://github.com/timcsy/demucs-web) and [ONNX Runtime Web](https://onnxruntime.ai/).
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

To embed it in another site, copy `index.html` and `songs.js` into any folder that is served over HTTPS.

## Change the songs

Edit `songs.txt` (one `Artist - Title` per line), then rebuild `songs.js`:

```bash
python3 build_songs.py
```

The script only looks up songs that are not already in `songs.js`, and lists any it could not find a preview for. A new artist name gets its own chip on the start screen automatically.

## Files

| File | Purpose |
| --- | --- |
| `index.html` | The whole game: markup, styles and script |
| `songs.js` | Generated song pool |
| `songs.txt` | The song list you edit |
| `build_songs.py` | Rebuilds `songs.js` from `songs.txt` (Python 3, needs `curl`) |

## A note on the music

Song previews and artwork are streamed from Apple and remain the property of their rights holders; this project stores and redistributes none of it. Apple provides previews for promoting its catalogue, so if you host this publicly, check that your use fits Apple's terms and keep the "Listen on Apple Music" link in place.
