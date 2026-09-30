"""The few Google Drive calls account sync needs, all inside the app-data folder (a hidden
folder in the user's Drive that only LocalFlow can see; scope ``drive.appdata``)."""
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"


class DriveError(RuntimeError):
    pass


class AppFolder:
    def __init__(self, token):
        self._token = token  # () -> access token

    def _request(self, url, data=None, method="GET", headers=None, raw=False, timeout=60):
        h = {"Authorization": "Bearer " + self._token()}
        h.update(headers or {})
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise DriveError("Drive %s %s: %s" % (method, exc.code, exc.read()[:200].decode("utf-8", "replace"))) from None
        return body if raw else (json.loads(body) if body else {})

    def list(self):
        """{name: {"id", "modifiedTime"}} for every file LocalFlow keeps in the account."""
        out, page = {}, None
        while True:
            q = {"spaces": "appDataFolder", "fields": "nextPageToken,files(id,name,modifiedTime)",
                 "pageSize": 1000}
            if page:
                q["pageToken"] = page
            data = self._request(API + "?" + urllib.parse.urlencode(q))
            for f in data.get("files", []):
                out[f["name"]] = f
            page = data.get("nextPageToken")
            if not page:
                return out

    def download(self, file_id):
        return self._request("%s/%s?alt=media" % (API, file_id), raw=True)

    def upload(self, name, content, file_id=None, mime="application/octet-stream"):
        """Create or replace a file; returns its id."""
        boundary = uuid.uuid4().hex
        meta = {"name": name} if file_id else {"name": name, "parents": ["appDataFolder"]}
        body = ("--%s\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n%s\r\n--%s\r\nContent-Type: %s\r\n\r\n"
                % (boundary, json.dumps(meta), boundary, mime)).encode() + content + ("\r\n--%s--" % boundary).encode()
        url = (UPLOAD + "/" + file_id if file_id else UPLOAD) + "?uploadType=multipart&fields=id"
        data = self._request(url, data=body, method="PATCH" if file_id else "POST",
                             headers={"Content-Type": "multipart/related; boundary=" + boundary})
        return data["id"]

    def delete(self, file_id):
        self._request("%s/%s" % (API, file_id), method="DELETE")
