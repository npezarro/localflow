# Choosing a local model

LocalFlow runs two families of local models:

- **Whisper** (OpenAI), through faster-whisper on the CPU or an NVIDIA GPU, or MLX on an Apple Silicon GPU. `base.en` is bundled.
- **Parakeet** (NVIDIA), through sherpa-onnx on the CPU. Downloads once into `data/models/sherpa/`.

**Short version:** on a computer without an NVIDIA GPU, use `parakeet-v2` (English) or `parakeet-v3` (25 European languages). Use `parakeet-small` on an older or slower machine. Whisper is still the choice for other languages, and for steering spelling with the Vocabulary list.

| Model | Languages | Download | Vocabulary list |
|---|---|---|---|
| parakeet-small | English | 110 MB | no (use Replacements) |
| parakeet-v2 | English | 660 MB | no (use Replacements) |
| parakeet-v3 | 25 European | 670 MB | no (use Replacements) |
| base.en | English | bundled | yes |
| small.en | English | 460 MB | yes |
| large-v3-turbo | 99 | 1.6 GB | yes |

## Benchmark (CPU, 2026-10-20)

190 dictation-length clips (3-20 s, median about 15 s) from the public Open ASR Leaderboard test sets: LibriSpeech clean and other (read audiobooks), Earnings-22 (earnings calls: accents, company names, numbers), AMI (meetings) and VoxPopuli (European Parliament speeches). Word error rate (WER) uses Whisper's English text normalizer, so punctuation and casing don't count. Time is per clip with 6 CPU threads (what LocalFlow uses on a 12-thread CPU), int8 weights throughout, Whisper with LocalFlow's own decoding options (greedy, VAD on, timestamps on). A "bad" output is empty, or has under 40% or over 150% of the reference's word count.

| Model | WER | AMI | Earnings-22 | Libri clean | Libri other | VoxPopuli | Median s/clip | Max s | Bad |
|---|---|---|---|---|---|---|---|---|---|
| cohere-transcribe 2B (not shipped) | **4.69** | 5.3 | 9.2 | 0.9 | 2.5 | 5.4 | 3.25 | 5.42 | 0 |
| **parakeet-v2** | 6.02 | 6.8 | 12.7 | 1.3 | 1.9 | 7.1 | 0.96 | 2.53 | 1 |
| parakeet-unified-en (not shipped) | 6.14 | 7.5 | 12.2 | 1.1 | 2.5 | 7.8 | 0.99 | 1.67 | 1 |
| large-v3-turbo | 6.39 | 11.4 | 9.9 | 1.3 | 3.2 | 8.4 | 8.76 | 9.71 | 0 |
| **parakeet-v3** | 6.96 | 7.0 | 14.4 | 1.9 | 3.2 | 7.6 | 0.96 | 2.10 | 1 |
| **parakeet-small** | 7.32 | 10.2 | 12.1 | 1.9 | 4.8 | 8.6 | **0.32** | 0.54 | 0 |
| nemotron-streaming-en (not shipped) | 7.53 | 10.3 | 12.4 | 2.1 | 5.0 | 8.8 | 3.35 | 5.26 | 0 |
| qwen3-asr 0.6B (not shipped) | 7.72 | 11.0 | 12.2 | 2.0 | 5.3 | 9.4 | 3.54 | 15.21 | 0 |
| small.en | 8.32 | 11.8 | 13.1 | 2.5 | 5.7 | 9.9 | 2.16 | 5.93 | 2 |
| base.en (bundled) | 9.81 | 11.8 | 14.9 | 3.2 | 8.3 | 11.7 | 0.75 | 5.04 | 0 |
| nemotron-3.5-streaming (not shipped) | 9.94 | 15.4 | 15.7 | 3.2 | 6.7 | 10.4 | 3.42 | 5.22 | 1 |
| canary-180m-flash (not shipped) | 14.41 | 10.8 | 41.3 | 1.8 | 3.0 | 8.9 | 1.72 | 7.63 | 6 |
| moonshine-base v2 (not shipped) | failed | | | | | | | | 190 |

Shipped models again, run through LocalFlow's own transcription path (`Transcriber.transcribe`: speech-only trimming, the decoding settings the app uses), which is what you get:

