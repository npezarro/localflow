# Account sync: Google sign-in setup (project owner, once)

LocalFlow's account sync signs in with Google and keeps data in the user's own Drive
app-data folder (scope `drive.appdata`, which Google classes as non-sensitive, plus
`openid email`). Builds need a Google OAuth **Desktop app** client; without one the app works
normally and the Account section says sign-in isn't available.

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project (e.g.
   `localflow`) and enable the **Google Drive API** (APIs & Services → Library).
2. **OAuth consent screen** (Google Auth Platform → Branding / Audience / Data access):
   app name `LocalFlow`, your support email; audience **External**; data access: add the
   scopes `openid`, `.../auth/userinfo.email`, `.../auth/drive.appdata`.
3. **Audience → Publish app** ("In production"). With only non-sensitive scopes no review is
   needed. (Left in *Testing*, only listed test users can sign in and sign-ins expire after
   7 days.)
4. **Clients → Create client → Desktop app**, name `LocalFlow`. Download the JSON.
5. Add repository secrets (GitHub → Settings → Secrets and variables → Actions):

   | Secret | Value |
   |---|---|
   | `LOCALFLOW_GOOGLE_CLIENT_ID` | `client_id` from the JSON |
   | `LOCALFLOW_GOOGLE_CLIENT_SECRET` | `client_secret` from the JSON |

   CI writes them into `localflow/_oauth_client.py` (gitignored) at build time. For a desktop
   client Google doesn't treat the secret as confidential, but it stays out of the source.

Running from source: put the downloaded JSON in the data folder as `google_oauth.json`.

What syncs: `learned.json` (merged per entry, newest change wins, deletions kept),
the pronunciation dictionary (entries plus recordings stored once by content hash), and one
`device-<id>.json` per device with its name and settings. Transcripts never sync.
