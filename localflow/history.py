import json
import os
import threading
import time
import uuid

from . import paths


class History:
    def __init__(self, limit=500):
        self.path = os.path.join(paths.data_dir(), "history.jsonl")
        self.limit = limit
        self._lock = threading.Lock()
        self.items = self._read()

    def _read(self):
        items = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        items.append(json.loads(line))
                    except ValueError:
                        continue
        except FileNotFoundError:
            pass
        return items[-self.limit:]

    def _write(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for item in self.items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def add(self, text, seconds=0.0, model="", raw=None):
        item = {"id": uuid.uuid4().hex[:12], "ts": time.time(), "text": text,
                "seconds": round(seconds, 1), "model": model}
        if raw and raw.strip() != text.strip():
            item["raw"] = raw
        with self._lock:
            self.items.append(item)
            trimmed = len(self.items) > self.limit
            self.items = self.items[-self.limit:]
            if trimmed:
                self._write()
            else:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
        return item

    def delete(self, item_id):
        with self._lock:
            self.items = [i for i in self.items if i["id"] != item_id]
            self._write()

    def clear(self):
        with self._lock:
            self.items = []
            self._write()

    def last(self):
        return self.items[-1] if self.items else None
