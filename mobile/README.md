# LYS Workflow Hub — app Android (Capacitor)

Wrapper Capacitor del portale esterno (`/portale`). La WebView carica
direttamente l'URL pubblico del server — nessun asset web è bundlato
nell'APK (`www/` è uno stub vuoto richiesto da Capacitor, mai mostrato).

## IMPORTANTE: `server.url` deve combaciare con `PUBLIC_BASE_URL`

`capacitor.config.json` → `server.url` è un valore **build-time**, cablato
nell'APK. Se `PUBLIC_BASE_URL` nel `.env` del backend Python cambia,
aggiornare anche questo file e **ricompilare l'APK** — non c'è sync
automatico tra i due.

## Requisiti locali (verificati su questa macchina)

- Node.js + npm (qualunque versione recente va bene per `npm install`)
- JDK 17 — trovato in
  `C:\Program Files (x86)\Android\openjdk\jdk-17.0.8.101-hotspot`
- Android SDK — trovato in
  `C:\Program Files (x86)\Android\android-sdk`
  (build-tools 34.0.0, platform android-33/34, platform-tools,
  cmdline-tools 11.0)

Questi path sono specifici di questa macchina — su un'altra postazione
vanno adattati in `build_debug.bat` (gitignored, script di comodo locale)
o passati come variabili d'ambiente `JAVA_HOME`/`ANDROID_HOME` prima di
lanciare Gradle.

## Setup iniziale (già fatto in questo repo)

```
npm install
npx cap add android
```

`android/local.properties` (gitignored, contiene `sdk.dir` con path
locale) va ricreato su ogni nuova macchina.

## Build APK debug

```
npm run build:apk
```

oppure direttamente `cd android && gradlew.bat assembleDebug` con
`JAVA_HOME`/`ANDROID_HOME` impostati. Output:
`android/app/build/outputs/apk/debug/app-debug.apk`.

## Stato attuale (Fase B completata)

- Progetto Capacitor scaffoldato, app ID `it.lysauto.workflowhub`.
- Build debug verificata (BUILD SUCCESSFUL).
- **Zero plugin nativi** in questa fase — nessun push FCM, nessuna camera
  nativa. Serve solo a validare che il wrapping funzioni (login, sessione
  persistente, navigazione, upload foto via picker esistente).
- Da testare on-device (non fatto qui): sideload dell'APK, login, verifica
  persistenza sessione dopo kill+riavvio app.

Prossime fasi (vedi piano): C = push notifications (richiede progetto
Firebase + `google-services.json`, non ancora creato), D = camera nativa,
E = build/firma release per closed testing.
