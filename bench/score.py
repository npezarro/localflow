import json, glob, os, re, statistics as st, jiwer, sys
from whisper_normalizer.english import EnglishTextNormalizer
n = EnglishTextNormalizer()
def wer(rows):
    R = [n(r["ref"]) for r in rows]; H = [n(r["hyp"]) for r in rows]
    keep = [(a, b) for a, b in zip(R, H) if a.strip()]
    return 100 * jiwer.wer([a for a, _ in keep], [b if b.strip() else "<empty>" for _, b in keep])
def bad(r):
    rw, hw = len(n(r["ref"]).split()), len(n(r["hyp"]).split())
    return hw == 0 or hw > 1.5 * rw + 3 or hw < 0.4 * rw
out = []
for f in sorted(glob.glob("results/*.json")):
    name = os.path.basename(f)[:-5]
    if name.endswith("-vocab") or name.startswith("vocab-"): continue
    d = json.load(open(f)); rows = d["rows"]
    if len(rows) < 50: continue
    sets = sorted({r["set"] for r in rows})
    per = {s.split(".")[0] + ("-other" if "other" in s else ""): wer([r for r in rows if r["set"] == s]) for s in sets}
    secs = [r["sec"] for r in rows]; rtf = sum(secs) / sum(r["dur"] for r in rows)
    out.append((wer(rows), name, per, st.median(secs), max(secs), rtf, sum(map(bad, rows)), d["load"], len(rows)))
cols = list(out[0][2]) if out else []
print("| model | WER all | " + " | ".join(cols) + " | median s/clip | max s | RTF | bad outputs | load s |")
print("|" + "---|" * (len(cols) + 7))
for w, name, per, med, mx, rtf, b, load, nrows in sorted(out):
    print(f"| {name} | {w:.2f} | " + " | ".join(f"{per.get(c, float('nan')):.1f}" for c in cols) + f" | {med:.2f} | {mx:.2f} | {rtf:.3f} | {b}/{nrows} | {load:.1f} |")
