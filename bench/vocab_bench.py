"""Vocabulary test: each clip's uncommon words (LocalFlow's own definition) plus distractors from
other clips as the vocabulary list; measure recall of the clip's uncommon words with and without it."""
import json, sys, time, re, soundfile as sf, bench
meta = [e for e in json.load(open("clips/meta.json")) if e["set"].startswith(("earnings22", "ami", "voxpopuli"))]
for e in meta: e["unc"] = bench.uncommon(e["ref"])
meta = [e for e in meta if e["unc"]]
name = sys.argv[1]; mode = sys.argv[2] if len(sys.argv) > 2 else "plain"
if name.startswith("pk") :
    v = name[2:]; d = f"sherpa-onnx-nemo-parakeet-tdt-0.6b-{v}-int8"
    run = bench.sherpa_offline("transducer", d, decoding_method="modified_beam_search", hotwords_file="empty_hot.txt",
                               hotwords_score=float(sys.argv[3]) if len(sys.argv) > 3 else 1.5, modeling_unit="bpe",
                               bpe_vocab=f"models/{d}/bpe.vocab") if mode != "plain" else bench.ENGINES["parakeet-tdt-0.6b-" + v]()
    if mode == "beam-novocab": mode = "beam"
    tag = mode + (sys.argv[3] if len(sys.argv) > 3 else "")
else:
    run = bench.ENGINES[name]()
    tag = mode
norm = lambda t: set(re.sub(r"[^a-z' ]", " ", t.lower()).split())
hit = tot = empty = 0; rows = []
for i, e in enumerate(meta):
    a = sf.read("clips/" + e["file"], dtype="float32")[0]
    vocab = sorted(set(e["unc"]) | set(sum((m["unc"] for m in meta[i + 1:i + 4]), [])))[:25] if mode == "vocab" else None
    t = time.time(); hyp = run(a, vocab); dt = time.time() - t
    got = sum(w.lower() in norm(hyp) for w in e["unc"]); hit += got; tot += len(e["unc"]); empty += not hyp.strip()
    rows.append({"file": e["file"], "unc": e["unc"], "hyp": hyp, "sec": dt})
json.dump(rows, open(f"results/vocab-{name}-{tag}.json", "w"), indent=1)
print(f"{name} {tag}: uncommon-word recall {100*hit/tot:.1f}% ({hit}/{tot}) over {len(meta)} clips, empty={empty}")
