# Store PC setup (Windows 10 / 11)

Scripts for the PCs in the store. Both run in Windows PowerShell 5.1 (built into
Windows 10 and 11) without administrator rights, and can be run again safely.
The step-by-step guide for the owner, in Spanish, is kept with the user manual.

| Script | Run in | What it does |
|---|---|---|
| `create-pos-shortcut.ps1` | the cashiers' Windows account | Desktop shortcut "Caja Josheps": the POS in a Chrome app window with its own profile (`%LOCALAPPDATA%\CajaPOS\Chrome`), so clearing everyday Chrome data never deletes unsent offline sales |
| `install-importer.ps1` | the account of whoever imports counts and prints labels | Installs `odoo-sync` in `%LOCALAPPDATA%\odoo-sync\venv`, adds it to the user's PATH, and creates the settings file `%APPDATA%\odoo-sync\.env` (readable only by that user) |
| `..\backup\install-pos.ps1` | PowerShell **as administrator** | Off-server backup copy, see [backup/README.md](../backup/README.md) |

```powershell
# from deploy\pos-pc in a copy of this repository
powershell -ExecutionPolicy Bypass -File .\create-pos-shortcut.ps1
winget install -e --id Python.Python.3.12 --scope user    # once, if Python is missing
powershell -ExecutionPolicy Bypass -File .\install-importer.ps1
```

`odoo-sync` reads `.env` in the current folder first, then `%APPDATA%\odoo-sync\.env`
(`~/.config/odoo-sync/.env` on Linux and macOS). Use an API key of a dedicated user
with Inventory administrator rights, never a password.

Both scripts run on every push in the `windows` CI job.
