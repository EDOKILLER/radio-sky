#!/usr/bin/env bash
# Cielo radio 1.0 - compilazione per Ubuntu
# © 2026 Edoardo Battistel
#
# Uso:  cd ~/Claude/Astropy/programma_cielo_radio/versione_1.0 && bash crea_eseguibile_ubuntu.sh
# Crea la cartella CieloRadio/ (eseguibile, icona, LEGGIMI), l'icona nel menu e sulla Scrivania,
# poi lancia l'autotest dell'eseguibile (scarica il modello GSM2008, 82 MB, se non c'è ancora).
set -euo pipefail
QUI="$(cd "$(dirname "$0")" && pwd)"
cd "$QUI"
VENV="$HOME/Claude/Astropy/venv_compila"
PY="$VENV/bin/python"
if [ ! -x "$PY" ]; then
  echo "Non trovo $PY (il venv di compilazione)."; exit 1
fi
echo "== 1. Controllo dei pacchetti"
"$PY" - <<'EOF'
import numpy, PIL, tkinter, h5py
print("numpy", numpy.__version__, "| Pillow", PIL.__version__, "| h5py", h5py.__version__, "| Tk", tkinter.TkVersion)
EOF
echo "== 2. Icona"
"$PY" crea_icona.py "$QUI"
echo "== 3. PyInstaller"
rm -rf build CieloRadio
"$PY" -m PyInstaller --noconfirm --clean --onefile --windowed --name CieloRadio \
  --hidden-import PIL._tkinter_finder --hidden-import h5py --collect-submodules h5py \
  --exclude-module matplotlib --exclude-module scipy --exclude-module astropy --exclude-module healpy \
  --exclude-module lal --exclude-module lalsimulation --exclude-module sklearn \
  --add-data "$QUI/cielo_radio.png:." \
  --distpath "$QUI/CieloRadio" --workpath "$QUI/build" --specpath "$QUI/build" \
  cielo_radio.py
cp cielo_radio.png LEGGIMI.txt CieloRadio/
echo "== 4. Icona nel menu e sulla Scrivania"
mkdir -p "$HOME/.local/share/icons" "$HOME/.local/share/applications"
cp cielo_radio.png "$HOME/.local/share/icons/cielo_radio.png"
DESKTOP_FILE="$QUI/CieloRadio/cieloradio.desktop"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Cielo radio
Comment=Il cielo sopra casa visto da un occhio radio (10 MHz - 94 GHz) e LWA TV in diretta
Exec="$QUI/CieloRadio/CieloRadio"
Path=$QUI/CieloRadio
Icon=$HOME/.local/share/icons/cielo_radio.png
Terminal=false
Categories=Education;Science;Astronomy;
StartupWMClass=Cieloradio
StartupNotify=true
Actions=autotest;

[Desktop Action autotest]
Name=Autotest (risultati in ~/Scrivania/Astronomia/Cielo_radio/autotest.txt)
Exec="$QUI/CieloRadio/CieloRadio" --autotest
EOF
chmod +x "$DESKTOP_FILE"
cp "$DESKTOP_FILE" "$HOME/.local/share/applications/cieloradio.desktop"
SCRIVANIA="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Scrivania")"
if [ -d "$SCRIVANIA" ]; then
  cp "$DESKTOP_FILE" "$SCRIVANIA/cieloradio.desktop"
  chmod +x "$SCRIVANIA/cieloradio.desktop"
  gio set "$SCRIVANIA/cieloradio.desktop" metadata::trusted true 2>/dev/null || true
fi
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "== 5. Autotest dell'eseguibile (scarica il modello GSM2008 se manca: qualche minuto)"
"$QUI/CieloRadio/CieloRadio" --autotest || true
echo
echo "Fatto. Eseguibile: $QUI/CieloRadio/CieloRadio"
echo "Icona sulla Scrivania: se serve, tasto destro -> «Consenti avvio»."
echo "Risultati dell'autotest: $HOME/Scrivania/Astronomia/Cielo_radio/autotest.txt"
