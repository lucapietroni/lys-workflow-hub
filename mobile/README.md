# LYS Workflow Hub — app Android (Capacitor)

Wrapper Capacitor del portale esterno (`/portale`), nome app **LYSApp**
(`it.lysauto.workflowhub`). La WebView carica direttamente l'URL pubblico del
server — nessun asset web è bundlato nell'APK (`www/` è uno stub vuoto
richiesto da Capacitor, mai mostrato). Conseguenza pratica: le modifiche a
template/CSS/JS del backend Python sono immediatamente live nell'app, senza
rebuild — solo le modifiche a plugin nativi/manifest/Java richiedono una
nuova build + reinstallazione.

**Versionamento separato dal portale web**: `versionName`/`versionCode` in
`android/app/build.gradle` seguono un semver proprio del wrapper Android,
scollegato da `pyproject.toml`/footer del portale. Il footer nell'app mostra
sempre la versione del portale (server-side, sempre live), non quella
dell'APK installato — solo codice nativo/plugin segue il versionamento
qui.

## IMPORTANTE: `server.url` deve combaciare con `PUBLIC_BASE_URL`

`capacitor.config.json` → `server.url` è un valore **build-time**, cablato
nell'APK. Se `PUBLIC_BASE_URL` nel `.env` del backend Python cambia,
aggiornare anche questo file e **ricompilare l'APK** — non c'è sync
automatico tra i due.

## Cosa fa

- Login/sessione persistente, navigazione identica al portale browser.
- Notifiche push native (Firebase Cloud Messaging): deep-link alla pratica
  corretta al tap sulla notifica.
- Scatto foto nativo (`@capacitor/camera`) per l'upload diretto da pratica.
- Apertura documenti (PDF/docx/xlsx/...): `fetch` same-origin (cookie di
  sessione condivisi) + `@capacitor/filesystem` (scrittura in cache) +
  `@capacitor/share`. Chrome Custom Tabs (`@capacitor/browser`) NON va bene
  per questo: usa un cookie jar separato dalla WebView, quindi non vede la
  sessione autenticata e la richiesta fallisce.
- Sblocco biometrico opzionale (impronta/Face, `capgo-capacitor-native-
  biometric`): auto-setup al primo login, verifica ad ogni cold start reale
  del processo (plugin nativo `ColdStartPlugin`, non sessionStorage) e al
  resume da background oltre soglia. Fail-closed: qualunque errore nella
  catena porta all'overlay di blocco, mai a uno sblocco silenzioso.
- Galleria foto: pinch-zoom/pan nel lightbox, pressione prolungata per
  selezione multipla, "Scarica" (`@capacitor/filesystem`, area app-privata)
  e "Condividi" (`@capacitor/share`, es. WhatsApp).
- Full screen: status bar e navigation bar dello stesso colore dello sfondo
  app.

## Requisiti locali (verificati su questa macchina)

- Node.js + npm (qualunque versione recente va bene per `npm install`)
- JDK 17 — trovato in
  `C:\Program Files (x86)\Android\openjdk\jdk-17.0.8.101-hotspot`
- Android SDK — trovato in
  `C:\Program Files (x86)\Android\android-sdk`
  (build-tools 34.0.0, platform android-33/34, platform-tools,
  cmdline-tools 11.0)

Questi path sono specifici di questa macchina — su un'altra postazione
vanno adattati in `build_debug.bat`/`build_release.bat` (gitignored, script
di comodo locale) o passati come variabili d'ambiente `JAVA_HOME`/
`ANDROID_HOME` prima di lanciare Gradle.

`android/local.properties` (gitignored, contiene `sdk.dir` con path
locale) va ricreato su ogni nuova macchina. `android/app/google-services.json`
(config Firebase per FCM) deve essere presente per compilare — non
committato nel repo.

## Build

```
npm install
npm run build:apk           # debug
cd android && gradlew.bat assembleRelease   # release firmata (keystore)
```

WSL non ha SDK/JDK Android: i build da questo ambiente passano per
`cmd.exe /c "build_release.bat"` (interop Windows, ~1-2 minuti). Output:
`android/app/build/outputs/apk/{debug,release}/app-{debug,release}.apk`.

Le release firmate vengono pubblicate come GitHub Release del repo
(`LYSApp Android (release build)`) per la distribuzione agli utenti esterni
via closed testing/sideload — non c'è Google Play.
