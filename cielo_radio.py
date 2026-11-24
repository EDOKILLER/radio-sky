#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CIELO RADIO 1.0
===============
© 2026 Edoardo Battistel

Il cielo sopra casa, adesso, come lo vedrebbe un occhio radio (10 MHz - 94 GHz),
con lo stesso orizzonte e le stesse costellazioni della vista a occhio nudo,
e il cielo radio in diretta delle stazioni LWA (New Mexico).

Avvio:      python3 cielo_radio.py           (oppure l'eseguibile CieloRadio)
Autotest:   python3 cielo_radio.py --autotest
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import numpy as np
from PIL import Image, ImageTk

import motore_radio as M

TITOLO = f"Cielo radio {M.VERSIONE}"
FILE_IMPOSTAZIONI = Path(os.path.expanduser("~/.cielo_radio.json"))
CARTELLA_IMMAGINI = M.CARTELLA / "immagini"
FREQ_RAPIDE = [(10, "10 MHz"), (38, "38 MHz"), (74, "74 MHz"), (150, "150 MHz"), (408, "408 MHz"),
               (1420, "1,4 GHz"), (5000, "5 GHz"), (23000, "23 GHz"), (94000, "94 GHz")]
COLORI_SFONDO = "#14161f"
COLORE_TESTO = "#e8e8e8"
COLORE_ORO = "#e6c869"


def ambiente_originale():
    """Variabili d'ambiente senza le librerie di PyInstaller (per zenity, xdg-open, ffmpeg)."""
    amb = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in amb:
        amb["LD_LIBRARY_PATH"] = amb["LD_LIBRARY_PATH_ORIG"]
    elif getattr(sys, "frozen", False):
        amb.pop("LD_LIBRARY_PATH", None)
    return amb


def apri_cartella(p):
    try:
        subprocess.Popen(["xdg-open", str(p)], env=ambiente_originale(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# =============================================================================
# FINESTRA DI MESSAGGIO RIDIMENSIONABILE (il testo va a capo)
# =============================================================================
class Messaggio(tk.Toplevel):
    def __init__(self, app, titolo, testo, pulsanti=(("Chiudi", None),)):
        super().__init__(app.root)
        self.title(titolo)
        self.configure(bg=COLORI_SFONDO)
        self.transient(app.root)
        f = ttk.Frame(self, padding=app.px(14))
        f.pack(fill="both", expand=True)
        self.lab = ttk.Label(f, text=testo, justify="left", style="Testo.TLabel")
        self.lab.pack(fill="both", expand=True)
        riga = ttk.Frame(f)
        riga.pack(fill="x", pady=(app.px(12), 0))
        for nome, azione in pulsanti:
            def fai(a=azione):
                if a:
                    a()
                self.destroy()
            ttk.Button(riga, text=nome, command=fai).pack(side="right", padx=app.px(4))
        f.bind("<Configure>", lambda e: self.lab.configure(wraplength=max(200, e.width - app.px(30))))
        w = min(int(app.root.winfo_screenwidth() * 0.6), app.px(820))
        self.geometry(f"{w}x{app.px(300)}")
        self.minsize(app.px(300), app.px(150))


# =============================================================================
# PANNELLO CON BARRA DI SCORRIMENTO
# =============================================================================
class Scorrevole(ttk.Frame):
    def __init__(self, genitore, app):
        super().__init__(genitore)
        self.tela = tk.Canvas(self, highlightthickness=0, bg=COLORI_SFONDO)
        self.barra = ttk.Scrollbar(self, orient="vertical", command=self.tela.yview)
        self.dentro = ttk.Frame(self.tela, padding=app.px(8))
        self._id = self.tela.create_window(0, 0, window=self.dentro, anchor="nw")
        self.tela.configure(yscrollcommand=self.barra.set)
        self.tela.pack(side="left", fill="both", expand=True)
        self.barra.pack(side="right", fill="y")
        self.dentro.bind("<Configure>", lambda e: self.tela.configure(scrollregion=self.tela.bbox("all")))
        self.tela.bind("<Configure>", self._larghezza)
        self.etichette = []
        for w in (self.tela, self.dentro):
            w.bind("<Enter>", lambda e: self._ruota(True))
            w.bind("<Leave>", lambda e: self._ruota(False))

    def _larghezza(self, e):
        self.tela.itemconfigure(self._id, width=e.width)
        for lab in self.etichette:
            try:
                lab.configure(wraplength=max(120, e.width - 40))
            except tk.TclError:
                pass

    def _ruota(self, attiva):
        if attiva:
            self.tela.bind_all("<Button-4>", lambda e: self.tela.yview_scroll(-2, "units"))
            self.tela.bind_all("<Button-5>", lambda e: self.tela.yview_scroll(2, "units"))
            self.tela.bind_all("<MouseWheel>", lambda e: self.tela.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        else:
            for ev in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
                self.tela.unbind_all(ev)


# =============================================================================
# APPLICAZIONE
# =============================================================================
class App:
    def __init__(self, root, autotest=False):
        self.root = root
        self.autotest = autotest
        self.coda = queue.Queue()
        self.ferma = threading.Event()
        self.imp = M.Impostazioni()
        self._carica_impostazioni()
        self.pittore = M.Pittore()
        self.dt = datetime.now(timezone.utc)
        self.ora_vera = tk.BooleanVar(value=True)
        self.in_corsa = False
        self.rendering = False
        self.richiesta = False
        self.foto = None
        self.img_corrente = None
        self.selezione = None              # (ra, dec) J2000 per i ritagli
        self.lwa_foto = None
        self.lwa_img = None
        self.lwa_quando = 0
        self.luogo_salvato = None          # per «Torna al mio luogo»
        self.video_attivo = None
        self.dati_pronti = {"GSM2008": M.npy_pronti("GSM2008"), "LFSM": M.npy_pronti("LFSM")}
        root.title(TITOLO)
        root.configure(bg=COLORI_SFONDO)
        self._stile()
        self._costruisci()
        self._applica_testo()
        w, h = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{int(w * 0.92)}x{int(h * 0.86)}+{int(w * 0.04)}+{int(h * 0.04)}")
        root.minsize(900, 600)
        root.protocol("WM_DELETE_WINDOW", self.chiudi)
        root.after(100, self._ciclo)
        root.after(300, self._avvio)
        root.after(30000, self._orologio)

    # ------------------------------------------------------------------ impostazioni
    def _carica_impostazioni(self):
        try:
            d = json.loads(FILE_IMPOSTAZIONI.read_text(encoding="utf-8"))
            for k, v in d.items():
                if hasattr(self.imp, k):
                    setattr(self.imp, k, v)
        except (OSError, ValueError):
            pass
        if self.imp.modello not in M.MODELLI:
            self.imp.modello = "GSM2008"

    def _salva_impostazioni(self):
        try:
            FILE_IMPOSTAZIONI.write_text(json.dumps(self.imp.__dict__, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def px(self, n):
        return int(round(n * self.imp.scala_testo))

    # ------------------------------------------------------------------ stile
    def _stile(self):
        self.st = ttk.Style(self.root)
        try:
            self.st.theme_use("clam")
        except tk.TclError:
            pass
        self.font_base = tkfont.nametofont("TkDefaultFont")
        self.font_testo = tkfont.nametofont("TkTextFont")
        self.font_grande = tkfont.Font(family=self.font_base.cget("family"), weight="bold")
        self.font_titolo = tkfont.Font(family=self.font_base.cget("family"), weight="bold")
        self.font_freq = tkfont.Font(family=self.font_base.cget("family"), weight="bold")

    def _applica_testo(self):
        s = self.imp.scala_testo
        for f, base in ((self.font_base, 11), (self.font_testo, 11), (tkfont.nametofont("TkMenuFont"), 11),
                        (tkfont.nametofont("TkHeadingFont"), 11)):
            f.configure(size=int(round(base * s)))
        self.font_grande.configure(size=int(round(12 * s)))
        self.font_titolo.configure(size=int(round(15 * s)))
        self.font_freq.configure(size=int(round(20 * s)))
        riga = self.font_base.metrics("linespace")
        st = self.st
        bg, fg = COLORI_SFONDO, COLORE_TESTO
        st.configure(".", background=bg, foreground=fg, fieldbackground="#262a38", font=self.font_base,
                     bordercolor="#3a3f52", lightcolor="#3a3f52", darkcolor="#10121a", troughcolor="#262a38",
                     selectbackground="#3d5a99", selectforeground="#ffffff", insertcolor=fg)
        st.configure("TButton", padding=(int(riga * 0.6), int(riga * 0.3)), background="#2c3245")
        st.map("TButton", background=[("active", "#3d4660"), ("disabled", "#20232f")],
               foreground=[("disabled", "#777")])
        st.configure("Oro.TButton", foreground=COLORE_ORO)
        st.configure("TCheckbutton", indicatorsize=int(riga * 0.75), padding=int(riga * 0.15))
        st.configure("TRadiobutton", indicatorsize=int(riga * 0.75), padding=int(riga * 0.15))
        st.map("TCheckbutton", background=[("active", bg)])
        st.map("TRadiobutton", background=[("active", bg)])
        st.configure("TCombobox", arrowsize=int(riga * 0.85), padding=int(riga * 0.2))
        st.configure("TScrollbar", arrowsize=int(riga * 0.9), width=int(riga * 0.9))
        st.configure("Vertical.TScrollbar", arrowsize=int(riga * 0.9), width=int(riga * 0.9))
        st.configure("Horizontal.TScale", sliderlength=int(riga * 1.4), sliderthickness=int(riga * 0.9))
        st.configure("TNotebook.Tab", padding=(int(riga * 0.6), int(riga * 0.25)), font=self.font_grande)
        st.map("TNotebook.Tab", background=[("selected", "#2c3245")], foreground=[("selected", COLORE_ORO)])
        st.configure("TLabelframe", padding=int(riga * 0.4))
        st.configure("TLabelframe.Label", font=self.font_grande, foreground=COLORE_ORO)
        st.configure("Titolo.TLabel", font=self.font_titolo, foreground=COLORE_ORO)
        st.configure("Freq.TLabel", font=self.font_freq, foreground="#9fe7ff")
        st.configure("Testo.TLabel", font=self.font_testo)
        st.configure("Firma.TLabel", foreground="#b8a970")
        st.configure("Horizontal.TProgressbar", thickness=int(riga * 0.7))
        self.root.option_add("*TCombobox*Listbox.font", self.font_base)
        if hasattr(self, "testo_info"):
            self.testo_info.configure(font=self.font_testo)
        for sc in getattr(self, "scorrevoli", []):
            sc._larghezza(type("E", (), {"width": max(200, sc.tela.winfo_width())})())

    # ------------------------------------------------------------------ interfaccia
    def _costruisci(self):
        r = self.root
        alto = ttk.Frame(r, padding=(8, 6))
        alto.pack(side="top", fill="x")
        ttk.Label(alto, text=TITOLO, style="Titolo.TLabel").pack(side="left")
        self.lab_luogo = ttk.Label(alto, text="")
        self.lab_luogo.pack(side="left", padx=(20, 6))
        ttk.Button(alto, text="Cambia luogo…", command=self.finestra_luogo).pack(side="left")
        self.lab_ora = ttk.Label(alto, text="", font=self.font_grande)
        self.lab_ora.pack(side="left", padx=20)
        ttk.Label(alto, text=M.FIRMA, style="Firma.TLabel").pack(side="right")
        self.var_testo = tk.StringVar(value=f"{int(round(self.imp.scala_testo * 100))} %")
        cb = ttk.Combobox(alto, textvariable=self.var_testo, state="readonly", width=6,
                          values=[f"{p} %" for p in range(100, 210, 10)])
        cb.pack(side="right", padx=(4, 16))
        cb.bind("<<ComboboxSelected>>", self._cambia_testo)
        ttk.Label(alto, text="Dimensione testo").pack(side="right")

        basso = ttk.Frame(r, padding=(8, 4))
        basso.pack(side="bottom", fill="x")
        self.prog = ttk.Progressbar(basso, mode="determinate", maximum=1.0, length=260)
        self.prog.pack(side="right", padx=6)
        self.lab_stato = ttk.Label(basso, text="")
        self.lab_stato.pack(side="left", fill="x", expand=True)
        basso.bind("<Configure>", lambda e: self.lab_stato.configure(wraplength=max(200, e.width - 320)))

        corpo = ttk.PanedWindow(r, orient="horizontal")
        corpo.pack(fill="both", expand=True)
        sinistra = ttk.Frame(corpo)
        self.tela = tk.Canvas(sinistra, bg="black", highlightthickness=0, cursor="crosshair")
        self.tela.pack(fill="both", expand=True)
        self.lab_puntatore = ttk.Label(sinistra, text="Passa con il mouse sul cielo per leggere coordinate e "
                                                      "temperatura; clic su un oggetto per la sua scheda.",
                                       justify="left")
        self.lab_puntatore.pack(fill="x", padx=6, pady=4)
        sinistra.bind("<Configure>", lambda e: self.lab_puntatore.configure(wraplength=max(200, e.width - 20)))
        corpo.add(sinistra, weight=3)
        destra = ttk.Frame(corpo, width=520)
        corpo.add(destra, weight=1)
        self.tela.bind("<Configure>", lambda e: self.chiedi_disegno(ritardo=200))
        self.tela.bind("<Motion>", self._puntatore)
        self.tela.bind("<Button-1>", self._clic)

        self.nb = ttk.Notebook(destra)
        self.nb.pack(fill="both", expand=True)
        self.scorrevoli = []
        self._scheda_cielo()
        self._scheda_tempo()
        self._scheda_lwa()
        self._scheda_info()
        self.nb.bind("<<NotebookTabChanged>>", self._cambio_scheda)
        self._aggiorna_etichette()

    def _pannello(self, titolo):
        sc = Scorrevole(self.nb, self)
        self.nb.add(sc, text=titolo)
        self.scorrevoli.append(sc)
        return sc

    def _etichetta(self, sc, genitore, testo, **kw):
        lab = ttk.Label(genitore, text=testo, justify="left", **kw)
        sc.etichette.append(lab)
        return lab

    def _scheda_cielo(self):
        sc = self._pannello("Cielo")
        f = sc.dentro
        # modo
        lf = ttk.LabelFrame(f, text="Come guardare")
        lf.pack(fill="x", pady=4)
        self.var_modo = tk.StringVar(value=self.imp.modo)
        ttk.Radiobutton(lf, text="Occhio radio", value="radio", variable=self.var_modo,
                        command=self._cambia_modo).pack(anchor="w")
        ttk.Radiobutton(lf, text="Occhio nudo (luce visibile)", value="occhio", variable=self.var_modo,
                        command=self._cambia_modo).pack(anchor="w")
        # frequenza
        lf = ttk.LabelFrame(f, text="Frequenza")
        lf.pack(fill="x", pady=4)
        self.lab_freq = ttk.Label(lf, text="", style="Freq.TLabel")
        self.lab_freq.pack(anchor="w")
        self.var_scala = tk.DoubleVar(value=self._f_a_cursore(self.imp.f_mhz))
        self.cursore = ttk.Scale(lf, from_=0.0, to=1000.0, variable=self.var_scala, orient="horizontal",
                                 command=self._muovi_cursore)
        self.cursore.pack(fill="x", pady=4)
        griglia = ttk.Frame(lf)
        griglia.pack(fill="x")
        for k, (fq, t) in enumerate(FREQ_RAPIDE):
            ttk.Button(griglia, text=t, command=lambda x=fq: self.imposta_frequenza(x)).grid(
                row=k // 3, column=k % 3, sticky="ew", padx=2, pady=2)
        for c in range(3):
            griglia.columnconfigure(c, weight=1)
        self.lab_freq_spieg = self._etichetta(sc, lf, "")
        self.lab_freq_spieg.pack(fill="x", pady=(6, 0))
        # modello
        lf = ttk.LabelFrame(f, text="Modello del cielo radio")
        lf.pack(fill="x", pady=4)
        self.var_modello = tk.StringVar(value=M.MODELLI[self.imp.modello]["nome"])
        cb = ttk.Combobox(lf, textvariable=self.var_modello, state="readonly",
                          values=[m["nome"] for m in M.MODELLI.values()])
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", self._cambia_modello)
        self.lab_modello = self._etichetta(sc, lf, "")
        self.lab_modello.pack(fill="x", pady=(4, 0))
        # vista
        lf = ttk.LabelFrame(f, text="Vista")
        lf.pack(fill="x", pady=4)
        self.var_vista = tk.StringVar(value=self.imp.vista)
        ttk.Radiobutton(lf, text="Cupola: tutto il cielo, guardando in alto (N in alto, E a sinistra)",
                        value="cupola", variable=self.var_vista, command=self._cambia_vista).pack(anchor="w")
        ttk.Radiobutton(lf, text="Panorama: l'orizzonte tutto intorno", value="panorama",
                        variable=self.var_vista, command=self._cambia_vista).pack(anchor="w")
        riga = ttk.Frame(lf)
        riga.pack(fill="x", pady=2)
        ttk.Label(riga, text="Proiezione della cupola").pack(side="left")
        self.var_proiez = tk.StringVar(value=self.imp.proiezione)
        cb = ttk.Combobox(riga, textvariable=self.var_proiez, state="readonly", width=13,
                          values=["equidistante", "ortografica"])
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", self._cambia_vista)
        riga = ttk.Frame(lf)
        riga.pack(fill="x", pady=2)
        ttk.Label(riga, text="Colori").pack(side="left")
        self.var_colori = tk.StringVar(value=self.imp.colori)
        cb = ttk.Combobox(riga, textvariable=self.var_colori, state="readonly", width=18, values=list(M.LUT.keys()))
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", self._cambia_vista)
        # sovrapposizioni
        lf = ttk.LabelFrame(f, text="Da mostrare")
        lf.pack(fill="x", pady=4)
        self.var_sovr = {}
        for k, t in (("costellazioni", "Linee delle costellazioni"), ("nomi_costellazioni", "Nomi delle costellazioni"),
                     ("stelle_in_radio", "Stelle anche nella vista radio"), ("sorgenti", "Sorgenti radio famose"),
                     ("pianeti", "Sole, Luna e pianeti"), ("griglia", "Cerchi di altezza (30° e 60°)"),
                     ("piano_galattico", "Piano della Via Lattea (tratteggio azzurro)")):
            v = tk.BooleanVar(value=getattr(self.imp, k))
            self.var_sovr[k] = v
            ttk.Checkbutton(lf, text=t, variable=v, command=self._cambia_sovr).pack(anchor="w")
        lf = ttk.LabelFrame(f, text="Salva")
        lf.pack(fill="x", pady=4)
        ttk.Button(lf, text="Salva l'immagine (PNG)", command=self.salva_png).pack(fill="x", pady=2)
        ttk.Button(lf, text="Apri la cartella delle immagini", command=lambda: apri_cartella(CARTELLA_IMMAGINI)).pack(fill="x", pady=2)

    def _scheda_tempo(self):
        sc = self._pannello("Tempo e video")
        f = sc.dentro
        lf = ttk.LabelFrame(f, text="Quando")
        lf.pack(fill="x", pady=4)
        ttk.Checkbutton(lf, text="Adesso (segue l'ora vera)", variable=self.ora_vera,
                        command=self._cambia_ora_vera).pack(anchor="w")
        riga = ttk.Frame(lf)
        riga.pack(fill="x", pady=4)
        for t, m in (("−1 ora", -60), ("−10 min", -10), ("+10 min", 10), ("+1 ora", 60)):
            ttk.Button(riga, text=t, command=lambda x=m: self.sposta_tempo(x)).pack(side="left", expand=True, fill="x", padx=2)
        riga = ttk.Frame(lf)
        riga.pack(fill="x", pady=4)
        ttk.Button(riga, text="Stasera alle 22", command=self.stasera).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(riga, text="−1 giorno", command=lambda: self.sposta_tempo(-1440)).pack(side="left", expand=True, fill="x", padx=2)
        ttk.Button(riga, text="+1 giorno", command=lambda: self.sposta_tempo(1440)).pack(side="left", expand=True, fill="x", padx=2)
        riga = ttk.Frame(lf)
        riga.pack(fill="x", pady=4)
        ttk.Label(riga, text="Data e ora (AAAA-MM-GG HH:MM)").pack(side="left")
        self.var_data = tk.StringVar()
        e = ttk.Entry(riga, textvariable=self.var_data, width=17, font=self.font_base)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.vai_alla_data())
        ttk.Button(riga, text="Vai", command=self.vai_alla_data).pack(side="left")
        lf = ttk.LabelFrame(f, text="Tempo accelerato")
        lf.pack(fill="x", pady=4)
        self._etichetta(sc, lf, "Il cielo gira: 12 ore in un minuto. Guarda la Via Lattea radio che sorge e "
                                "tramonta, e il Sole che compare come sorgente fortissima.").pack(fill="x")
        self.btn_corsa = ttk.Button(lf, text="Avvia: una notte in un minuto", command=self.corsa)
        self.btn_corsa.pack(fill="x", pady=4)
        lf = ttk.LabelFrame(f, text="Video MP4")
        lf.pack(fill="x", pady=4)
        self.var_tipo_video = tk.StringVar(value="notte")
        ttk.Radiobutton(lf, text="Una notte: 12 ore a partire dall'ora mostrata", value="notte",
                        variable=self.var_tipo_video).pack(anchor="w")
        ttk.Radiobutton(lf, text="Le frequenze: da 10 MHz al massimo del modello, all'ora mostrata",
                        value="frequenze", variable=self.var_tipo_video).pack(anchor="w")
        riga = ttk.Frame(lf)
        riga.pack(fill="x", pady=4)
        ttk.Label(riga, text="Dimensione").pack(side="left")
        self.var_dim_video = tk.StringVar(value="1080 × 1080")
        ttk.Combobox(riga, textvariable=self.var_dim_video, state="readonly", width=12,
                     values=["720 × 720", "1080 × 1080", "1920 × 1080"]).pack(side="left", padx=6)
        ttk.Label(riga, text="Durata").pack(side="left", padx=(10, 0))
        self.var_durata = tk.StringVar(value="10 s")
        ttk.Combobox(riga, textvariable=self.var_durata, state="readonly", width=6,
                     values=["5 s", "10 s", "20 s", "40 s"]).pack(side="left", padx=6)
        self.btn_video = ttk.Button(lf, text="Crea il video", command=self.crea_video)
        self.btn_video.pack(fill="x", pady=4)
        self._etichetta(sc, lf, f"I video e le immagini vanno in {CARTELLA_IMMAGINI}.").pack(fill="x")

    def _scheda_lwa(self):
        sc = self._pannello("LWA TV in diretta")
        f = sc.dentro
        riga = ttk.Frame(f)
        riga.pack(fill="x", pady=4)
        ttk.Label(riga, text="Canale").pack(side="left")
        self.var_canale = tk.StringVar(value=M.LWATV["1"][0])
        cb = ttk.Combobox(riga, textvariable=self.var_canale, state="readonly",
                          values=[v[0] for v in M.LWATV.values()])
        cb.pack(side="left", fill="x", expand=True, padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.aggiorna_lwa(forza=True))
        self.lab_lwa = ttk.Label(f, text="(immagine non ancora scaricata)", anchor="center")
        self.lab_lwa.pack(fill="x", pady=4)
        self.lab_lwa_info = self._etichetta(sc, f, "")
        self.lab_lwa_info.pack(fill="x", pady=4)
        riga = ttk.Frame(f)
        riga.pack(fill="x", pady=4)
        self.var_lwa_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(riga, text="Aggiorna ogni 15 s", variable=self.var_lwa_auto).pack(side="left")
        ttk.Button(riga, text="Aggiorna ora", command=lambda: self.aggiorna_lwa(forza=True)).pack(side="right")
        ttk.Button(f, text="Confronta: il modello visto da LWA1 adesso (38 MHz, ortografica)",
                   command=self.confronta_lwa, style="Oro.TButton").pack(fill="x", pady=4)
        self.btn_torna = ttk.Button(f, text="Torna al mio luogo", command=self.torna_luogo, state="disabled")
        self.btn_torna.pack(fill="x", pady=2)
        self._etichetta(sc, f, (
            "Che cos'è: le stazioni del Long Wavelength Array, nel New Mexico (USA), sono radiotelescopi fatti di "
            "centinaia di antenne a dipolo sparse sul terreno. Insieme fotografano tutto il cielo ogni pochi secondi, "
            "tra 10 e 88 MHz. A sinistra l'intensità totale (Stokes I), a destra la polarizzazione circolare |V|: "
            "il cielo normale lì è quasi vuoto, ma compaiono il Sole e Giove quando hanno tempeste radio. "
            "Le scritte «good», «Cal» ecc. dicono lo stato dell'osservazione; a volte le stazioni fanno altro e "
            "l'immagine resta ferma.\n\n"
            "Il New Mexico è circa 120° a ovest di Padova: il cielo passa prima sopra l'Italia e poi sopra di "
            "loro. Quello che LWA vede adesso allo zenit, Padova l'ha avuto allo zenit circa 8 ore fa (e lo "
            "riavrà fra circa 16 ore). Il New Mexico è anche 11° più a sud: vede un po' più cielo australe.")).pack(fill="x", pady=6)

    def _scheda_info(self):
        f = ttk.Frame(self.nb, padding=self.px(6))
        self.nb.add(f, text="Informazioni")
        self.testo_info = tk.Text(f, wrap="word", bg="#1b1e2a", fg=COLORE_TESTO, insertbackground=COLORE_TESTO,
                                  relief="flat", font=self.font_testo, padx=8, pady=8)
        barra = ttk.Scrollbar(f, orient="vertical", command=self.testo_info.yview)
        self.testo_info.configure(yscrollcommand=barra.set)
        riga = ttk.Frame(f)
        riga.pack(side="bottom", fill="x", pady=4)
        self.btn_ritagli = ttk.Button(riga, text="Ritagli radio di SkyView (NVSS, TGSS, VLSSr, HI4PI)",
                                      command=self.ritagli, state="disabled")
        self.btn_ritagli.pack(fill="x")
        barra.pack(side="right", fill="y")
        self.testo_info.pack(fill="both", expand=True)
        self.scrivi_info(GUIDA)

    def scrivi_info(self, t):
        self.testo_info.configure(state="normal")
        self.testo_info.delete("1.0", "end")
        self.testo_info.insert("1.0", t)
        self.testo_info.configure(state="disabled")

    # ------------------------------------------------------------------ etichette
    def _aggiorna_etichette(self):
        f = self.imp.f_mhz
        self.lab_freq.configure(text=f"{M.testo_frequenza(f)}   (λ = {M.lunghezza_onda(f)})")
        self.lab_freq_spieg.configure(text=spiegazione_frequenza(f))
        self.lab_luogo.configure(text=f"Luogo: {self.imp.luogo} ({M.num(self.imp.lat, 2)}°, {M.num(self.imp.lon, 2)}°)")
        loc = self.dt.astimezone()
        self.lab_ora.configure(text=("Adesso: " if self.ora_vera.get() else "Ora mostrata: ")
                               + f"{loc:%d/%m/%Y %H:%M} ({self.dt:%H:%M} UTC)")
        self.var_data.set(f"{loc:%Y-%m-%d %H:%M}")
        mod = M.MODELLI[self.imp.modello]
        stato = "pronto" if self.dati_pronti.get(self.imp.modello) else f"da scaricare ({M.num(mod['mb'], 1)} MB)"
        self.lab_modello.configure(text=f"Da {M.testo_frequenza(mod['fmin'])} a {M.testo_frequenza(mod['fmax'])} – {stato}. "
                                        + DESCR_MODELLI[self.imp.modello])

    def stato(self, t):
        self.lab_stato.configure(text=t)

    # ------------------------------------------------------------------ cursore della frequenza
    def _f_a_cursore(self, f):
        return 1000.0 * np.log(f / 10.0) / np.log(94000.0 / 10.0)

    def _cursore_a_f(self, v):
        return float(10.0 * (94000.0 / 10.0) ** (float(v) / 1000.0))

    def _muovi_cursore(self, v):
        f = self._cursore_a_f(v)
        f = float(f"{f:.3g}")
        if abs(f - self.imp.f_mhz) / self.imp.f_mhz > 1e-4:
            self.imp.f_mhz = f
            self._dopo_frequenza()

    def imposta_frequenza(self, f):
        mod = M.MODELLI[self.imp.modello]
        if f > mod["fmax"]:
            self.stato(f"Il modello {mod['breve']} arriva solo a {M.testo_frequenza(mod['fmax'])}: passo a GSM2008.")
            self.var_modello.set(M.MODELLI["GSM2008"]["nome"])
            self._cambia_modello()
        self.imp.f_mhz = float(f)
        self.var_scala.set(self._f_a_cursore(f))
        if self.imp.modo != "radio":
            self.var_modo.set("radio")
            self.imp.modo = "radio"
        self._dopo_frequenza()

    def _dopo_frequenza(self):
        mod = M.MODELLI[self.imp.modello]
        if self.imp.f_mhz > mod["fmax"]:
            self.imp.f_mhz = mod["fmax"]
            self.var_scala.set(self._f_a_cursore(self.imp.f_mhz))
        self._aggiorna_etichette()
        self.chiedi_disegno(ritardo=60)

    # ------------------------------------------------------------------ comandi
    def _cambia_testo(self, e=None):
        try:
            self.imp.scala_testo = int(self.var_testo.get().split()[0]) / 100.0
        except ValueError:
            return
        self._applica_testo()
        self._aggiorna_etichette()
        self.chiedi_disegno()
        self._salva_impostazioni()

    def _cambia_modo(self):
        self.imp.modo = self.var_modo.get()
        if self.imp.modo == "occhio" and not self.pittore.visibile.pronto():
            self.stato("Scarico la mappa della Via Lattea in luce visibile (Mellinger, SkyView): circa un minuto…")
            self.in_background(self._scarica_visibile)
        self.chiedi_disegno()

    def _cambia_modello(self, e=None):
        nome = next(k for k, v in M.MODELLI.items() if v["nome"] == self.var_modello.get())
        self.imp.modello = nome
        if not self.dati_pronti.get(nome):
            self.in_background(lambda: self._prepara(nome))
        if self.imp.f_mhz > M.MODELLI[nome]["fmax"]:
            self.imp.f_mhz = M.MODELLI[nome]["fmax"]
            self.var_scala.set(self._f_a_cursore(self.imp.f_mhz))
        self._aggiorna_etichette()
        self.chiedi_disegno()

    def _cambia_vista(self, e=None):
        self.imp.vista = self.var_vista.get()
        self.imp.proiezione = self.var_proiez.get()
        self.imp.colori = self.var_colori.get()
        self.chiedi_disegno()

    def _cambia_sovr(self):
        for k, v in self.var_sovr.items():
            setattr(self.imp, k, bool(v.get()))
        self.chiedi_disegno()

    def _cambia_ora_vera(self):
        if self.ora_vera.get():
            self.dt = datetime.now(timezone.utc)
            if self.in_corsa:
                self.corsa()
        self._aggiorna_etichette()
        self.chiedi_disegno()

    def sposta_tempo(self, minuti):
        self.ora_vera.set(False)
        self.dt = self.dt + timedelta(minutes=minuti)
        self._aggiorna_etichette()
        self.chiedi_disegno()

    def stasera(self):
        loc = datetime.now().astimezone()
        sera = loc.replace(hour=22, minute=0, second=0, microsecond=0)
        self.ora_vera.set(False)
        self.dt = sera.astimezone(timezone.utc)
        self._aggiorna_etichette()
        self.chiedi_disegno()

    def vai_alla_data(self):
        try:
            loc = datetime.strptime(self.var_data.get().strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            Messaggio(self, "Data non valida", "Scrivi la data così: 2026-12-24 22:30 (ora locale).")
            return
        self.ora_vera.set(False)
        self.dt = loc.astimezone().astimezone(timezone.utc)
        self._aggiorna_etichette()
        self.chiedi_disegno()

    def corsa(self):
        self.in_corsa = not self.in_corsa
        self.btn_corsa.configure(text="Ferma" if self.in_corsa else "Avvia: una notte in un minuto")
        if self.in_corsa:
            self.ora_vera.set(False)
            self._passo_corsa()

    def _passo_corsa(self):
        if not self.in_corsa:
            return
        if not self.rendering:
            self.dt += timedelta(minutes=3)
            self._aggiorna_etichette()
            self.chiedi_disegno(ritardo=0)
        self.root.after(250, self._passo_corsa)

    def _orologio(self):
        if self.ora_vera.get() and not self.in_corsa:
            self.dt = datetime.now(timezone.utc)
            self._aggiorna_etichette()
            self.chiedi_disegno(ritardo=0)
        self.root.after(30000, self._orologio)

    # ------------------------------------------------------------------ luogo
    def finestra_luogo(self):
        w = tk.Toplevel(self.root)
        w.title("Luogo di osservazione")
        w.configure(bg=COLORI_SFONDO)
        w.transient(self.root)
        f = ttk.Frame(w, padding=self.px(14))
        f.pack(fill="both", expand=True)
        vars_ = {}
        for i, (k, t, v) in enumerate((("nome", "Nome", self.imp.luogo), ("lat", "Latitudine (°, nord positiva)", self.imp.lat),
                                       ("lon", "Longitudine (°, est positiva)", self.imp.lon),
                                       ("quota", "Quota (m)", self.imp.quota))):
            ttk.Label(f, text=t).grid(row=i, column=0, sticky="w", pady=3)
            vars_[k] = tk.StringVar(value=str(v))
            ttk.Entry(f, textvariable=vars_[k], font=self.font_base, width=24).grid(row=i, column=1, sticky="ew", padx=6)
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Luoghi pronti:").grid(row=4, column=0, sticky="w", pady=(10, 2))
        pr = ttk.Frame(f)
        pr.grid(row=5, column=0, columnspan=2, sticky="ew")
        for k, (n, (la, lo, q)) in enumerate(M.LUOGHI.items()):
            def metti(n=n, la=la, lo=lo, q=q):
                vars_["nome"].set(n)
                vars_["lat"].set(str(la))
                vars_["lon"].set(str(lo))
                vars_["quota"].set(str(q))
            ttk.Button(pr, text=n, command=metti).grid(row=k // 2, column=k % 2, sticky="ew", padx=2, pady=2)
        pr.columnconfigure(0, weight=1)
        pr.columnconfigure(1, weight=1)

        def ok():
            try:
                lat = float(vars_["lat"].get().replace(",", "."))
                lon = float(vars_["lon"].get().replace(",", "."))
                q = float(vars_["quota"].get().replace(",", ".") or 0)
                assert -90 <= lat <= 90 and -180 <= lon <= 360
            except (ValueError, AssertionError):
                Messaggio(self, "Valori non validi", "Latitudine tra −90 e 90, longitudine tra −180 e 180 "
                                                     "(per esempio Padova: 45,4064 e 11,8768).")
                return
            self.imp.luogo = vars_["nome"].get().strip() or "Luogo"
            self.imp.lat, self.imp.lon, self.imp.quota = lat, (lon + 180) % 360 - 180, q
            self.luogo_salvato = None
            self.btn_torna.configure(state="disabled")
            self._salva_impostazioni()
            self._aggiorna_etichette()
            self.chiedi_disegno()
            w.destroy()
        riga = ttk.Frame(f)
        riga.grid(row=6, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(riga, text="Annulla", command=w.destroy).pack(side="right", padx=4)
        ttk.Button(riga, text="Usa questo luogo", command=ok, style="Oro.TButton").pack(side="right", padx=4)
        w.minsize(self.px(420), self.px(260))

    # ------------------------------------------------------------------ disegno
    def chiedi_disegno(self, ritardo=30):
        if getattr(self, "_timer", None):
            try:
                self.root.after_cancel(self._timer)
            except (tk.TclError, ValueError):
                pass
        self._timer = self.root.after(ritardo, self._avvia_disegno)

    def _avvia_disegno(self):
        self._timer = None
        if self.rendering:
            self.richiesta = True
            return
        w, h = self.tela.winfo_width(), self.tela.winfo_height()
        if w < 50 or h < 50:
            return
        self.rendering = True
        self.richiesta = False
        imp = M.Impostazioni(**dict(self.imp.__dict__))
        dt = self.dt
        if self.imp.modo == "radio" and not self.pittore.modello(self.imp.modello):
            self.stato(f"Il modello {self.imp.modello} non è ancora pronto: mostro solo costellazioni e oggetti.")

        def lavoro():
            try:
                t0 = time.time()
                img = self.pittore.disegna(imp, dt, w, h)
                self.coda.put(("immagine", img, time.time() - t0))
            except Exception as e:  # noqa: BLE001
                traceback.print_exc()
                self.coda.put(("errore_disegno", f"{type(e).__name__}: {e}"))
        threading.Thread(target=lavoro, daemon=True).start()

    def _mostra(self, img):
        self.img_corrente = img
        self.foto = ImageTk.PhotoImage(img)
        self.tela.delete("all")
        self.tela.create_image(0, 0, image=self.foto, anchor="nw")

    def _puntatore(self, e):
        t = self.pittore.info_punto(self.imp, e.x, e.y)
        if t:
            ogg = self.pittore.oggetto_vicino(e.x, e.y, self.px(14))
            if ogg:
                t = f"{ogg[1]} – clic per la scheda\n" + t
            self.lab_puntatore.configure(text=t)

    def _clic(self, e):
        ogg = self.pittore.oggetto_vicino(e.x, e.y, self.px(16))
        if ogg:
            testo, pos = self.pittore.scheda(self.imp, self.dt, ogg[0], ogg[1])
            self.selezione = pos
            if pos is None:
                cor = self.pittore.ultima_cornice
                corpi = M.posizioni_corpi(self.dt, self.imp.lat, self.imp.lon)
                ra, dec = corpi[ogg[1]][:2]
                v = M.radec_vett(ra, dec) @ cor.P           # equinozio della data -> J2000
                self.selezione = tuple(float(x) for x in M.vett_radec(v))
        else:
            t = self.pittore.info_punto(self.imp, e.x, e.y)
            if not t:
                return
            r = self.pittore.ultima_proiezione.altaz_pixel(e.x, e.y)
            v = M.altaz_enu(*r) @ self.pittore.ultima_cornice.enu_j2000.T
            self.selezione = tuple(float(x) for x in M.vett_radec(v))
            testo = "Punto del cielo\n\n" + t + "\n\n" + spiegazione_frequenza(self.imp.f_mhz)
        self.scrivi_info(testo)
        self.btn_ritagli.configure(state="normal")
        self.nb.select(3)

    def _cambio_scheda(self, e=None):
        if self.nb.index("current") == 2:
            self.aggiorna_lwa()

    # ------------------------------------------------------------------ salvataggi
    def _nome_file(self, est):
        CARTELLA_IMMAGINI.mkdir(parents=True, exist_ok=True)
        loc = self.dt.astimezone()
        modo = ("radio_" + M.testo_frequenza_en(self.imp.f_mhz).replace(" ", "").replace(".", "p")) \
            if self.imp.modo == "radio" else "visibile"
        luogo = "".join(c for c in self.imp.luogo if c.isalnum())[:20]
        return CARTELLA_IMMAGINI / f"cielo_{luogo}_{modo}_{loc:%Y%m%d_%H%M}.{est}"

    def salva_png(self):
        imp = M.Impostazioni(**dict(self.imp.__dict__))
        imp.scala_testo = 1.3
        p = self._nome_file("png")
        self.stato("Preparo l'immagine 2000 × 2000…")
        dt = self.dt

        def lavoro():
            pit = self.pittore.copia()
            img = pit.disegna(imp, dt, 2000, 2000 if imp.vista == "cupola" else 900)
            img.save(p)
            return p
        self.in_background(lavoro, fatto=lambda r: Messaggio(
            self, "Immagine salvata", f"Salvata in:\n{r}", (("Apri la cartella", lambda: apri_cartella(r.parent)),
                                                             ("Chiudi", None))))

    def crea_video(self):
        if self.video_attivo:
            self.video_attivo.set()
            return
        if self.imp.modo == "radio" and not self.pittore.modello(self.imp.modello):
            Messaggio(self, "Modello non pronto", "Aspetta che il modello del cielo radio sia scaricato.")
            return
        w, h = (int(x) for x in self.var_dim_video.get().replace("×", "x").split("x"))
        if self.imp.vista == "panorama" and w == h:
            h = w // 2
        fps = 24
        n = int(self.var_durata.get().split()[0]) * fps
        tipo = self.var_tipo_video.get()
        imp = M.Impostazioni(**dict(self.imp.__dict__))
        imp.scala_testo = 1.2 * max(1.0, h / 1080)
        if tipo == "frequenze":
            imp.modo = "radio"
        p = self._nome_file("mp4")
        if tipo == "frequenze":
            p = p.with_name(p.stem.replace("radio_", "frequenze_") + ".mp4")
        else:
            p = p.with_name(p.stem + "_12ore.mp4")
        ferma = threading.Event()
        self.video_attivo = ferma
        self.btn_video.configure(text="Interrompi il video")
        dt = self.dt

        def lavoro():
            pit = self.pittore.copia()
            return pit.video(imp, dt, tipo, p, w=w, h=h, fotogrammi=n, fps=fps, ore=12.0,
                             avanzamento=lambda k, t: self.coda.put(("progresso", k / t, f"Video: fotogramma {k} di {t}")),
                             ferma=ferma)

        def fine(r):
            self.video_attivo = None
            self.btn_video.configure(text="Crea il video")
            if isinstance(r, Path):
                Messaggio(self, "Video pronto", f"Salvato in:\n{r}", (("Apri la cartella", lambda: apri_cartella(r.parent)),
                                                                     ("Chiudi", None)))
        self.in_background(lavoro, fatto=fine, errore=lambda e: fine(None))

    # ------------------------------------------------------------------ LWA TV
    def aggiorna_lwa(self, forza=False):
        if not forza and time.time() - self.lwa_quando < 14:
            return
        self.lwa_quando = time.time()
        canale = next(k for k, v in M.LWATV.items() if v[0] == self.var_canale.get())

        def lavoro():
            return canale, M.immagine_lwatv(canale)

        def fatto(r):
            c, img = r
            self.lwa_img = img
            self._mostra_lwa()

        def errore(e):
            self.lab_lwa.configure(image="", text=f"LWA TV non risponde ({type(e).__name__}). Riprovo fra poco.")
        self.in_background(lavoro, fatto=fatto, errore=errore, silenzioso=True)

    def _mostra_lwa(self):
        if self.lwa_img is None:
            return
        larg = max(200, self.scorrevoli[2].tela.winfo_width() - 40)
        img = self.lwa_img
        k = larg / img.width
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
        self.lwa_foto = ImageTk.PhotoImage(img)
        self.lab_lwa.configure(image=self.lwa_foto, text="")
        ora = datetime.now(timezone.utc)
        ore = M.ore_fa_lwa(ora, self.imp.lon)
        self.lab_lwa_info.configure(text=(
            f"Scaricata alle {datetime.now():%H:%M:%S}. L'immagine ha circa 30 s di ritardo.\n"
            f"Tempo siderale di LWA1: {M.num(M.lst_gradi(ora, -107.6284) / 15, 2)} h; "
            f"{self.imp.luogo} ha avuto questo cielo allo zenit circa {M.num(ore, 1)} ore fa."))

    def _giro_lwa(self):
        if self.var_lwa_auto.get() and self.nb.index("current") == 2:
            self.aggiorna_lwa()
        self.root.after(15000, self._giro_lwa)

    def confronta_lwa(self):
        if self.luogo_salvato is None:
            self.luogo_salvato = (self.imp.luogo, self.imp.lat, self.imp.lon, self.imp.quota, self.imp.f_mhz,
                                  self.imp.vista, self.imp.proiezione, self.imp.colori, self.imp.modo)
        la, lo, q = M.LUOGHI["LWA1 (New Mexico)"]
        self.imp.luogo, self.imp.lat, self.imp.lon, self.imp.quota = "LWA1 (New Mexico)", la, lo, q
        self.imp.vista, self.imp.proiezione, self.imp.colori, self.imp.modo = "cupola", "ortografica", "jet (come LWA TV)", "radio"
        for var, val in ((self.var_vista, "cupola"), (self.var_proiez, "ortografica"),
                         (self.var_colori, "jet (come LWA TV)"), (self.var_modo, "radio")):
            var.set(val)
        self.ora_vera.set(True)
        self.dt = datetime.now(timezone.utc) - timedelta(seconds=30)
        self.btn_torna.configure(state="normal")
        self.imposta_frequenza(38.0)
        self.stato("Ora vedi il modello come lo vedrebbe LWA1 adesso: confrontalo con l'immagine qui a destra. "
                   "Il modello non ha il Sole attivo né Giove, e ha 1–3° di risoluzione.")

    def torna_luogo(self):
        if not self.luogo_salvato:
            return
        (self.imp.luogo, self.imp.lat, self.imp.lon, self.imp.quota, f, self.imp.vista, self.imp.proiezione,
         self.imp.colori, self.imp.modo) = self.luogo_salvato
        self.luogo_salvato = None
        for var, val in ((self.var_vista, self.imp.vista), (self.var_proiez, self.imp.proiezione),
                         (self.var_colori, self.imp.colori), (self.var_modo, self.imp.modo)):
            var.set(val)
        self.btn_torna.configure(state="disabled")
        self.imposta_frequenza(f)

    # ------------------------------------------------------------------ ritagli SkyView
    def ritagli(self):
        if not self.selezione:
            return
        ra, dec = self.selezione
        w = tk.Toplevel(self.root)
        w.title(f"Ritagli radio – AR {ra / 15:.3f} h, dec {dec:+.2f}°")
        w.configure(bg=COLORI_SFONDO)
        f = ttk.Frame(w, padding=self.px(10))
        f.pack(fill="both", expand=True)
        celle = []
        for k, (s, t) in enumerate(M.RITAGLI):
            c = ttk.Frame(f)
            c.grid(row=k // 2, column=k % 2, padx=6, pady=6, sticky="nsew")
            ttk.Label(c, text=t, font=self.font_grande).pack()
            lab = ttk.Label(c, text="scarico da SkyView…")
            lab.pack()
            celle.append(lab)
        ttk.Label(f, text="Campo di 2° × 2° (la Luna piena è larga 0,5°). Fonte: NASA SkyView.",
                  wraplength=self.px(600)).grid(row=2, column=0, columnspan=2, pady=4)
        w.foto = []

        def lavoro():
            for k, (s, t) in enumerate(M.RITAGLI):
                try:
                    img = M.ritaglio_png(s, ra, dec, 2.0, 300)
                    self.coda.put(("ritaglio", w, celle[k], img, None))
                except Exception as e:  # noqa: BLE001
                    self.coda.put(("ritaglio", w, celle[k], None, f"non disponibile: {e}"))
                time.sleep(1)
        threading.Thread(target=lavoro, daemon=True).start()

    # ------------------------------------------------------------------ lavori in sottofondo
    def in_background(self, funzione, fatto=None, errore=None, silenzioso=False):
        def corri():
            try:
                r = funzione()
                self.coda.put(("fatto", fatto, r))
            except InterruptedError as e:
                self.coda.put(("errore", errore, e, True))
                self.coda.put(("stato", "Interrotto."))
            except Exception as e:  # noqa: BLE001
                if not silenzioso:
                    traceback.print_exc()
                self.coda.put(("errore", errore, e, silenzioso))
        threading.Thread(target=corri, daemon=True).start()

    def _ciclo(self):
        try:
            while True:
                m = self.coda.get_nowait()
                tipo = m[0]
                if tipo == "immagine":
                    self.rendering = False
                    self._mostra(m[1])
                    if self.richiesta:
                        self.chiedi_disegno(ritardo=0)
                elif tipo == "errore_disegno":
                    self.rendering = False
                    self.stato("Errore nel disegno: " + m[1])
                elif tipo == "stato":
                    self.stato(m[1])
                elif tipo == "progresso":
                    self.prog.configure(value=m[1])
                    if len(m) > 2:
                        self.stato(m[2])
                elif tipo == "fatto":
                    self.prog.configure(value=0)
                    if m[1]:
                        m[1](m[2])
                elif tipo == "errore":
                    self.prog.configure(value=0)
                    if m[1]:
                        m[1](m[2])
                    if not m[3]:
                        self.stato(f"Errore: {type(m[2]).__name__}: {m[2]}")
                elif tipo == "ridisegna":
                    self._aggiorna_etichette()
                    self.chiedi_disegno(ritardo=0)
                elif tipo == "ritaglio":
                    _, win, lab, img, err = m
                    try:
                        if img is not None:
                            ft = ImageTk.PhotoImage(img.resize((self.px(240), self.px(240))))
                            win.foto.append(ft)
                            lab.configure(image=ft, text="")
                        else:
                            lab.configure(text=err)
                    except tk.TclError:
                        pass
        except queue.Empty:
            pass
        self.root.after(80, self._ciclo)

    # ------------------------------------------------------------------ avvio e dati
    def _avvio(self):
        self._giro_lwa()

        def lavoro():
            self.coda.put(("stato", "Carico stelle e costellazioni…"))
            self.pittore.ottico.carica()
            self.coda.put(("ridisegna",))
            self.pittore.sole.aggiorna()
            if self.imp.modo == "occhio" and not self.pittore.visibile.pronto():
                self._scarica_visibile()
            ok = self._prepara(self.imp.modello)
            if ok:
                self.coda.put(("stato", "Pronto. " + SUGGERIMENTO))
            if not self.pittore.visibile.pronto() and not self.ferma.is_set():
                try:
                    self.pittore.visibile.scarica(self.ferma)
                except Exception as e:  # noqa: BLE001
                    M.log(f"Mellinger non scaricato: {e}")
            return ok
        self.in_background(lavoro)

    def _prepara(self, nome):
        if self.dati_pronti.get(nome):
            return True
        mod = M.MODELLI[nome]
        self.coda.put(("stato", f"Scarico il modello {mod['breve']} ({M.num(mod['mb'], 1)} MB) da Zenodo: "
                                f"serve solo la prima volta…"))

        def av(n, tot):
            if tot:
                self.coda.put(("progresso", n / tot, f"Scarico {mod['breve']}: {M.num(n / 1e6, 1)} di "
                                                     f"{M.num(tot / 1e6, 1)} MB"))
        try:
            ok = M.prepara_modello(nome, av, self.ferma)
        except ImportError:
            self.coda.put(("stato", "Manca il modulo h5py: serve per leggere il modello la prima volta."))
            return False
        except Exception as e:  # noqa: BLE001
            self.coda.put(("stato", f"Il modello {mod['breve']} non si è potuto scaricare ({type(e).__name__}: {e}). "
                                    f"Riprova più tardi: il download riprende da dove si è fermato."))
            return False
        self.dati_pronti[nome] = ok
        self.coda.put(("progresso", 0.0))
        self.coda.put(("ridisegna",))
        return ok

    def _scarica_visibile(self):
        try:
            self.pittore.visibile.scarica(self.ferma)
            self.coda.put(("ridisegna",))
            self.coda.put(("stato", "Mappa in luce visibile pronta."))
        except Exception as e:  # noqa: BLE001
            self.coda.put(("stato", f"La mappa in luce visibile non è arrivata ({type(e).__name__}): "
                                    f"mostro solo le stelle. SkyView a volte è sovraccarico, riprova più tardi."))

    def chiudi(self):
        self.ferma.set()
        if self.video_attivo:
            self.video_attivo.set()
        self._salva_impostazioni()
        self.root.destroy()


# =============================================================================
# TESTI
# =============================================================================
SUGGERIMENTO = ("Muovi il cursore della frequenza: in basso la Via Lattea radio è fortissima, "
                "in alto si spegne. Clic su un oggetto per la sua scheda.")
DESCR_MODELLI = {
    "GSM2008": ("Unisce 11 mappe vere del cielo (da 10 MHz a 94 GHz) con l'analisi delle componenti principali: "
                "dà il cielo diffuso a qualsiasi frequenza, risoluzione circa 1° (2° sopra 10 GHz). Non contiene il "
                "fondo cosmico a 2,7 K."),
    "LFSM": ("Costruito con le osservazioni di LWA1, il radiotelescopio del New Mexico di LWA TV: più fedele "
             "sotto 100 MHz, risoluzione circa 3°."),
}

GUIDA = """Cielo radio – come si legge

La cupola è tutto il cielo sopra di te, come se fossi sdraiato a guardare in alto: il centro è lo zenit, il bordo l'orizzonte, il Nord in alto e l'Est a sinistra (è giusto così: guardando in su, Est e Ovest si scambiano rispetto a una cartina).

Occhio radio: i colori sono la temperatura di brillanza del cielo, in kelvin, su una scala logaritmica. In radio le stelle quasi spariscono: domina la Via Lattea, perché gli elettroni velocissimi dei raggi cosmici girano nel campo magnetico della Galassia ed emettono luce di sincrotrone. Alle frequenze basse il cielo è caldissimo (decine di migliaia di kelvin a 38 MHz); salendo di frequenza si spegne e restano il piano galattico e le nubi di gas.

Sorgenti radio (cerchi verdi): Cassiopea A, Cigno A, Toro A sono le più brillanti. Clic per la scheda.

Sole e Luna: in radio il Sole è la sorgente più forte e si vede anche di giorno; la Luna, a bassa frequenza, è un'ombra sulla Via Lattea.

LWA TV: la scheda accanto mostra in diretta il cielo radio visto dal New Mexico. Con «Confronta» il programma disegna il modello come lo vedrebbe quella stazione, per metterli a confronto.

Fonti: GSM2008 (de Oliveira-Costa et al. 2008), LFSM (Dowell et al. 2017), mappa della Via Lattea di A. Mellinger e ritagli da NASA SkyView, stelle e costellazioni d3-celestial, Sole da NOAA SWPC (rete RSTN), LWA TV dell'Università del New Mexico.

© 2026 Edoardo Battistel"""


def spiegazione_frequenza(f):
    if f < 30:
        return ("Frequenze bassissime: la ionosfera della Terra le riflette e le distorce (si osservano solo di "
                "notte o da siti speciali). Il cielo è dominato dal sincrotrone della Galassia; il gas ionizzato vicino "
                "al piano galattico comincia ad assorbire e lo rende più scuro.")
    if f < 100:
        return ("La banda di LWA TV e di LOFAR: il sincrotrone della Via Lattea è fortissimo, Cassiopea A e Cigno A "
                "spiccano come punti. Giove e il Sole possono avere tempeste radio.")
    if f < 1000:
        return ("Onde metriche e decimetriche (radio FM a 100 MHz, la mappa di Haslam a 408 MHz): la Via Lattea "
                "radio è ancora brillante, con i grandi archi dei «loop» (resti di antiche supernove).")
    if f < 3000:
        return ("Banda L, attorno a 1,4 GHz: la riga a 21 cm dell'idrogeno neutro (quella delle tue osservazioni "
                "SALSA e PICTOR) sta a 1420 MHz. Il cielo diffuso è ormai tiepido, pochi kelvin fuori dal piano.")
    if f < 30000:
        return ("Microonde: il sincrotrone è debole; diventano importanti l'emissione libero-libero del gas "
                "ionizzato e la polvere che ruota velocemente (emissione «anomala» attorno a 20-30 GHz).")
    return ("Onde millimetriche: il cielo galattico è debolissimo e il fondo cosmico a 2,7 K (non incluso qui) "
            "dominerebbe; restano la polvere fredda e le nubi del piano galattico.")


# Pittore.copia(): un pittore che condivide i dati (per salvataggi e video in sottofondo)
def _copia(self):
    p = M.Pittore.__new__(M.Pittore)
    p.__dict__.update(self.__dict__)
    p._proiezioni = {}
    p.ultimi_valori = p.ultima_proiezione = p.ultima_cornice = None
    p.ultimi_oggetti = []
    return p


M.Pittore.copia = _copia


# =============================================================================
# AUTOTEST
# =============================================================================
def autotest():
    righe = []
    ok_tot = [0, 0]

    def esito(nome, ok, extra=""):
        ok_tot[0 if ok else 1] += 1
        righe.append(f"{'OK  ' if ok else 'NO  '} {nome}{(' – ' + extra) if extra else ''}")
        print(righe[-1], flush=True)

    # motore
    try:
        ip = np.random.default_rng(1).integers(0, 12 * 512 * 512, 200000)
        lon, lat = M.ring_ang(512, ip)
        esito("HEALPix ang2pix/pix2ang", bool(np.all(M.ang2pix_ring(512, np.radians(90 - lat), np.radians(lon)) == ip)))
        dt = datetime(2026, 11, 20, 9, 8, 18, tzinfo=timezone.utc)
        cor = M.Cornice(dt, 45.4064, 11.8768)
        c = M.posizioni_corpi(dt, 45.4064, 11.8768)
        rif = {"Sole": (20.3, 152.5), "Luna": (-35.9, 34.8), "Giove": (30.7, 257.5), "Venere": (34.8, 187.9)}
        err = max(max(abs(float(cor.altaz_da_data(c[k][0], c[k][1])[0]) - a),
                      abs(float(cor.altaz_da_data(c[k][0], c[k][1])[1]) - z)) for k, (a, z) in rif.items())
        esito("Posizioni di Sole, Luna, pianeti (vs astropy)", err < 0.15, f"scarto massimo {err:.2f}°")
        al, az = cor.altaz_da_j2000(350.850, 58.815)
        esito("Posizione di Cassiopea A (vs astropy)", abs(float(al) - 18.8) < 0.1 and abs(float(az) - 19.2) < 0.1)
    except Exception as e:  # noqa: BLE001
        esito("motore", False, f"{type(e).__name__}: {e}")
    # dati
    for nome in ("GSM2008",):
        try:
            pronto = M.npy_pronti(nome) or M.prepara_modello(nome)
            mod = M.ModelloCielo(nome)
            m38, m408, m23 = mod.mappa(38.0), mod.mappa(408.0), mod.mappa(23000.0)
            esito(f"{nome}: mappe a 38 MHz, 408 MHz, 23 GHz", pronto,
                  f"mediane {np.median(m38):.0f} K, {np.median(m408):.1f} K, {np.median(m23) * 1000:.2f} mK")
            esito(f"{nome}: valori plausibili", 1000 < np.median(m38) < 60000 and 8 < np.median(m408) < 60
                  and np.max(m408) > 300)
        except Exception as e:  # noqa: BLE001
            esito(f"{nome}", False, f"{type(e).__name__}: {e}")
    try:
        b = M.scarica_bytes(M.LWATV["1"][1], timeout=15, totale=30)
        esito("LWA TV raggiungibile", b[:4] == b"\x89PNG", f"{len(b)} byte")
    except Exception as e:  # noqa: BLE001
        esito("LWA TV raggiungibile", False, f"{type(e).__name__}")
    # finestra
    root = tk.Tk(className="CieloRadio")
    app = App(root, autotest=True)
    stato = {"t0": time.time(), "fase": 0}

    def passo():
        try:
            if stato["fase"] == 0:
                if app.foto is not None and app.pittore.ottico.linee and app.dati_pronti.get("GSM2008"):
                    esito("Finestra aperta e cielo disegnato", True, f"{app.img_corrente.size}")
                    stato["fase"] = 1
                elif time.time() - stato["t0"] > 240:
                    esito("Finestra aperta e cielo disegnato", app.foto is not None, "tempo scaduto per i dati")
                    stato["fase"] = 1
            elif stato["fase"] == 1:
                app.imposta_frequenza(1420)
                app._puntatore(type("E", (), {"x": app.tela.winfo_width() // 2, "y": app.tela.winfo_height() // 3})())
                esito("Puntatore", "Altezza" in app.lab_puntatore.cget("text"))
                ogg = app.pittore.ultimi_oggetti[0] if app.pittore.ultimi_oggetti else None
                if ogg:
                    app._clic(type("E", (), {"x": ogg[2], "y": ogg[3]})())
                    esito("Scheda di un oggetto", ogg[1].split()[0] in app.testo_info.get("1.0", "end"), ogg[1])
                for k in ("occhio", "radio"):
                    app.var_modo.set(k)
                    app._cambia_modo()
                app.var_vista.set("panorama")
                app._cambia_vista()
                app.var_testo.set("200 %")
                app._cambia_testo()
                stato["fase"] = 2
                stato["t1"] = time.time()
            elif stato["fase"] == 2 and not app.rendering and time.time() - stato["t1"] > 3:
                esito("Panorama e testo 200 %", app.img_corrente is not None)
                imp = M.Impostazioni(**dict(app.imp.__dict__))
                imp.vista = "cupola"
                p = M.CARTELLA / "autotest_cielo.png"
                app.pittore.copia().disegna(imp, app.dt, 1200, 1200).save(p)
                esito("Immagine PNG", p.exists() and p.stat().st_size > 50000, str(p))
                if M.shutil.which("ffmpeg"):
                    v = M.CARTELLA / "autotest_video.mp4"
                    app.pittore.copia().video(imp, app.dt, "notte", v, w=360, h=360, fotogrammi=12, fps=12)
                    esito("Video MP4 (ffmpeg)", v.exists() and v.stat().st_size > 10000, str(v))
                else:
                    esito("Video MP4 (ffmpeg)", False, "ffmpeg non trovato")
                app.var_testo.set("150 %")
                app._cambia_testo()
                app.var_vista.set("cupola")
                app._cambia_vista()
                stato["fase"] = 3
                stato["t2"] = time.time()
            elif stato["fase"] == 3 and time.time() - stato["t2"] > 2:
                app.chiudi()
                return
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            esito("finestra", False, f"{type(e).__name__}: {e}")
            app.chiudi()
            return
        root.after(500, passo)
    root.after(1000, passo)
    root.mainloop()
    righe.append(f"\nTotale: {ok_tot[0]} OK, {ok_tot[1]} NO  ({'TUTTO OK' if ok_tot[1] == 0 else 'CI SONO PROBLEMI'})")
    M.CARTELLA.mkdir(parents=True, exist_ok=True)
    (M.CARTELLA / "autotest.txt").write_text(f"Cielo radio {M.VERSIONE} – autotest del {datetime.now():%d/%m/%Y %H:%M}\n\n"
                                             + "\n".join(righe) + "\n", encoding="utf-8")
    print(righe[-1])
    print(f"Risultati in {M.CARTELLA / 'autotest.txt'}")
    return 0 if ok_tot[1] == 0 else 1


def main():
    if "--autotest" in sys.argv:
        sys.exit(autotest())
    root = tk.Tk(className="CieloRadio")
    try:
        ico = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "cielo_radio.png"
        if ico.exists():
            root.iconphoto(True, tk.PhotoImage(file=str(ico)))
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
