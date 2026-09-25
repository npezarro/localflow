"""Online transcription and text clean-up over OpenAI-compatible HTTP APIs
(OpenAI, Groq, or any compatible server). Stdlib only."""
import json
import logging
import uuid
import urllib.error
import urllib.request

from . import audio

log = logging.getLogger(__name__)

STT_PRESETS = {
    "groq": {"label": "Groq (whisper-large-v3-turbo, very fast, free tier)",
             "base_url": "https://api.groq.com/openai/v1", "model": "whisper-large-v3-turbo",
             "key_name": "groq_api_key", "key_url": "https://console.groq.com/keys"},
    "openai": {"label": "OpenAI (gpt-transcribe)",
               "base_url": "https://api.openai.com/v1", "model": "gpt-transcribe",
               "key_name": "openai_api_key", "key_url": "https://platform.openai.com/api-keys"},
    "custom": {"label": "Custom OpenAI-compatible server",
               "base_url": "http://localhost:8000/v1", "model": "whisper-1",
               "key_name": "custom_api_key", "key_url": ""},
}

CHAT_PRESETS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "qwen/qwen3.8-27b",
             "key_name": "groq_api_key"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-5.4-mini",
               "key_name": "openai_api_key"},
    "custom": {"base_url": "http://localhost:11434/v1", "model": "llama3.1",
               "key_name": "custom_api_key"},
}


class CloudError(RuntimeError):
    pass


def _request(url, body, headers, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise CloudError("HTTP %d from %s: %s" % (exc.code, url.split("/v1")[0], detail)) from exc
    except urllib.error.URLError as exc:
        raise CloudError("cannot reach %s: %s" % (url.split("/v1")[0], exc.reason)) from exc


def multipart(fields, files):
    """fields: {name: str}; files: {name: (filename, bytes, content_type)}."""
    boundary = "----LocalFlow" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, name, value)).encode("utf-8"))
    for name, (filename, data, ctype) in files.items():
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                      "Content-Type: %s\r\n\r\n" % (boundary, name, filename, ctype)).encode("utf-8"))
        parts.append(data)
        parts.append(b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(parts), "multipart/form-data; boundary=" + boundary


def transcribe(samples, base_url, model, api_key, language="en", vocabulary=None, timeout=30):
    fields = {"model": model, "response_format": "json", "temperature": "0"}
    if language and language != "auto":
        fields["language"] = language
    if vocabulary:
        fields["prompt"] = "Vocabulary: " + ", ".join(vocabulary) + "."
    body, ctype = multipart(fields, {"file": ("speech.wav", audio.to_wav_bytes(samples), "audio/wav")})
    headers = {"Content-Type": ctype, "User-Agent": "LocalFlow"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    data = _request(base_url.rstrip("/") + "/audio/transcriptions", body, headers, timeout)
    if "text" not in data:
        raise CloudError("unexpected response: %s" % str(data)[:200])
    return data["text"].strip()


def chat(system, user, base_url, model, api_key, timeout=20):
    payload = {"model": model,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if not model.startswith(("gpt-5", "o1", "o3", "o4")):  # reasoning models reject temperature
        payload["temperature"] = 0
    headers = {"Content-Type": "application/json", "User-Agent": "LocalFlow"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    data = _request(base_url.rstrip("/") + "/chat/completions", json.dumps(payload).encode("utf-8"),
                    headers, timeout)
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise CloudError("unexpected response: %s" % str(data)[:200]) from exc