| Model | WER | AMI | Earnings-22 | Libri clean | Libri other | VoxPopuli | Median s/clip | Max s | Bad |
|---|---|---|---|---|---|---|---|---|---|
| parakeet-v2 | 5.78 | 7.2 | 11.5 | 1.1 | 2.0 | 7.5 | 0.96 | 1.97 | 0 |
| parakeet-small | 7.17 | 9.8 | 11.7 | 2.0 | 4.5 | 8.9 | 0.36 | 0.57 | 0 |
| parakeet-v3 | 7.42 | 7.2 | 15.1 | 2.2 | 3.5 | 8.1 | 0.98 | 1.66 | 1 |

Short dictations (same machine, best of 3, through the app path):

| Model | 3 s of speech | 6 s | 11 s |
|---|---|---|---|
| base.en | 0.61 s | 0.68 s | 0.70 s |
| parakeet-v2 | 0.18 s | 0.35 s | 0.65 s |
| parakeet-small | 0.08 s | 0.13 s | 0.23 s |

Whisper pads every pass to a 30-second window, so its time barely changes with length; Parakeet's grows with the audio, which makes short dictations very quick.

The bar for shipping a model was: lower WER than small.en, no slower than small.en, and no more bad outputs. All three shipped Parakeet models clear it. The one "bad" Parakeet clip is an earnings call where two people talk over each other; the model kept one speaker.

Not shipped, and why:

- **cohere-transcribe** is the most accurate, but slower than the bar (roughly 1 s for a 5 s dictation, from its 0.22 real-time factor) and a 1.7 GB download. Worth adding later as a "most accurate" option.
- **parakeet-unified-en** is no better than parakeet-v2.
- **Nemotron** streaming models are built to show text while you talk; run on a finished recording they are slower and less accurate than Parakeet.
- **qwen3-asr** is slower and less accurate than parakeet-v2, with one very slow clip (15 s).
- **canary-180m-flash** returned empty or near-empty text (".", "O") for several earnings-call clips (41% WER there).
- **moonshine-base v2** works on its own 4 s sample but sherpa-onnx 1.13.8 fails on longer clips, so every benchmark clip came back empty.

Earnings-22 is the one set where large-v3-turbo beats Parakeet (9.9 vs 12.7): long numbers and company names. If your dictation is mostly that and you have an NVIDIA GPU, large-v3-turbo is still a good pick.

## Vocabulary

Whisper models read the Vocabulary list as a prompt; Parakeet has no prompt. To see what that costs, 48 clips from Earnings-22, AMI and VoxPopuli were scored on their uncommon words (79 in all, picked the way LocalFlow picks them: words Whisper's tokenizer splits into several pieces), with and without a vocabulary list holding those words plus distractors from other clips.

| Model | Uncommon words right, no list | With the list |
|---|---|---|
| base.en | 60.8% | 83.5% |
| small.en | 74.7% | 91.1% |
| large-v3-turbo | 81.0% | 84.8% |
| parakeet-v2 | 88.6% | 89.9% (hotwords) |
| parakeet-v3 | 73.4% | 78.5% (hotwords) |
| parakeet-small | 74.7% | n/a |

parakeet-v2 without a list beat base.en and large-v3-turbo with one, and came close to small.en with one. sherpa-onnx can bias Parakeet toward listed words ("hotwords"), but only with its beam search, which has an open bug returning empty or made-up text for some Parakeet clips (k2-fsa/sherpa-onnx#3267). It didn't trigger here (no empty outputs in 192 hotword runs), but the gain was small (one word for v2, four for v3), so LocalFlow uses the plain decoder and doesn't pass the list to Parakeet. Replacements and learned corrections work with every model. If you depend on the list for names, base.en or small.en with the list may still spell them better than parakeet-small or parakeet-v3.

## Reproduce

Scripts are in [`bench/`](../bench): `fetch_data.py` pulls the clips, `bench.py <model>` runs one model, `score.py` prints the table, `vocab_bench.py` runs the vocabulary test. sherpa-onnx models come from the `asr-models` release of k2-fsa/sherpa-onnx.
