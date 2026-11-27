// Runs the stem-separation model (Demucs on ONNX Runtime Web) in a background thread,
// so splitting upcoming songs never stalls the page or the playhead.
//
// Messages in:  { type: 'load' }                          start loading the model
//               { type: 'separate', id, left, right }     split one stereo clip (44.1 kHz Float32Arrays)
// Messages out: { type: 'status', text }                  loading progress, for the status line
//               { type: 'backend', backend }              'webgpu' (fast) or 'wasm' (CPU, much slower)
//               { type: 'ready' } / { type: 'fatal', message }
//               { type: 'progress', id, p }               0..1 while separating
//               { type: 'done', id, mixLevel, stems: [{ left, right, peaks, level }] }  drums, bass, other, vocals
//               { type: 'error', id, message }

const ORT_DIST = 'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/';
const DEMUCS_URL = 'https://cdn.jsdelivr.net/npm/demucs-web@1.0.2/src/index.js';
const PARTS = ['drums', 'bass', 'other', 'vocals'];
const PEAK_BINS = 800;

let ort, demucs, modelUrl, proc = null, backend = null, currentId = 0;
let loading = null, queue = Promise.resolve();

// The model is ~180 MB. Download it straight into one buffer (no copies, which matters on phones)
// and store it in the Cache API so later visits load it from disk.
async function getModel() {
  let cache = null;
  try {
    cache = await caches.open('unmix-model');
    const hit = await cache.match(modelUrl);
    if (hit) return await hit.arrayBuffer();
  } catch {}
  const res = await fetch(modelUrl);
  if (!res.ok) throw new Error(`Model download failed (${res.status})`);
  const saving = cache ? cache.put(modelUrl, res.clone()).catch(() => {}) : null;
  const total = +res.headers.get('Content-Length') || 0;
  const reader = res.body.getReader();
  let buf = total ? new Uint8Array(total) : null, chunks = [], got = 0, lastPct = -1;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    if (buf && got + value.length <= buf.length) buf.set(value, got); else { buf = null; chunks.push(value); }
    got += value.length;
    const pct = total ? Math.min(99, Math.round(got / total * 100)) : -1;
    if (pct !== lastPct) {
      lastPct = pct;
      postMessage({ type: 'status', text: `Downloading separation model (one time, ${Math.round((total || 180_500_000) / 1e6)} MB)${pct >= 0 ? ` · ${pct}%` : '…'}` });
    }
  }
  await saving;
  if (buf) return got === buf.length ? buf.buffer : buf.slice(0, got).buffer;
  return new Blob(chunks).arrayBuffer();  // no Content-Length: fall back to collecting chunks
}

// `model` is the downloaded buffer when we already hold it; otherwise it is re-read from the cache.
async function makeProcessor(providers, model) {
  const p = new demucs.DemucsProcessor({
    ort, sessionOptions: { executionProviders: providers },
    onProgress: q => postMessage({ type: 'progress', id: currentId, p: q.progress }),
  });
  await p.loadModel(model?.byteLength ? model : await getModel());
  return p;
}

function setBackend(name) { backend = name; postMessage({ type: 'backend', backend }); }

async function load() {
  postMessage({ type: 'status', text: 'Loading audio engine…' });
  [ort, demucs] = await Promise.all([import(ORT_DIST + 'ort.webgpu.min.mjs'), import(DEMUCS_URL)]);
  ort.env.wasm.wasmPaths = ORT_DIST;
  modelUrl = demucs.CONSTANTS.DEFAULT_MODEL_URL;
  let model = await getModel();  // first visit: download (with progress) and cache
  postMessage({ type: 'status', text: 'Starting separation model…' });
  // Use the GPU when this browser offers it here; many phones don't, or can't fit the model, so fall
  // back to the CPU (works everywhere, just slower).
  const adapter = self.navigator?.gpu ? await navigator.gpu.requestAdapter().catch(() => null) : null;
  if (adapter) {
    try { proc = await makeProcessor(['webgpu'], model); setBackend('webgpu'); }
    catch (err) { console.warn('WebGPU separation unavailable, using CPU', err); }
  }
  if (!proc) { proc = await makeProcessor(['wasm'], model); setBackend('wasm'); }
  model = null;  // the session has its own copy now
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

async function separate({ id, left, right }, retried = false) {
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
    // Phones reset the GPU when the browser goes to the background, which breaks the session.
    // Rebuild it once (on the CPU if the GPU won't come back) and try this clip again.
    if (!retried && proc) {
      console.warn('Separation failed, rebuilding the session', err);
      try { proc = await makeProcessor([backend || 'wasm']); }
      catch { proc = await makeProcessor(['wasm']); setBackend('wasm'); }
      return separate({ id, left, right }, true);
    }
    postMessage({ type: 'error', id, message: err?.message || String(err) });
  }
}

onmessage = ({ data }) => {
  if (data.type === 'load') {
    loading ??= load().then(
      () => postMessage({ type: 'ready' }),
      err => { postMessage({ type: 'fatal', message: err?.message || String(err) }); throw err; });
  } else if (data.type === 'separate') {
    queue = queue.then(() => separate(data)).catch(() => {});  // one clip at a time
  }
};
