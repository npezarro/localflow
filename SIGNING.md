# Code signing

Unsigned builds trigger **"Windows protected your PC"** (SmartScreen) on Windows and
**"Apple could not verify…"** (Gatekeeper) on macOS. Signing needs a certificate tied to a
verified identity, so it has to be set up by the project owner. The build workflow already
does the rest: once a platform's secrets exist, every build is signed automatically.

| | Windows | macOS |
|---|---|---|
| Service | Azure Artifact Signing (formerly Trusted Signing) | Apple Developer Program |
| Cost | about $9.99 / month | $99 / year |
| Who can get it | organizations in the US, Canada, EU, UK; **individuals in the US and Canada** | anyone |
| Effect | shows a verified publisher; SmartScreen warnings fade as the app earns reputation (no certificate gives instant trust since 2024) | Gatekeeper warning gone completely (signed + notarized) |
| Free alternatives | [SignPath Foundation](https://signpath.org) signs qualifying open-source projects for free (manual review, 1-2 weeks); the Microsoft Store signs MSIX packages for free | none for individuals |

The workflow checks each platform's secrets: **all set** → signs; **none set** → unsigned
build with a notice; **only some set** → the build fails (so a half-finished setup can never
quietly publish unsigned builds).

## Windows: Azure Artifact Signing

1. Create an Azure account and an **Artifact Signing account** (portal: "Artifact Signing").
   Note its region endpoint, e.g. `https://eus.codesigning.azure.net/`.
2. Complete **identity validation** as an individual (government photo ID via Microsoft Entra
   Verified ID; a few business days).
3. Create a **certificate profile** of type *Public Trust*.
4. Create an **app registration** (service principal) with a client secret, and give it the
   *Artifact Signing Certificate Profile Signer* role on the signing account.
5. Add these repository secrets (GitHub → Settings → Secrets and variables → Actions):

   | Secret | Value |
   |---|---|
   | `AZURE_TENANT_ID` | directory (tenant) ID |
   | `AZURE_CLIENT_ID` | app registration's application (client) ID |
   | `AZURE_CLIENT_SECRET` | the client secret |
   | `ARTIFACT_SIGNING_ENDPOINT` | e.g. `https://eus.codesigning.azure.net/` |
   | `ARTIFACT_SIGNING_ACCOUNT` | signing account name |
   | `ARTIFACT_SIGNING_PROFILE` | certificate profile name |

`LocalFlow.exe` and `LocalFlow-Setup-x64.exe` are then signed and CI verifies both signatures.

## macOS: Developer ID + notarization

1. Join the [Apple Developer Program](https://developer.apple.com/programs/) ($99/year).
2. In Xcode or the developer portal, create a **Developer ID Application** certificate and
   export it with its private key as a `.p12` file (set a password).
3. In App Store Connect → Users and Access → Integrations, create an **App Store Connect API
   key** (role: Developer) and download the `.p8` file; note its Key ID and the Issuer ID.
4. Add these repository secrets:

   | Secret | Value |
   |---|---|
   | `MACOS_CERT_P12` | `base64 -i cert.p12` output |
   | `MACOS_CERT_PASSWORD` | the .p12 password |
   | `APPLE_API_KEY_P8` | `base64 -i AuthKey_XXXX.p8` output |
   | `APPLE_API_KEY_ID` | the key ID |
   | `APPLE_API_ISSUER_ID` | the issuer ID |

The app is then signed inside-out with the hardened runtime (`scripts/macos_sign.sh`,
entitlements in `installer/entitlements.plist`), notarized and stapled, and the `.dmg` is
signed, notarized and stapled too. Even without these secrets, CI already signs ad-hoc with
the hardened runtime and runs the app under it, so the switch to a real certificate doesn't
change how the app runs.

**Until then (stable self-signed identity):** if the secrets `MACOS_SELF_SIGN_P12` and `MACOS_SELF_SIGN_PASSWORD` exist (a self-signed code-signing certificate, CN `LocalFlow Self-Signed`, exported with `openssl pkcs12 -export -legacy`), CI signs every Mac build with it. Gatekeeper still warns, but macOS ties Accessibility / Input Monitoring / Microphone grants to that identity, so they survive updates. Users re-grant once when switching from the ad-hoc builds.

Signing also fixes a macOS update annoyance: an ad-hoc-signed app has a new identity every
build, so macOS asks for Accessibility and Input Monitoring again after each update. A
Developer ID signature keeps the same identity across versions.
