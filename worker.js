// Runs the stem-separation model (Demucs on ONNX Runtime Web) in a background thread,
// so splitting upcoming songs never stalls the page or the playhead.
//
// Messages in:  { type: 'load' }                          start loading the model
//               { type: 'separate', id, left, right }     split one stereo clip (44.1 kHz Float32Arrays)
// Messages out: { type: 'status', text }                  loading progress, for the status line
//               { type: 'ready' } / { type: 'fatal', message }
//               { type: 'progress', id, p }               0..1 while separating
//               { type: 'done', id, mixLevel, stems: [{ left, right, peaks, level }] }  drums, bass, other, vocals
//               { type: 'error', id, message }

const ORT_DIST = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/';
const DEMUCS_URL = 'https://cdn.jsdelivr.net/npm/demucs-web@1.0.2/src/index.js';
const PARTS = ['drums', 'bass', 'other', 'vocals'];
const PEAK_BINS = 800;

let loading = null, proc = null, currentId = 0, queue = Promise.resolve();

async function fetchModel(url) {
  let cache = null;
  try {
    cache = await caches.open('unmix-model');
    const hit = await cache.match(url);
    if (hit) return await hit.arrayBuffer();
  } catch {}
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Model download failed (${res.status})`);
  const total = +res.headers.get('Content-Length') || 180_500_000;
  const reader = res.body.getReader(), chunks = [];
  let lastPct = -1;
  for (let got = 0;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value); got += value.length;
    const pct = Math.min(99, Math.round(got / total * 100));
    if (pct !== lastPct) {
      lastPct = pct;
      postMessage({ type: 'status', text: `Downloading separation model (one time, ${Math.round(total / 1e6)} MB) · ${pct}%` });
    }
  }
  const blob = new Blob(chunks);
  try { await cache?.put(url, new Response(blob)); } catch {}
  return blob.arrayBuffer();
}

async function load() {
  postMessage({ type: 'status', text: 'Loading audio engine…' });
  const [ort, demucs] = await Promise.all([import(ORT_DIST + 'ort.webgpu.min.mjs'), import(DEMUCS_URL)]);
  ort.env.wasm.wasmPaths = ORT_DIST;
  const model = await fetchModel(demucs.CONSTANTS.DEFAULT_MODEL_URL);
  postMessage({ type: 'status', text: 'Starting separation model…' });
  proc = new demucs.DemucsProcessor({ ort, onProgress: p => postMessage({ type: 'progress', id: currentId, p: p.progress }) });
  await proc.loadModel(model);
}

const rms = a => { let s = 0; for (let i = 0; i < a.length; i += 4) s += a[i] * a[i]; return Math.sqrt(s / (a.length / 4)); };

// Loudest sample in each of PEAK_BINS equal time slices, across both channels (for the waveform).
function peaksOf(left, right) {
  const peaks = new Float32Array(PEAK_BINS);
  for (let b = 0; b < PEAK_BINS; b++) {
    let m = 0;
    for (let i = Math.floor(b * left.length / PEAK_BINS), e = Math.floor((b + 1) * left.length / PEAK_BINS); i < e; i += 4) {
      const v = Math.max(Math.abs(left[i]), Math.abs(right[i])); if (v > m) m = v;
    }
    peaks[b] = m;
  }
  return peaks;
}

async function separate({ id, left, right }) {
  try {
    await loading;
    currentId = id;
    const out = await proc.separate(left, right);
    const stems = PARTS.map(key => {
      const { left: l, right: r } = out[key];
      return { left: l, right: r, peaks: peaksOf(l, r), level: rms(l) };
    });
    postMessage({ type: 'done', id, mixLevel: rms(left), stems },
      stems.flatMap(s => [s.left.buffer, s.right.buffer, s.peaks.buffer]));
  } catch (err) {
    postMessage({ type: 'error', id, message: err?.message || String(err) });
  }
}

onmessage = ({ data }) => {
  if (data.type === 'load') {
    loading ??= load().then(
      () => postMessage({ type: 'ready' }),
      err => { postMessage({ type: 'fatal', message: err?.message || String(err) }); throw err; });
  } else if (data.type === 'separate') {
    queue = queue.then(() => separate(data));  // one clip at a time
  }
};
