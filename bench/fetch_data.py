# Pull dictation-length clips (3-20 s) from public Open ASR test sets.
import json, os, soundfile as sf, numpy as np, librosa
from datasets import load_dataset
SETS = [("librispeech", "test.clean", 40), ("librispeech", "test.other", 40),
        ("earnings22", "test", 50), ("ami", "test", 30), ("tedlium", "test", 30), ("voxpopuli", "test", 30)]
os.makedirs("clips", exist_ok=True)
meta = []
for cfg, split, n in SETS:
    try:
        ds = load_dataset("hf-audio/esb-datasets-test-only-sorted", cfg, split=split, streaming=True)
    except Exception as e:
        print("skip", cfg, split, e); continue
    got = 0
    for i, ex in enumerate(ds):
        a = ex["audio"]; y = np.asarray(a["array"], dtype=np.float32); sr = a["sampling_rate"]
        if sr != 16000: y = librosa.resample(y, orig_sr=sr, target_sr=16000)
        d = len(y) / 16000; text = ex.get("text") or ex.get("norm_text") or ""
        if i % 7 or not (3 <= d <= 20) or len(text.split()) < 4: continue
        name = f"{cfg}-{split}-{got:03d}.wav"; sf.write(f"clips/{name}", y, 16000)
        meta.append({"file": name, "set": f"{cfg}.{split}", "dur": round(d, 2), "ref": text}); got += 1
        if got >= n: break
    print(cfg, split, got)
json.dump(meta, open("clips/meta.json", "w"), indent=1)
