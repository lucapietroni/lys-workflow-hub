# Pubblicazione del repository su GitHub

Procedura per collegare il repository locale `lys-workflow-hub` a un repository
privato su GitHub. Si esegue **una sola volta**, alla prima pubblicazione.

## Prerequisiti

- Git installato sul PC (verifica con `git --version` in PowerShell). Se manca:
  scaricalo da <https://git-scm.com/download/win> con le impostazioni di default.
- Account GitHub attivo. Se non ce l'hai: <https://github.com/signup>.

## Passo 1 — Configurare git (solo la prima volta in vita tua)

In PowerShell, sostituendo nome e email con i tuoi:

```powershell
git config --global user.name "Luca Pietroni"
git config --global user.email "luca.pietroni@gmail.com"
```

## Passo 2 — Inizializzare il repository locale

Apri PowerShell e portati nella cartella del progetto:

```powershell
cd "$env:USERPROFILE\OneDrive\Documenti\Claude\Projects\Lysauto\lys-workflow-hub"
git init
git branch -M main
git add .
git status
```

L'ultimo comando elenca i file che verranno committati. **Verifica con calma**:
non deve apparire nessun `.mdb`, nessun `.env`, nessuna cartella `wincar-sample/`
o `data/*.db`. Se vedi qualcosa di sospetto, fermati e dimmelo prima di fare il
commit.

Quando sei tranquillo:

```powershell
git commit -m "Initial commit: foundation + workflow A skeleton"
```

## Passo 3 — Creare il repository vuoto su GitHub

1. Apri <https://github.com/new> nel browser.
2. **Repository name**: `lys-workflow-hub`
3. **Description** (opzionale): "Piattaforma di automazione documentale per Carrozzeria LYS Auto srl, integrata con WinCar."
4. **Visibility**: **Private** ✅
5. **NON** spuntare "Add a README", "Add .gitignore", "Choose a license"
   (questi file li abbiamo già localmente; se li metti su GitHub si crea un conflitto).
6. Click **Create repository**.

GitHub ti mostra una pagina con i comandi per collegare un repository esistente.
Servono solo le due righe sotto "…or push an existing repository from the command line".

## Passo 4 — Collegare e pubblicare

Torna in PowerShell, nella stessa cartella di prima. Sostituisci `<tuo-username>`
con il tuo nome utente GitHub:

```powershell
git remote add origin https://github.com/<tuo-username>/lys-workflow-hub.git
git push -u origin main
```

Al primo push GitHub ti chiede di autenticarti:

- Se è la prima volta su questa macchina, si aprirà una finestra del browser per
  fare login su GitHub (con il tuo account) e autorizzare git. È normale.
- In alternativa puoi creare un **Personal Access Token (PAT)** classico da
  <https://github.com/settings/tokens> e incollarlo al posto della password.

Quando il push finisce, la pagina del repository su GitHub mostra tutti i file.
Da quel momento ogni futuro `git push` è una singola riga.

## Passo 5 — Verifica finale

Su GitHub controlla che:

- Il repository risulti **Private** (etichetta grigia in alto, non "Public").
- **NON** ci sia nessun file `.mdb` o `wincar-sample/` (sono nel `.gitignore`).
- Il file `README.md` venga renderizzato correttamente nella home del repo.

## Flusso quotidiano dopo il primo push

Quando aggiungi o modifichi codice, il ciclo è:

```powershell
git status                    # vedi cosa è cambiato
git add .                     # mette tutto in staging
git commit -m "messaggio"     # snapshot locale
git push                      # invio su GitHub
```

## Nota su OneDrive

La cartella del progetto è dentro OneDrive. Git e OneDrive normalmente
convivono, ma se vedi errori strani durante un push o un commit:

- Pausa la sincronizzazione di OneDrive per qualche minuto e riprova.
- Oppure, escludi solo la cartella `.git\` dalla sincronizzazione: in OneDrive
  → impostazioni → Backup → Gestisci backup → escludi
  `Documenti\Claude\Projects\Lysauto\lys-workflow-hub\.git\`.

## Cosa NON è ancora automatizzato

- **CI / GitHub Actions**: niente test automatici al push. Si aggiungono dopo il
  primo milestone produttivo.
- **Releases / tag**: per ora una versione lineare; gli sviluppi grossi
  verranno taggati con `v0.1.0`, `v0.2.0`, ecc.
- **Deploy**: l'app gira solo localmente sul PC della carrozzeria, non c'è
  rilascio cloud.
