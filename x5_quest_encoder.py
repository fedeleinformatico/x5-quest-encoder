#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X5 → Quest Encoder
GUI per codificare video 360 equirettangolari (Insta360 X5 / X6 / Pro 2, già
stitchati ed esportati in ProRes) verso Meta Quest 3/3S e iniettare i metadati
spaziali. Pensata per macOS / Apple Silicon.

Riferimento completo: docs/Workflow_360_Quest3.md

Dipendenze esterne (riga di comando):
  - ffmpeg / ffprobe  (brew install ffmpeg)
  - exiftool          (brew install exiftool)   -> solo per i metadati 360

Avvio:  python3 x5_quest_encoder.py
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "X5 → Quest Encoder"

# Parametri x265 tarati per fascia di carico (pixel al secondo), vedi sezione 7
# del workflow: una stringa unica con vbv-maxrate=120000 strozza tutto quello
# che sta sopra il 6K60.
# {kf} = keyframe ogni secondo, calcolato dal framerate del sorgente (60 a 60p,
# 50 a 50p): un keyint fisso a 60 su un 8K50 sposta i keyframe a 1,2 s.
# Fascia bassa: 5.7K60, 6K60, 8K30 (fino a ~1150 Mpx/s).
X265_PARAMS_STD = (
    "keyint={kf}:min-keyint={kf}:bframes=3:aq-mode=3:"
    "psy-rd=2.0:psy-rdoq=1.0:sao=0:rc-lookahead=40:"
    "vbv-maxrate=120000:vbv-bufsize=240000"
)
# Fascia alta: 7K60, 8K50, 8K60 (sopra ~1300 Mpx/s). Il level non va forzato:
# x265 sceglie da solo Level 6.1 High tier per l'8K60 a 160 Mbps, mentre
# level-idc=6.1 rifiuta i frame più grandi (Pro 2 3D TB 7680×7680).
X265_PARAMS_HIGH = (
    "keyint={kf}:min-keyint={kf}:bframes=4:aq-mode=3:"
    "psy-rd=1.5:psy-rdoq=1.0:sao=0:rc-lookahead=25:"
    "vbv-maxrate=160000:vbv-bufsize=320000"
)
# Soglia tra le due fasce, in pixel codificati al secondo.
X265_HIGH_THRESHOLD = 1_200_000_000

# Tetti di bitrate imposti dal VBV (Mbps): servono per stimare la dimensione
# massima dell'output in modalità CRF.
X265_VBV_MAXRATE_MBPS = {"std": 120, "high": 160}

# h264_videotoolbox non apre l'encoder oltre 4096 px per lato (verificato su
# Apple Silicon): sopra si passa a libx264.
H264_VT_MAX_SIDE = 4096

# Rapporto larghezza/altezza del frame per modalità 3D: equirettangolare mono
# 2:1, stereo top-bottom 1:1 (es. Pro 2 8K 3D 7680×7680), side-by-side 4:1.
STEREO_ASPECT = {
    "Mono (2D)": 2,
    "Stereo Top-Bottom (TB)": 1,
    "Stereo Side-by-Side (SBS)": 4,
}

# Bitrate consigliati (Mbps) per gli encoder a bitrate fisso.
DEFAULT_BITRATE = {"hw": "120", "h264": "200"}

# AV1 (SVT-AV1): la Quest 3 lo decodifica in hardware. Misurato su 8K60 reale,
# a 122 Mbps rende come un HEVC hardware a 206 Mbps, in un sesto del tempo di
# x265 -preset slow. Solo CRF: SVT-AV1 rifiuta bitrate target sopra 100 Mbps.
X265_PRESETS = ["ultrafast", "fast", "medium", "slow", "slower"]
AV1_PRESETS = ["10", "9", "8", "7", "6"]
AV1_DEFAULT_CRF = "29"
# Bitrate misurati per la stima dello spazio: 122 Mbps a CRF 28 su 8K60,
# 127 a CRF 29 su 8K50, circa +9% per ogni punto di CRF in meno.
AV1_MBPS_AT_CRF28 = 127.0

# Velocità di codifica attese, in Mpx/s di frame in uscita (larghezza × altezza ×
# fps × secondi di video / secondi di orologio). Misurate su un 8K50 ProRes con
# un Mac Apple Silicon: SVT-AV1 preset 8 ≈ 0,09x (≈134 Mpx/s), hevc_videotoolbox
# ≈ 0,35x (≈500 Mpx/s, il collo di bottiglia è la decodifica del ProRes).
# I preset diversi dal misurato sono proporzioni indicative: dopo ogni encode
# riuscito il valore reale viene salvato in SPEED_FILE e sostituisce la stima.
SPEED_MPXS = {
    "av1": {"10": 240, "9": 180, "8": 134, "7": 95, "6": 65},
    "sw": {"ultrafast": 250, "fast": 80, "medium": 45, "slow": 22, "slower": 12},
    "hw": 500,
    "h264": 500,
}
SPEED_FILE = os.path.expanduser("~/.x5_quest_encoder_speeds.json")


def load_speeds():
    try:
        with open(SPEED_FILE) as f:
            return {k: float(v) for k, v in json.load(f).items()}
    except Exception:
        return {}


def save_speed(key, mpxs):
    """Media mobile fra il valore salvato e quello appena misurato."""
    data = load_speeds()
    data[key] = round(mpxs if key not in data else 0.5 * data[key] + 0.5 * mpxs, 1)
    try:
        with open(SPEED_FILE, "w") as f:
            json.dump(data, f, indent=1)
    except OSError:
        pass


def fmt_dur(sec):
    """Durata leggibile: '45 s', '12 min', '2 h 05 min'."""
    if sec < 90:
        return f"{int(sec)} s"
    mins = int(round(sec / 60))
    return f"{mins} min" if mins < 60 else f"{mins // 60} h {mins % 60:02d} min"


# Risoluzioni output: larghezze dei preset, l'altezza dipende dalla modalità 3D
# (le etichette mostrano il caso mono 2:1). None = mantieni originale.
# Si può solo scendere: se il target non è più piccolo del sorgente, lo
# scaling viene saltato (vedi _scale_plan).
RESOLUTIONS = {
    "Originale (nessun ridimensionamento)": None,
    "8K — 7680×3840 (nativo X5 / X6 / Pro 2 · 8K30 = qualità max)": (7680, 3840),
    "7K — 6656×3328 (a 60fps pochi bit per pixel)": (6656, 3328),
    "6K — 6016×3008 (nativo X6 · 6K60 per il movimento)": (6016, 3008),
    "5.7K — 5760×2880 (nativo X5)": (5760, 2880),
    "4K — 3840×1920 (leggerissimo, per test/anteprime)": (3840, 1920),
    "Personalizzata…": "custom",
}

# Mappatura tag colore per i tre scenari.
COLOR_TAGS = {
    "SDR (BT.709)": ["-color_primaries", "bt709",
                     "-color_trc", "bt709",
                     "-colorspace", "bt709"],
    "HDR PQ (BT.2020 / HDR10)": ["-color_primaries", "bt2020",
                                 "-color_trc", "smpte2084",
                                 "-colorspace", "bt2020nc"],
    "HDR HLG (BT.2020)": ["-color_primaries", "bt2020",
                          "-color_trc", "arib-std-b67",
                          "-colorspace", "bt2020nc"],
}

# Gestione traccia audio.
AUDIO_MODES = {
    "AAC stereo 320 kbps (default)": "stereo",
    "AAC multicanale 512 kbps (mantiene i canali)": "multi",
    "Nessun audio": "none",
}

# ExifTool deve poter scrivere oltre i 4 GB: senza questo, su un MP4 grande
# fallisce con "End of processing at large atom (LargeFileSupport not enabled)".
EXIFTOOL_BASE = ["exiftool", "-api", "LargeFileSupport=1"]


def which(cmd):
    return shutil.which(cmd)


_probe_cache = {}


def ffprobe_info(path):
    """Dict con codec, width, height, fps, pix_fmt, transfer, primaries, duration
    del primo stream video, o None. Con cache (chiave: percorso + mtime)."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key in _probe_cache:
        return _probe_cache[key]
    info = None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=codec_name,width,height,avg_frame_rate,pix_fmt,color_transfer,"
             "color_primaries:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout)
        st = data["streams"][0]
        num, _, den = st.get("avg_frame_rate", "0/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 0.0
        try:
            dur = float(data.get("format", {}).get("duration"))
        except (TypeError, ValueError):
            dur = None
        info = {"codec": st.get("codec_name", "?"), "width": int(st["width"]),
                "height": int(st["height"]), "fps": fps,
                "pix_fmt": st.get("pix_fmt", ""),
                "transfer": st.get("color_transfer", ""),
                "primaries": st.get("color_primaries", ""), "duration": dur}
    except Exception:
        pass
    _probe_cache[key] = info
    return info


def ffprobe_video(path):
    """(larghezza, altezza, fps) del primo stream video, o None. Con cache."""
    i = ffprobe_info(path)
    return (i["width"], i["height"], i["fps"]) if i else None


def ffprobe_duration(path):
    """Ritorna la durata in secondi (float) o None."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def cmd_to_str(cmd):
    """Stringa shell copia-incollabile (quota spazi e caratteri speciali)."""
    return " ".join(shlex.quote(str(a)) for a in cmd)


def human_gb(n):
    return f"{n / 1e9:.1f} GB"


def free_space(path):
    """Byte liberi sul volume che contiene path (o la sua cartella padre)."""
    p = os.path.abspath(path)
    if not os.path.isdir(p):
        p = os.path.dirname(p) or "."
    try:
        return shutil.disk_usage(p).free
    except Exception:
        return None


class EncoderApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("880x820")
        root.minsize(720, 560)

        self.proc = None
        self.worker = None
        self.log_q = queue.Queue()
        self._prog_live = False          # l'ultima riga del log è una riga di avanzamento
        self.ui_q = queue.Queue()        # callback da eseguire nel thread della GUI
        # la GUI parte con AV1 selezionato: la famiglia iniziale è quella
        self._crf_family = "av1"
        self._crf_saved = {"x265": ("slow", "16"), "av1": ("8", AV1_DEFAULT_CRF)}
        self.cancel_flag = threading.Event()

        self._build_ui()
        self._check_deps()
        self.root.after(100, self._drain_log)

    # ---------------------------------------------------------------- UI
    def _hint(self, parent, text, **grid):
        """Etichetta-guida grigia sotto un controllo."""
        lbl = ttk.Label(parent, text=text, foreground="#7a7a7a",
                        font=("", 10), wraplength=800, justify="left")
        if grid:
            lbl.grid(**grid)
        else:
            lbl.pack(anchor="w", padx=8, pady=(0, 4))
        return lbl

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- Log (fisso in basso) ---
        frm_log = ttk.LabelFrame(self.root, text="Log")
        frm_log.pack(side="bottom", fill="both", expand=False, **pad)
        self.show_raw = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_log, text="Mostra l'uscita grezza di ffmpeg (riga di avanzamento, aggiornata sul posto)",
                        variable=self.show_raw).pack(side="top", anchor="w", padx=6)
        self.txt = tk.Text(frm_log, height=9, wrap="word", state="disabled",
                           background="#111", foreground="#ddd", insertbackground="#ddd")
        self.txt.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(frm_log, command=self.txt.yview)
        sb.pack(side="right", fill="y", pady=6)
        self.txt.config(yscrollcommand=sb.set)

        # --- Azioni (fisse, sopra il log) ---
        frm_act = ttk.Frame(self.root)
        frm_act.pack(side="bottom", fill="x", **pad)
        self.btn_run = ttk.Button(frm_act, text="▶  Avvia", command=self.start)
        self.btn_run.pack(side="left", padx=6)
        self.btn_preview = ttk.Button(frm_act, text="⌗  Mostra comando", command=self.preview_cmd)
        self.btn_preview.pack(side="left", padx=6)
        self.btn_check = ttk.Button(frm_act, text="◷  Stima spazio", command=self.check_space)
        self.btn_check.pack(side="left", padx=6)
        self.btn_cancel = ttk.Button(frm_act, text="■  Annulla", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        self.progress = ttk.Progressbar(frm_act, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=6)
        self.lbl_status = ttk.Label(frm_act, text="Pronto", width=26)
        self.lbl_status.pack(side="right", padx=6)

        # --- Area configurazione scrollabile (riempie il resto) ---
        container = ttk.Frame(self.root)
        container.pack(side="top", fill="both", expand=True)
        canvas = tk.Canvas(container, highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(canvas)
        body_id = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(body_id, width=e.width))
        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        # rotella del mouse attiva solo quando il puntatore è sull'area di configurazione
        def _wheel(e):
            canvas.yview_scroll(-1 if e.delta > 0 else 1, "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # --- File input ---
        frm_in = ttk.LabelFrame(self.body, text="File sorgente (ProRes / MOV)")
        frm_in.pack(fill="x", **pad)

        row_in = ttk.Frame(frm_in)
        row_in.pack(fill="x")
        self.lst_files = tk.Listbox(row_in, height=4, selectmode=tk.EXTENDED)
        self.lst_files.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        btn_col = ttk.Frame(row_in)
        btn_col.pack(side="right", fill="y", padx=6, pady=6)
        ttk.Button(btn_col, text="Aggiungi…", command=self.add_files).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="Rimuovi", command=self.remove_selected).pack(fill="x", pady=2)
        ttk.Button(btn_col, text="Svuota", command=self.clear_files).pack(fill="x", pady=2)
        self._hint(frm_in, "→ Usa il master ProRes 422 esportato da Premiere, non l'MP4 della camera. "
                           "Più file = batch. L'output esce con suffisso _quest.mp4 nella cartella scelta qui sotto.")

        # --- Analisi e consigli ---
        frm_adv = ttk.LabelFrame(self.body, text="Analisi sorgente e consigli")
        frm_adv.pack(fill="x", **pad)
        self.txt_adv = tk.Text(frm_adv, height=12, wrap="word", state="disabled", relief="flat",
                               background=self.root.cget("background"), font=("", 11))
        self.txt_adv.pack(fill="x", padx=8, pady=(6, 2))
        for tag, color in (("ok", "#060"), ("warn", "#a60"), ("bad", "#b00"), ("dim", "#666")):
            self.txt_adv.tag_configure(tag, foreground=color)
        self.txt_adv.tag_configure("head", font=("", 11, "bold"))
        row_adv = ttk.Frame(frm_adv)
        row_adv.pack(fill="x")
        self.btn_apply = ttk.Button(row_adv, text="✓  Applica i consigli", command=self.apply_advice,
                                    state="disabled")
        self.btn_apply.pack(side="left", padx=8, pady=(0, 6))
        self.lbl_adv_note = ttk.Label(row_adv, text="", foreground="#7a7a7a", font=("", 10))
        self.lbl_adv_note.pack(side="left", padx=4, pady=(0, 6))
        self._fixes = []
        self._an_gen = 0
        self._an_after = None
        self._adv_text(["Aggiungi un file sorgente: qui compaiono formato, tempo stimato e consigli sui settaggi."],
                       ["dim"])

        # --- Destinazione ---
        frm_out = ttk.LabelFrame(self.body, text="Cartella di destinazione")
        frm_out.pack(fill="x", **pad)
        row_out = ttk.Frame(frm_out)
        row_out.pack(fill="x")
        self.same_folder = tk.BooleanVar(value=True)
        ttk.Checkbutton(row_out, text="Accanto al sorgente",
                        variable=self.same_folder,
                        command=self._sync_out_widgets).pack(side="left", padx=6, pady=6)
        self.out_dir = tk.StringVar(value="")
        self.ent_out = ttk.Entry(row_out, textvariable=self.out_dir, state="disabled")
        self.ent_out.pack(side="left", fill="x", expand=True, padx=6)
        self.btn_out = ttk.Button(row_out, text="Scegli…", command=self.pick_out_dir, state="disabled")
        self.btn_out.pack(side="left", padx=6)

        row_free = ttk.Frame(frm_out)
        row_free.pack(fill="x")
        self.lbl_free = ttk.Label(row_free, text="Spazio libero: —", foreground="#444")
        self.lbl_free.pack(side="left", padx=6, pady=(0, 6))
        ttk.Button(row_free, text="Aggiorna", command=self.refresh_free).pack(side="left", padx=6, pady=(0, 6))

        self.ignore_space = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_out, text="Ignora il controllo dello spazio (sconsigliato)",
                        variable=self.ignore_space).pack(anchor="w", padx=6)
        self._hint(frm_out, "→ Un 360 a 8K in ProRes consuma decine di GB e l'output HEVC può arrivare a diversi GB per "
                            "minuto. Se il volume del sorgente è pieno, punta la destinazione su un altro disco: "
                            "lavorare in lettura da un disco e in scrittura su un altro è anche più veloce. "
                            "Prima di ogni file la GUI stima l'output e si ferma se non ci sta, invece di scrivere "
                            "un MP4 troncato.")

        # --- Encoder ---
        frm_enc = ttk.LabelFrame(self.body, text="Encoder")
        frm_enc.pack(fill="x", **pad)

        self.encoder = tk.StringVar(value="av1")
        ttk.Radiobutton(frm_enc, text="AV1 — libsvtav1 (Quest 3: qualità di un HEVC a 200 Mbps usandone 127) ★ consigliato",
                        variable=self.encoder, value="av1",
                        command=self._sync_enc_widgets).grid(row=0, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        ttk.Radiobutton(frm_enc, text="Hardware HEVC — hevc_videotoolbox (il più veloce, ~4 min/clip)",
                        variable=self.encoder, value="hw",
                        command=self._sync_enc_widgets).grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        ttk.Radiobutton(frm_enc, text="Software HEVC — libx265 (resa migliore in HEVC, molto lento)",
                        variable=self.encoder, value="sw",
                        command=self._sync_enc_widgets).grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        ttk.Radiobutton(frm_enc, text="H.264 old-style — h264_videotoolbox (massima compatibilità)",
                        variable=self.encoder, value="h264",
                        command=self._sync_enc_widgets).grid(row=3, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        self._hint(frm_enc, "→ AV1 = default per la Quest 3: verificato nel visore, decodifica hardware, ~40% di bitrate in meno "
                            "a parità di qualità e sei volte più rapido di x265 slow (ma SVT-AV1 dà l'8K per "
                            "sperimentale). HEVC HW = il più veloce, da usare quando il player non gestisce AV1 o "
                            "servono tempi minimi: è il meno efficiente, a parità di qualità gli serve il 60% di "
                            "bitrate in più di x265. Software = la resa migliore in HEVC, molto più lento. H.264 = solo per compatibilità con player/dispositivi "
                            "vecchi — vedi i limiti nel riquadro H.264 più sotto.",
                   row=4, column=0, columnspan=4, sticky="w", padx=6)

        # preset (solo software)
        ttk.Label(frm_enc, text="Preset x265:").grid(row=5, column=0, sticky="e", padx=6)
        self.preset = tk.StringVar(value="8")
        self.cmb_preset = ttk.Combobox(frm_enc, textvariable=self.preset, width=10, state="readonly",
                                       values=AV1_PRESETS)
        self.cmb_preset.grid(row=5, column=1, sticky="w", padx=6, pady=2)

        ttk.Label(frm_enc, text="CRF (sw):").grid(row=5, column=2, sticky="e", padx=6)
        self.crf = tk.StringVar(value=AV1_DEFAULT_CRF)
        self.spn_crf = ttk.Spinbox(frm_enc, from_=18, to=40, textvariable=self.crf, width=6)
        self.spn_crf.grid(row=5, column=3, sticky="w", padx=6, pady=2)
        self._hint(frm_enc, "→ x265: il preset vale quanto un gradino di bitrate — misurato su 8K60, 'slow' a 120 Mbps rende "
                            "come 'medium' a 160 e come l'hardware a 165. 'fast' se il tempo conta. "
                            "CRF: 16 = altissima qualità; più basso (14) = più pesante, più alto (18-20) = più leggero. "
                            "AV1: preset 8 è il punto di equilibrio (più basso = più lento e più bello). "
                            "CRF 29 ≈ 127 Mbps su un 8K50, CRF 28 ≈ 122 su un 8K60: sono i valori verificati nel "
                            "visore, oltre non si vede differenza. "
                            "I parametri x265 si scelgono da soli in base al carico: fino a 6K60/8K30 tetto VBV 120 Mbps, "
                            "7K60/8K50/8K60 tetto 160 Mbps. "
                            "In CRF la dimensione finale non è prevedibile: la stima usa il tetto VBV, quindi è prudenziale.",
                   row=6, column=0, columnspan=4, sticky="w", padx=6)

        # bitrate (hardware HEVC e H264)
        ttk.Label(frm_enc, text="Bitrate (Mbps):").grid(row=7, column=0, sticky="e", padx=6)
        self.bitrate = tk.StringVar(value=DEFAULT_BITRATE["hw"])
        self.spn_br = ttk.Spinbox(frm_enc, from_=40, to=250, textvariable=self.bitrate, width=6)
        self.spn_br.grid(row=7, column=1, sticky="w", padx=6, pady=2)
        self._hint(frm_enc, "→ Per HEVC HW: 120 Mbps per 6K60 e 8K30 da file locale (140-150 se vedi blocchi su acqua/foglie). "
                            "Per H.264 old-style: 200 Mbps è il valore classico Quest. Cambiando encoder il valore "
                            "consigliato si imposta da solo. Promemoria: 120 Mbps = circa 0,9 GB al minuto.",
                   row=8, column=0, columnspan=4, sticky="w", padx=6)

        # --- Riquadro limiti H.264 ---
        frm_h264 = ttk.LabelFrame(self.body, text="ℹ︎ H.264 old-style — limiti da sapere")
        frm_h264.pack(fill="x", **pad)
        self._hint(frm_h264,
                   "• La Quest 3 decodifica HEVC in hardware fino all'8K, ma per l'H.264 il limite hardware è più basso: "
                   "il 5.7K60 è al confine e può ricadere in decodifica software → stutter/frame drop nel visore.\n"
                   "• 200 Mbps è uno spike alto: il buffer della Quest è limitato e i picchi fanno scattare il 360 più del "
                   "bitrate medio. Qui si usa bufsize ridotto per attenuarlo, ma il file resta pesante.\n"
                   "• Sopra 4096 px per lato l'encoder hardware H.264 del Mac non parte: la GUI passa da sola a "
                   "libx264 (software, lento) — ma a quelle risoluzioni quasi nessun player H.264 regge. "
                   "Per l'H.264 conviene scegliere 4K qui sotto.\n"
                   "• Solo 8-bit SDR: l'H.264 old-style esce a yuv420p. Se la sorgente è HDR, va consegnata in HEVC.\n"
                   "• Quando usarlo: solo per riprodurre su player datati o dispositivi che NON supportano HEVC. "
                   "Per la Quest 3/3S, l'HEVC è sempre la scelta migliore (più leggero E più fluido).")

        # --- Colore ---
        frm_col = ttk.LabelFrame(self.body, text="Spazio colore (deve combaciare con la sorgente!)")
        frm_col.pack(fill="x", **pad)
        row_col = ttk.Frame(frm_col)
        row_col.pack(fill="x")
        self.color = tk.StringVar(value="SDR (BT.709)")
        ttk.Combobox(row_col, textvariable=self.color, state="readonly",
                     values=list(COLOR_TAGS.keys()), width=30).pack(side="left", padx=6, pady=6)
        ttk.Button(row_col, text="Rileva dalla sorgente",
                   command=self.detect_color).pack(side="left", padx=6)
        self._hint(frm_col, "→ IL PUNTO PIÙ DELICATO. Premi 'Rileva' prima di tutto: se la sorgente è HDR e qui resta "
                            "BT.709, nel visore i colori escono slavati/sbagliati. SDR per girato sviluppato a Rec.709, "
                            "PQ per HDR10, HLG per girato HLG. La GUI non converte HDR→SDR (quello si fa in Premiere).")

        # --- Risoluzione output ---
        frm_res = ttk.LabelFrame(self.body, text="Risoluzione output (ridimensionamento)")
        frm_res.pack(fill="x", **pad)
        row_res = ttk.Frame(frm_res)
        row_res.pack(fill="x")
        self.resolution = tk.StringVar(value="Originale (nessun ridimensionamento)")
        ttk.Combobox(row_res, textvariable=self.resolution, state="readonly",
                     values=list(RESOLUTIONS.keys()), width=48,
                     ).pack(side="left", padx=6, pady=6)
        self.resolution.trace_add("write", lambda *a: self._sync_res_widgets())
        ttk.Label(row_res, text="Larghezza:").pack(side="left", padx=(12, 2))
        self.custom_w = tk.StringVar(value="6016")
        self.spn_w = ttk.Spinbox(row_res, from_=1024, to=8192, increment=128,
                                 textvariable=self.custom_w, width=7, state="disabled")
        self.spn_w.pack(side="left")
        ttk.Label(row_res, text="× altezza automatica").pack(side="left", padx=(2, 6))
        self._hint(frm_res, "→ 'Originale' mantiene la risoluzione del ProRes (preferibile se coincide col target). "
                            "Il limite non è il decoder della Quest, sono i bit per pixel: 60 fps o nitidezza, non entrambi. "
                            "Movimento → 6K60; nitidezza → 8K30. Un 8K60 da Pro 2 va scalato a 6K60 se strozzato. "
                            "'Personalizzata' = scrivi la larghezza. L'altezza segue la Modalità 3D: metà per il mono (2:1), "
                            "uguale per lo stereo Top-Bottom (1:1), un quarto per il Side-by-Side (4:1). "
                            "Scala lanczos, senza riesportare da Premiere. Si può solo scendere: se il target non è più "
                            "piccolo del sorgente lo scaling viene saltato (lo dice il log).")

        # --- Audio ---
        frm_aud = ttk.LabelFrame(self.body, text="Audio")
        frm_aud.pack(fill="x", **pad)
        self.audio = tk.StringVar(value="AAC stereo 320 kbps (default)")
        ttk.Combobox(frm_aud, textvariable=self.audio, state="readonly",
                     values=list(AUDIO_MODES.keys()), width=46).pack(side="left", padx=6, pady=6)
        self._hint(frm_aud, "→ Stereo va bene per il 99% dei casi. 'Multicanale' se il master ha audio spaziale a 4 canali "
                            "e non vuoi il downmix — attenzione: i metadati ambisonici (SA3D) NON vengono scritti da "
                            "exiftool, per quelli serve il tool spatial-media di Google. Se la sorgente non ha audio, "
                            "la traccia viene semplicemente saltata.")

        # --- Metadati 360 ---
        frm_meta = ttk.LabelFrame(self.body, text="Metadati 360")
        frm_meta.pack(fill="x", **pad)
        self.inject_meta = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_meta, text="Inietta metadati equirettangolari dopo l'encoding",
                        variable=self.inject_meta).grid(row=0, column=0, columnspan=4, sticky="w", padx=6, pady=2)

        ttk.Label(frm_meta, text="Modalità 3D:").grid(row=1, column=0, sticky="e", padx=6)
        self.stereo = tk.StringVar(value="Mono (2D)")
        ttk.Combobox(frm_meta, textvariable=self.stereo, state="readonly", width=22,
                     values=["Mono (2D)",
                             "Stereo Top-Bottom (TB)",
                             "Stereo Side-by-Side (SBS)"]).grid(row=1, column=1, sticky="w", padx=6, pady=2)
        self._hint(frm_meta, "→ Mono per X5, X6 e Pro 2 in modalità 2D. Top-Bottom per la Pro 2 in 3D (8K 3D = 7680×7680). "
                             "Top-Bottom / Side-by-Side solo se hai girato/montato in 3D stereoscopico (occhio sx e dx affiancati o sovrapposti nel frame). "
                             "Sbagliare qui fa vedere doppio o piatto nel visore.",
                   row=2, column=0, columnspan=4, sticky="w", padx=6)

        self.overwrite_meta = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_meta, text="Sovrascrivi senza creare backup _original",
                        variable=self.overwrite_meta).grid(row=3, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        self._hint(frm_meta, "→ Spuntato: niente file di backup (più ordine). Tolto: exiftool tiene una copia "
                             "_original di sicurezza accanto al file — che su un 360 significa raddoppiare i GB.",
                   row=4, column=0, columnspan=4, sticky="w", padx=6)

        ttk.Button(frm_meta, text="Solo metadati su MP4 esistente…",
                   command=self.meta_only).grid(row=5, column=0, sticky="w", padx=6, pady=4)
        ttk.Button(frm_meta, text="Verifica metadati di un file…",
                   command=self.verify_meta).grid(row=5, column=1, sticky="w", padx=6, pady=4)
        self._hint(frm_meta, "→ 'Solo metadati' inietta su un MP4 già pronto senza ricodificare (usa la Modalità 3D qui sopra): "
                             "è la strada giusta per recuperare un encode finito male. ExifTool riscrive l'INTERO file, "
                             "quindi su un 360 da 10 GB servono minuti e altrettanti GB liberi sul disco: la GUI li controlla "
                             "prima di partire. 'Verifica' rilegge Spherical, colore e codec tag del file.",
                   row=6, column=0, columnspan=4, sticky="w", padx=6)

        self._sync_enc_widgets()
        self._sync_res_widgets()
        self._sync_out_widgets()
        for v in (self.encoder, self.preset, self.crf, self.bitrate, self.resolution, self.custom_w,
                  self.stereo, self.color, self.audio, self.same_folder, self.out_dir,
                  self.inject_meta):
            v.trace_add("write", lambda *a: self.schedule_analysis())

    # ------------------------------------------------- analisi e consigli
    def _adv_text(self, lines, tags):
        t = self.txt_adv
        t.config(state="normal")
        t.delete("1.0", "end")
        for line, tag in zip(lines, tags):
            t.insert("end", line + "\n", tag)
        t.config(state="disabled")
        # altezza = numero di righe visive, così il riquadro non scorre dentro lo scroll esterno
        t.config(height=max(3, min(24, int(t.index("end-1c").split(".")[0]) + sum(len(l) // 105 for l in lines))))

    def schedule_analysis(self):
        """Rilancia l'analisi dopo un attimo (evita raffiche mentre si digita)."""
        if self._an_after:
            self.root.after_cancel(self._an_after)
        self._an_after = self.root.after(350, self._start_analysis)

    def _start_analysis(self):
        self._an_after = None
        files = list(self.lst_files.get(0, "end"))
        self._an_gen += 1
        gen = self._an_gen
        if not files:
            self._fixes = []
            self.btn_apply.config(state="disabled")
            self.lbl_adv_note.config(text="")
            self._adv_text(["Aggiungi un file sorgente: qui compaiono formato, tempo stimato e consigli "
                            "sui settaggi."], ["dim"])
            return
        if not which("ffprobe"):
            return

        def _probe():                   # ffprobe può essere lento su dischi di rete: fuori dal thread GUI
            for f in files:
                ffprobe_info(f)
            self.ui_q.put(lambda: gen == self._an_gen and self._render_analysis(files))
        threading.Thread(target=_probe, daemon=True).start()

    def mpxs(self, enc=None, preset=None):
        """(Mpx/s, misurato?) per l'encoder: valore imparato sul Mac, altrimenti la stima di base."""
        enc = enc or self.encoder.get()
        preset = preset or self.preset.get()
        key = f"{enc}:{preset}" if enc in ("av1", "sw") else enc
        learned = load_speeds().get(key)
        if learned:
            return learned, True
        base = SPEED_MPXS.get(enc, 500)
        if isinstance(base, dict):
            base = base.get(preset, 100)
        return float(base), False

    def job_pixels(self, src):
        """Pixel totali da codificare per un file (frame in uscita × numero di frame)."""
        info = ffprobe_info(src)
        size = self._out_size(src)
        if not info or not size or not info["duration"]:
            return None
        return size[0] * size[1] * info["fps"] * info["duration"]

    def _render_analysis(self, files):
        lines, tags, fixes = [], [], []

        def add(text, tag="", fix=None):
            lines.append(text)
            tags.append(tag)
            if fix:
                fixes.append(fix)

        enc = self.encoder.get()
        infos = {f: ffprobe_info(f) for f in files}
        total_px = 0.0
        for f in files:
            i = infos[f]
            name = os.path.basename(f)
            if not i:
                add(f"• {name}: non riesco a leggerlo (ancora in scrittura, o non è un video?)", "warn")
                continue
            tr = i["transfer"]
            col = ("HDR PQ" if tr == "smpte2084" else "HDR HLG" if tr == "arib-std-b67"
                   else "SDR BT.709" if i["primaries"] in ("bt709", "") else i["primaries"])
            dur = i["duration"]
            px = self.job_pixels(f)
            total_px += px or 0
            add(f"• {name}: {i['width']}×{i['height']} · {i['fps']:.0f} fps · {i['codec']} · {col}"
                + (f" · {fmt_dur(dur)}" if dur else ""), "head")

        # --- tempo e dimensione
        speed, learned = self.mpxs()
        if total_px:
            est = total_px / (speed * 1e6)
            sizes = [self.estimate_output(f, (infos[f] or {}).get("duration")) for f in files]
            size_txt = ""
            if all(sizes):
                # per l'AV1 la stima di estimate_output include +20% di margine
                size_txt = f" · output ~{human_gb(sum(sizes) / (1.2 if enc == 'av1' else 1))}"
            add(f"⏱  Tempo stimato: ~{fmt_dur(est)}{size_txt}", "head")
            add("    " + ("misurato su questo Mac nelle codifiche precedenti" if learned else
                          "stima di base: si affina da sola dopo ogni codifica riuscita"), "dim")
            alts = []
            for e, pr, label in (("av1", "10", "AV1 preset 10"), ("hw", "", "HEVC hardware")):
                if (e, pr) != (enc, self.preset.get() if enc == "av1" else ""):
                    alts.append(f"{label} ~{fmt_dur(total_px / (self.mpxs(e, pr or None)[0] * 1e6))}")
            if alts:
                add("    alternative: " + " · ".join(alts), "dim")
        else:
            add("⏱  Tempo non stimabile (durata o risoluzione non leggibili).", "dim")

        # --- consigli
        add("", "")
        add("Consigli", "head")
        n_tips = len(lines)
        multi = len(files) > 1
        for f in files:
            main = infos[f]
            if not main:
                continue
            pre = f"[{os.path.basename(f)}] " if multi else ""
            w, h, fps = main["width"], main["height"], main["fps"]
            # colore
            tr = main["transfer"]
            want = ("HDR PQ (BT.2020 / HDR10)" if tr == "smpte2084" else
                    "HDR HLG (BT.2020)" if tr == "arib-std-b67" else None)
            if want and self.color.get() != want:
                add(pre + f"⚠ La sorgente è {want.split(' (')[0]} ma il colore impostato è «{self.color.get()}»: "
                    f"nel visore i colori uscirebbero sbagliati.", "bad",
                    lambda want=want: self.color.set(want))
            elif not want and self.color.get() != "SDR (BT.709)" and main["primaries"] in ("bt709", ""):
                add(pre + "⚠ La sorgente è SDR BT.709 ma il colore impostato è HDR.", "bad",
                    lambda: self.color.set("SDR (BT.709)"))
            if want and enc == "h264":
                add(pre + "⚠ H.264 old-style è solo 8-bit SDR: con sorgente HDR consegna in HEVC o AV1.", "bad")
            # forma del frame / stereo
            ratio = w / h if h else 0
            mode = self.stereo.get()
            if abs(ratio - 1) < 0.02 and mode == "Mono (2D)":
                add(pre + "⚠ Il frame è quadrato (1:1): sembra stereo Top-Bottom, ma la modalità 3D è Mono.", "warn",
                    lambda: self.stereo.set("Stereo Top-Bottom (TB)"))
            elif abs(ratio - 4) < 0.05 and mode == "Mono (2D)":
                add(pre + "⚠ Il frame è 4:1: sembra stereo Side-by-Side, ma la modalità 3D è Mono.", "warn",
                    lambda: self.stereo.set("Stereo Side-by-Side (SBS)"))
            elif abs(ratio - 2) < 0.02 and mode != "Mono (2D)":
                add(pre + "⚠ Il frame è 2:1 (mono) ma la modalità 3D è stereo: nel visore si vedrebbe doppio.", "warn",
                    lambda: self.stereo.set("Mono (2D)"))
            elif min(abs(ratio - r) for r in (1, 2, 4)) > 0.05:
                add(pre + f"⚠ Proporzioni {w}×{h}: non sembra un equirettangolare 360 (2:1, 1:1 o 4:1).", "warn")
            # framerate
            if 1 <= fps < 45:
                add(pre + f"⚠ {fps:.0f} fps: in movimento, nel visore, il 360 a bassa frequenza è quasi inguardabile. "
                    f"Meglio girare a 50/60.", "warn")
            if main["codec"] not in ("prores", "dnxhd", "dnxhr", "cfhd"):
                add(pre + f"💡 La sorgente è {main['codec']}, non un master: la qualità di partenza è già "
                    f"compressa. Meglio il ProRes esportato da Premiere.", "dim")
        main = next((infos[f] for f in files if infos[f] and infos[f]["width"] >= 7000),
                    next((infos[f] for f in files if infos[f]), None))
        if main:
            w, h, fps = main["width"], main["height"], main["fps"]
            # AV1: CRF in base a risoluzione e fps (valori verificati nel visore)
            if enc == "av1" and w >= 7000:
                try:
                    crf = int(float(self.crf.get() or AV1_DEFAULT_CRF))
                except ValueError:
                    crf = 0
                good = 28 if fps >= 55 else 29
                if crf != good:
                    add(f"💡 AV1 su 8K{fps:.0f}: il CRF verificato nel visore è {good} (ora {crf}).", "warn",
                        lambda good=good: self.crf.set(str(good)))
                else:
                    add(f"✓ CRF {crf} è il valore verificato nel visore per l'8K{fps:.0f}.", "ok")
            # HEVC a pochi bit per pixel
            if enc in ("hw", "sw") and w * h * fps > X265_HIGH_THRESHOLD and \
                    RESOLUTIONS.get(self.resolution.get()) is None:
                add(f"💡 8K a {fps:.0f} fps in HEVC dà pochi bit per pixel: o passi ad AV1, o scali a 6K60 "
                    f"(più nitido in movimento).", "warn",
                    lambda: self.resolution.set(next(k for k in RESOLUTIONS if k.startswith("6K"))))
            if enc == "av1" and w >= 7000:
                add("💡 SVT-AV1 dichiara l'8K sperimentale: guarda il primo file nel visore prima di produrre il resto.",
                    "dim")
            if enc == "hw":
                try:
                    bitrate = int(float(self._bitrate()))
                except ValueError:
                    bitrate = 0
                if bitrate and bitrate < 110 and w >= 7000:
                    add(f"💡 HEVC hardware a {bitrate} Mbps su 8K: sotto i ~120 Mbps (quelli usati finora nel "
                        f"visore) i blocchi su acqua e foglie diventano visibili.", "warn",
                        lambda: self.bitrate.set(DEFAULT_BITRATE["hw"]))
                elif bitrate > 160:
                    add(f"💡 HEVC hardware a {bitrate} Mbps: oltre ~130 Mbps la resa non sale in modo percepibile "
                        f"(misurato: +1,4 VMAF da 129 a 190) e si riempie solo il disco.", "warn",
                        lambda: self.bitrate.set(DEFAULT_BITRATE["hw"]))
        # --- metadati e disco
        if not self.inject_meta.get():
            add("⚠ Metadati 360 disattivati: senza, il visore riproduce l'MP4 come video piatto.", "warn",
                lambda: self.inject_meta.set(True))
        vols = {}
        for f in files:
            dst = os.path.dirname(self.dest_for(f)) or "."
            vols.setdefault(dst, 0)
            e = self.estimate_output(f, (infos[f] or {}).get("duration"))
            vols[dst] += e or 0
        for folder, need in vols.items():
            free = free_space(folder) if os.path.isdir(folder) else None
            if free is not None and need and free < need * 1.05:
                add(f"⚠ Su {folder} liberi {human_gb(free)}, servono fino a ~{human_gb(need)}: scegli un altro disco.",
                    "bad")
        if self.same_folder.get() and files:
            add("💡 L'output va accanto al sorgente: leggere da un disco e scrivere su un altro è più veloce "
                "e non rischia di riempirlo.", "dim")
        if len(lines) == n_tips:
            add("✓ Nessun problema: i settaggi sono coerenti con la sorgente.", "ok")

        self._fixes = fixes
        self.btn_apply.config(state="normal" if fixes else "disabled")
        self.lbl_adv_note.config(text=f"{len(fixes)} correzion{'e' if len(fixes) == 1 else 'i'} "
                                      f"applicabil{'e' if len(fixes) == 1 else 'i'} in automatico" if fixes else "")
        self._adv_text(lines, tags)

    def apply_advice(self):
        for fix in list(self._fixes):
            fix()
        self._start_analysis()

    def _sync_res_widgets(self):
        is_custom = RESOLUTIONS.get(self.resolution.get()) == "custom"
        self.spn_w.config(state="normal" if is_custom else "disabled")

    def _sync_out_widgets(self):
        same = self.same_folder.get()
        self.ent_out.config(state="disabled" if same else "normal")
        self.btn_out.config(state="disabled" if same else "normal")
        self.refresh_free()

    def _sync_enc_widgets(self):
        enc = self.encoder.get()
        sw, av1 = enc == "sw", enc == "av1"
        self.cmb_preset.config(state="readonly" if sw or av1 else "disabled")
        self.spn_crf.config(state="normal" if sw or av1 else "disabled")
        # bitrate attivo per HEVC hw e H264; software e AV1 lavorano in CRF
        self.spn_br.config(state="disabled" if sw or av1 else "normal")
        # preset e CRF hanno scale diverse fra x265 e SVT-AV1: ogni famiglia
        # conserva i propri valori, così passare da un encoder all'altro non
        # lascia un CRF che nell'altra scala significa tutt'altra qualità.
        family = "av1" if av1 else "x265"
        if family != self._crf_family:
            self._crf_saved[self._crf_family] = (self.preset.get(), self.crf.get().strip())
            self.cmb_preset.config(values=AV1_PRESETS if av1 else X265_PRESETS)
            preset, crf = self._crf_saved[family]
            self.preset.set(preset)
            self.crf.set(crf)
            self.spn_crf.config(from_=18 if av1 else 10, to=40 if av1 else 28)
            self._crf_family = family
        # bitrate consigliato per encoder, solo se l'utente non l'ha "personalizzato"
        cur = self.bitrate.get().strip()
        if enc == "h264" and cur in ("", DEFAULT_BITRATE["hw"]):
            self.bitrate.set(DEFAULT_BITRATE["h264"])
        elif enc == "hw" and cur in ("", DEFAULT_BITRATE["h264"]):
            self.bitrate.set(DEFAULT_BITRATE["hw"])

    # ------------------------------------------------------------- deps
    def _check_deps(self):
        missing = [c for c in ("ffmpeg", "ffprobe") if not which(c)]
        if missing:
            self.log(f"[ATTENZIONE] Mancano: {', '.join(missing)}. "
                     f"Installa con: brew install ffmpeg\n")
        if not which("exiftool"):
            self.log("[NOTA] exiftool non trovato: l'iniezione metadati non funzionerà. "
                     "Installa con: brew install exiftool\n")

    # -------------------------------------------------------------- log
    def log(self, msg):
        self.log_q.put(msg)

    def _drain_log(self):
        try:
            while True:
                msg = self.log_q.get_nowait()
                self.txt.config(state="normal")
                if isinstance(msg, tuple):             # ("prog", riga): sostituisce la precedente
                    if self._prog_live:
                        self.txt.delete("end-2l linestart", "end-1c")
                    self.txt.insert("end", msg[1] + "\n")
                    self._prog_live = True
                else:
                    self.txt.insert("end", msg)
                    self._prog_live = False
                self.txt.see("end")
                self.txt.config(state="disabled")
        except queue.Empty:
            pass
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log)

    # ------------------------------------------------------- file mgmt
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="Seleziona file sorgente",
            filetypes=[("Video", "*.mov *.mp4 *.mxf *.m4v"), ("Tutti", "*.*")])
        for p in paths:
            if p not in self.lst_files.get(0, "end"):
                self.lst_files.insert("end", p)
        self.refresh_free()
        self.schedule_analysis()

    def remove_selected(self):
        for i in reversed(self.lst_files.curselection()):
            self.lst_files.delete(i)
        self.schedule_analysis()

    def clear_files(self):
        self.lst_files.delete(0, "end")
        self.schedule_analysis()

    def pick_out_dir(self):
        d = filedialog.askdirectory(title="Cartella di destinazione")
        if d:
            self.out_dir.set(d)
            self.refresh_free()

    # ----------------------------------------------------------- spazio
    def dest_for(self, src):
        """Percorso dell'MP4 di output per un dato sorgente."""
        base = os.path.splitext(os.path.basename(src))[0] + "_quest.mp4"
        if self.same_folder.get() or not self.out_dir.get().strip():
            return os.path.join(os.path.dirname(src), base)
        return os.path.join(self.out_dir.get().strip(), base)

    def dest_root(self):
        """Cartella su cui misurare lo spazio libero (o None se dipende dal sorgente)."""
        if self.same_folder.get():
            files = self.lst_files.get(0, "end")
            return os.path.dirname(files[0]) if files else None
        return self.out_dir.get().strip() or None

    def refresh_free(self):
        root = self.dest_root()
        if not root or not os.path.isdir(root):
            self.lbl_free.config(text="Spazio libero: —", foreground="#444")
            return
        free = free_space(root)
        if free is None:
            self.lbl_free.config(text="Spazio libero: non rilevabile", foreground="#444")
            return
        color = "#b00" if free < 20e9 else ("#a60" if free < 60e9 else "#060")
        self.lbl_free.config(text=f"Spazio libero su {root}: {human_gb(free)}", foreground=color)

    def estimate_output(self, src, dur):
        """Stima prudenziale dei byte dell'MP4 di output."""
        if not dur:
            return None
        enc = self.encoder.get()
        if enc == "sw":
            # tetto VBV della fascia: caso peggiore in CRF
            mbps = X265_VBV_MAXRATE_MBPS[self._x265_tier(src)]
        elif enc == "av1":
            try:
                crf = float(self.crf.get().strip() or AV1_DEFAULT_CRF)
            except ValueError:
                crf = float(AV1_DEFAULT_CRF)
            mbps = AV1_MBPS_AT_CRF28 * (1.09 ** (28 - crf)) * 1.2   # +20% di margine
        else:
            try:
                mbps = float(self._bitrate())
            except ValueError:
                mbps = float(DEFAULT_BITRATE["hw"])
        mode = AUDIO_MODES.get(self.audio.get(), "stereo")
        mbps += {"stereo": 0.32, "multi": 0.52, "none": 0.0}[mode]
        return dur * mbps * 1e6 / 8 * 1.02     # +2% di overhead contenitore

    def check_space(self):
        """Stima l'occupazione del batch senza codificare nulla."""
        files = list(self.lst_files.get(0, "end"))
        if not files:
            messagebox.showinfo(APP_TITLE, "Aggiungi almeno un file.")
            return

        def _do():
            self.log("\n========== STIMA SPAZIO ==========\n")
            per_volume = {}
            for src in files:
                dur = ffprobe_duration(src)
                est = self.estimate_output(src, dur)
                dst = self.dest_for(src)
                folder = os.path.dirname(dst) or "."
                if est is None:
                    self.log(f"{os.path.basename(src)}: durata non leggibile, stima impossibile\n")
                    continue
                per_volume[folder] = per_volume.get(folder, 0) + est
                self.log(f"{os.path.basename(src)}: {dur/60:.1f} min → max ~{human_gb(est)}\n")
            self.log("\n")
            for folder, need in per_volume.items():
                free = free_space(folder)
                if free is None:
                    self.log(f"{folder}: spazio non rilevabile\n")
                    continue
                verdict = "OK" if free >= need * 1.05 else "INSUFFICIENTE"
                self.log(f"{folder}\n   serve ~{human_gb(need)} · liberi {human_gb(free)} → {verdict}\n")
            self.log("==================================\n")
            self.root.after(0, self.refresh_free)

        threading.Thread(target=_do, daemon=True).start()

    # -------------------------------------------------- color detection
    def detect_color(self):
        files = self.lst_files.get(0, "end")
        if not files:
            messagebox.showinfo(APP_TITLE, "Aggiungi prima un file sorgente.")
            return
        src = files[0]
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=color_primaries,color_transfer,color_space",
                 "-of", "default=noprint_wrappers=1", src],
                capture_output=True, text=True, timeout=30).stdout.lower()
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"ffprobe fallito: {e}")
            return
        self.log(f"[colore] {src}\n{out}\n")
        if "smpte2084" in out or "2084" in out:
            self.color.set("HDR PQ (BT.2020 / HDR10)")
            guess = "HDR PQ"
        elif "arib-std-b67" in out or "hlg" in out:
            self.color.set("HDR HLG (BT.2020)")
            guess = "HDR HLG"
        elif "bt2020" in out:
            self.color.set("HDR PQ (BT.2020 / HDR10)")
            guess = "BT.2020 (verifica PQ vs HLG)"
        else:
            self.color.set("SDR (BT.709)")
            guess = "SDR BT.709"
        messagebox.showinfo(APP_TITLE, f"Rilevato: {guess}\nImpostazione colore aggiornata.")

    # --------------------------------------------------- command build
    def _bitrate(self):
        return self.bitrate.get().strip() or DEFAULT_BITRATE.get(self.encoder.get(), DEFAULT_BITRATE["hw"])

    def _target_res(self):
        """(w, h) di destinazione, oppure None se nessun ridimensionamento."""
        res = RESOLUTIONS.get(self.resolution.get())
        if res is None:
            return None
        if res == "custom":
            try:
                w = int(self.custom_w.get())
            except ValueError:
                return None
        else:
            w = res[0]
        w -= w % 2          # larghezza pari
        h = w // STEREO_ASPECT.get(self.stereo.get(), 2)
        h -= h % 2          # altezza pari
        return (w, h)

    def _scale_plan(self, src):
        """((w, h) o None, nota o None). Blocca upscale e scaling inutili."""
        res = self._target_res()
        if not res:
            return None, None
        info = ffprobe_video(src)
        if not info:
            return res, None
        sw, sh, _ = info
        if res[0] > sw or res[1] > sh:
            return None, (f"target {res[0]}×{res[1]} più grande del sorgente {sw}×{sh}: "
                          f"niente upscale, mantengo l'originale")
        if res == (sw, sh):
            return None, f"target uguale al sorgente ({sw}×{sh}): nessuno scaling"
        return res, None

    def _x265_tier(self, src):
        """'high' per 7K60/8K50/8K60, 'std' per il resto (vedi X265_HIGH_THRESHOLD)."""
        info = ffprobe_video(src)
        if not info:
            return "std"
        sw, sh, fps = info
        w, h = self._scale_plan(src)[0] or (sw, sh)
        return "high" if w * h * fps > X265_HIGH_THRESHOLD else "std"

    def _keyint(self, src):
        """Keyframe ogni secondo: arrotonda il framerate del sorgente (60 se ignoto)."""
        info = ffprobe_video(src)
        fps = info[2] if info else 0
        return max(1, round(fps)) if 1 <= fps <= 240 else 60

    def _out_size(self, src):
        """(w, h) del video in uscita, o None se il sorgente non è leggibile."""
        res = self._scale_plan(src)[0]
        if res:
            return res
        info = ffprobe_video(src)
        return info[:2] if info else None

    def _h264_software(self, src):
        """True se l'H.264 va fatto con libx264 (VideoToolbox non regge la risoluzione)."""
        size = self._out_size(src)
        return bool(size) and max(size) > H264_VT_MAX_SIDE

    def build_cmd(self, src, dst):
        cmd = ["ffmpeg", "-y", "-i", src]

        # solo prima traccia video + prima traccia audio se esiste
        cmd += ["-map", "0:v:0", "-map", "0:a:0?"]

        # ridimensionamento opzionale (scala lanczos, mantiene il 2:1)
        res, _ = self._scale_plan(src)
        if res:
            w, h = res
            cmd += ["-vf", f"scale={w}:{h}:flags=lanczos"]

        enc = self.encoder.get()
        br = self._bitrate()
        if enc == "hw":
            cmd += ["-c:v", "hevc_videotoolbox", "-profile:v", "main10",
                    "-b:v", f"{br}M", "-pix_fmt", "p010le"]
            tag = "hvc1"
        elif enc == "h264":
            # old-style: 8-bit, GOP 1s, bufsize ridotto per limitare gli spike sulla Quest
            if self._h264_software(src):
                cmd += ["-c:v", "libx264", "-preset", "fast"]
            else:
                cmd += ["-c:v", "h264_videotoolbox"]
            cmd += ["-b:v", f"{br}M", "-maxrate", f"{br}M",
                    "-bufsize", f"{max(50, int(float(br) / 2))}M",
                    "-pix_fmt", "yuv420p", "-g", str(self._keyint(src))]
            tag = "avc1"
        elif enc == "av1":
            # SVT-AV1 accetta solo CRF sopra i 100 Mbps: -b:v 0 disattiva l'ABR.
            cmd += ["-c:v", "libsvtav1", "-preset", self.preset.get(),
                    "-crf", self.crf.get().strip() or AV1_DEFAULT_CRF, "-b:v", "0",
                    "-pix_fmt", "yuv420p10le",
                    "-svtav1-params", f"keyint={self._keyint(src)}"]
            tag = None
        else:
            cmd += ["-c:v", "libx265", "-preset", self.preset.get(),
                    "-crf", self.crf.get().strip() or "16",
                    "-pix_fmt", "yuv420p10le",
                    "-x265-params",
                    (X265_PARAMS_HIGH if self._x265_tier(src) == "high"
                     else X265_PARAMS_STD).format(kf=self._keyint(src))]
            tag = "hvc1"

        cmd += COLOR_TAGS[self.color.get()]
        if tag:                      # AV1 usa il suo tag (av01), scritto da ffmpeg
            cmd += ["-tag:v", tag]

        # audio
        mode = AUDIO_MODES.get(self.audio.get(), "stereo")
        if mode == "none":
            cmd += ["-an"]
        elif mode == "multi":
            cmd += ["-c:a", "aac", "-b:a", "512k"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "320k", "-ac", "2"]

        cmd += ["-movflags", "+faststart", dst]
        return cmd

    def exiftool_cmd(self, path):
        # XMP-GSpherical:StereoMode -> mono / top-bottom / left-right
        stereo_map = {
            "Mono (2D)": "mono",
            "Stereo Top-Bottom (TB)": "top-bottom",
            "Stereo Side-by-Side (SBS)": "left-right",
        }
        stereo = stereo_map.get(self.stereo.get(), "mono")
        cmd = list(EXIFTOOL_BASE)
        if self.overwrite_meta.get():
            cmd.append("-overwrite_original")
        # StitchingSoftware non è decorativo: senza, ffmpeg scarta il box
        # ("Invalid spherical metadata found") e non vede il 360.
        cmd += ['-XMP-GSpherical:Spherical=true',
                '-XMP-GSpherical:Stitched=true',
                f'-XMP-GSpherical:StitchingSoftware={APP_TITLE}',
                '-XMP-GSpherical:ProjectionType=equirectangular',
                f'-XMP-GSpherical:StereoMode={stereo}',
                path]
        return cmd

    def verify_cmd(self, path):
        return list(EXIFTOOL_BASE) + [
            "-XMP-GSpherical:all",
            "-CompressorID", "-VideoFrameRate", "-ImageSize",
            "-ColorPrimaries", "-TransferCharacteristics", "-MatrixCoefficients",
            "-AudioChannels", "-FileSize", "-Duration",
            path]

    # ----------------------------------------------------------- run
    def preview_cmd(self):
        """Mostra i comandi che verrebbero eseguiti, senza avviarli."""
        files = list(self.lst_files.get(0, "end"))
        if not files:
            messagebox.showinfo(APP_TITLE, "Aggiungi almeno un file per vedere il comando.")
            return
        self.log("\n========== ANTEPRIMA COMANDI (non eseguiti) ==========\n")
        for src in files:
            dst = self.dest_for(src)
            self.log(f"\n# {os.path.basename(src)}\n")
            self._log_plan(src)
            self.log(cmd_to_str(self.build_cmd(src, dst)) + "\n")
            if self.inject_meta.get():
                self.log(cmd_to_str(self.exiftool_cmd(dst)) + "\n")
        self.log("======================================================\n")

    def _log_plan(self, src):
        note = self._scale_plan(src)[1]
        if note:
            self.log(f"[risoluzione] {note}\n")
        if self.encoder.get() == "av1":
            self.log("[av1] SVT-AV1 segnala l'8K come sperimentale: verifica il file nel visore "
                     "prima di usarlo in produzione.\n")
        if self.encoder.get() == "h264" and self._h264_software(src):
            w, h = self._out_size(src)
            self.log(f"[h264] {w}×{h} supera i {H264_VT_MAX_SIDE} px di h264_videotoolbox: uso libx264 "
                     f"(lento). Molti player H.264 non decodificano oltre il 4K.\n")
        if self.encoder.get() == "sw":
            tier = self._x265_tier(src)
            self.log("[x265] fascia " + ("alta (VBV 160 Mbps)" if tier == "high"
                                         else "standard (VBV 120 Mbps)") + "\n")

    def start(self):
        files = list(self.lst_files.get(0, "end"))
        if not files:
            messagebox.showinfo(APP_TITLE, "Aggiungi almeno un file.")
            return
        if not which("ffmpeg"):
            messagebox.showerror(APP_TITLE, "ffmpeg non trovato. brew install ffmpeg")
            return
        if not self.same_folder.get():
            d = self.out_dir.get().strip()
            if not d or not os.path.isdir(d):
                messagebox.showerror(APP_TITLE, "Cartella di destinazione non valida.")
                return
        self.cancel_flag.clear()
        self.btn_run.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self.worker = threading.Thread(target=self._run_batch, args=(files,), daemon=True)
        self.worker.start()

    def _run_batch(self, files):
        done, failed, skipped = 0, 0, 0
        for idx, src in enumerate(files, 1):
            if self.cancel_flag.is_set():
                break
            dst = self.dest_for(src)
            self.log(f"\n=== [{idx}/{len(files)}] {os.path.basename(src)} ===\n")
            self._set_status(f"Encoding {idx}/{len(files)}")

            if not self._space_ok(src, dst):
                skipped += 1
                continue

            later = sum(self.job_pixels(f) or 0 for f in files[idx:])
            ok = self._encode(src, dst, later)
            if not ok:
                failed += 1
                continue
            if self.inject_meta.get() and not self.cancel_flag.is_set():
                self._inject(dst)
            done += 1

        self._set_status("Pronto")
        self._reset_buttons()
        self.root.after(0, self.refresh_free)
        self.log(f"\n--- Fine: {done} completati, {failed} falliti, {skipped} saltati ---\n")

    def _space_ok(self, src, dst):
        """Controllo preventivo dello spazio sulla destinazione."""
        folder = os.path.dirname(os.path.abspath(dst)) or "."
        if not os.path.isdir(folder):
            self.log(f"[ERRORE] cartella di destinazione inesistente: {folder}\n")
            return False
        free = free_space(folder)
        dur = ffprobe_duration(src)
        est = self.estimate_output(src, dur)
        if free is None or est is None:
            self.log("[spazio] impossibile stimare, procedo comunque.\n")
            return True
        self.log(f"[spazio] stima output max ~{human_gb(est)} · liberi {human_gb(free)}\n")
        if free >= est * 1.05:
            return True
        if self.ignore_space.get():
            self.log("[spazio] soglia superata ma il controllo è disattivato: procedo.\n")
            return True
        self.log(f"[SALTATO] servono ~{human_gb(est * 1.05)} su {folder}, "
                 f"disponibili {human_gb(free)}.\n"
                 f"          Libera spazio o scegli un'altra cartella di destinazione.\n")
        return False

    def _encode(self, src, dst, later_px=0):
        dur = ffprobe_duration(src)
        px_total = self.job_pixels(src)
        enc, preset = self.encoder.get(), self.preset.get()
        t0 = time.time()
        self._log_plan(src)
        cmd = self.build_cmd(src, dst)
        self.log("\n┌─ COMANDO FFMPEG ─────────────────────────────────────────\n")
        self.log(cmd_to_str(cmd) + "\n")
        self.log("└──────────────────────────────────────────────────────────\n")
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1)
        except Exception as e:
            self.log(f"[ERRORE] {e}\n")
            return False

        time_re = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        speed_re = re.compile(r"speed=\s*([\d.]+)x")
        disk_full = False
        aborted = False
        for line in self.proc.stdout:
            if "No space left on device" in line:
                disk_full = True
            if self.cancel_flag.is_set():
                self.proc.terminate()
                self.log("\n[annullato]\n")
                aborted = True
                break
            if self.show_raw.get() and ("frame=" in line or "size=" in line) and "time=" in line:
                self.log_q.put(("prog", line.strip()))
            m = time_re.search(line)
            if m and dur:
                h, mn, s = m.groups()
                t = int(h) * 3600 + int(mn) * 60 + float(s)
                self._set_progress(min(100, t / dur * 100))
                status = f"{t/dur*100:4.0f}%"
                sm = speed_re.search(line)
                if sm and float(sm.group(1)) > 0 and px_total:
                    x = float(sm.group(1))
                    left = (dur - t) / x                       # secondi di questo file
                    if later_px and t > 20:                    # i file dopo, alla velocità osservata
                        left += later_px / (px_total / dur * x)
                    status += f" · ancora {fmt_dur(left)}"
                self._set_status(status)
            elif "frame=" in line or "fps=" in line:
                pass  # rumore di progress, non lo stampo riga per riga
            else:
                self.log(line)
        self.proc.wait()
        rc = self.proc.returncode
        self.proc = None
        self._set_progress(100)

        if not aborted and rc == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
            took = time.time() - t0
            if px_total and took > 60:                       # sotto il minuto la misura non è affidabile
                save_speed(f"{enc}:{preset}" if enc in ("av1", "sw") else enc, px_total / took / 1e6)
                self.log(f"[tempo] {fmt_dur(took)} → velocità registrata per le prossime stime\n")
            self.log(f"[OK] {dst}  ({human_gb(os.path.getsize(dst))})\n")
            return True

        if disk_full:
            self.log("[ERRORE] Disco pieno durante la scrittura. "
                     "Il controllo preventivo aveva stimato meno di quanto serve, "
                     "oppure altro ha occupato spazio nel frattempo.\n")
        self.log(f"[FFmpeg uscito con codice {rc}]\n")
        self._cleanup_partial(dst)
        return False

    def _cleanup_partial(self, dst):
        """Rimuove l'MP4 troncato lasciato da un encode fallito: è inutilizzabile
        e su un 360 può occupare parecchi GB."""
        try:
            if os.path.exists(dst):
                size = os.path.getsize(dst)
                os.remove(dst)
                self.log(f"[pulizia] rimosso output incompleto ({human_gb(size)}): "
                         f"{os.path.basename(dst)}\n")
        except Exception as e:
            self.log(f"[pulizia] non sono riuscito a rimuovere {dst}: {e}\n")

    # ------------------------------------------------------- metadati
    def _inject(self, path):
        """Iniezione metadati 360. ExifTool riscrive l'intero file: niente timeout,
        controllo preventivo dello spazio libero, LargeFileSupport sempre attivo."""
        if not which("exiftool"):
            self.log("[metadati] exiftool mancante, salto l'iniezione.\n")
            return False
        if not os.path.exists(path):
            self.log(f"[metadati] file non trovato: {path}\n")
            return False

        size = os.path.getsize(path)
        folder = os.path.dirname(os.path.abspath(path)) or "."
        free = free_space(folder)
        # exiftool scrive un temporaneo delle stesse dimensioni; senza backup
        # serve ~1x il file, con backup ~2x.
        needed = size * (1.1 if self.overwrite_meta.get() else 2.1)
        if free is not None and free < needed:
            self.log(f"[ERRORE metadati] spazio insufficiente su {folder}: "
                     f"servono ~{human_gb(needed)}, disponibili {human_gb(free)}.\n"
                     f"           Libera spazio, poi usa 'Solo metadati su MP4 esistente…'.\n")
            return False

        cmd = self.exiftool_cmd(path)
        self.log("\n┌─ COMANDO EXIFTOOL ───────────────────────────────────────\n")
        self.log(cmd_to_str(cmd) + "\n")
        self.log("└──────────────────────────────────────────────────────────\n")
        self.log(f"[metadati] riscrittura di {human_gb(size)} — può richiedere diversi minuti, "
                 f"non chiudere la finestra.\n")
        self._set_status("Metadati…")
        self._set_indeterminate(True)

        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in self.proc.stdout:
                if self.cancel_flag.is_set():
                    self.proc.terminate()
                    self.log("\n[metadati annullati — controlla che non resti un file "
                             "*_exiftool_tmp accanto all'originale]\n")
                    return False
                self.log(line)
            self.proc.wait()
            rc = self.proc.returncode
        except Exception as e:
            self.log(f"[ERRORE metadati] {e}\n")
            return False
        finally:
            self.proc = None
            self._set_indeterminate(False)
            self._set_status("Pronto")

        if rc == 0:
            self.log("[metadati OK]\n")
            return True
        self.log(f"[ERRORE metadati] exiftool uscito con codice {rc}. "
                 f"Se il disco si è riempito, cerca e cancella *_exiftool_tmp.\n")
        return False

    # --------------------------------------------------- meta-only ops
    def meta_only(self):
        path = filedialog.askopenfilename(
            title="MP4 su cui iniettare i metadati 360",
            filetypes=[("MP4", "*.mp4 *.mov"), ("Tutti", "*.*")])
        if not path:
            return
        if not which("exiftool"):
            messagebox.showerror(APP_TITLE, "exiftool non trovato. brew install exiftool")
            return
        self.cancel_flag.clear()
        self.btn_run.config(state="disabled")
        self.btn_cancel.config(state="normal")

        def _do():
            self._inject(path)
            self._reset_buttons()
            self.root.after(0, self.refresh_free)

        threading.Thread(target=_do, daemon=True).start()

    def verify_meta(self):
        path = filedialog.askopenfilename(
            title="File da verificare",
            filetypes=[("Video", "*.mp4 *.mov"), ("Tutti", "*.*")])
        if not path:
            return
        if not which("exiftool"):
            messagebox.showerror(APP_TITLE, "exiftool non trovato. brew install exiftool")
            return

        def _do():
            try:
                out = subprocess.run(self.verify_cmd(path),
                                     capture_output=True, text=True, timeout=180)
                blob = (out.stdout + out.stderr).strip()
            except Exception as e:
                self.log(f"[verifica] errore: {e}\n")
                return
            self.log(f"\n[verifica] {os.path.basename(path)}\n")
            self.log((blob if blob else "Nessun metadato trovato.") + "\n")
            if "Spherical" not in blob:
                self.log("→ ATTENZIONE: nessun tag Spherical. Il file verrà visto come video piatto. "
                         "Usa 'Solo metadati su MP4 esistente…'.\n")
            if "av01" in blob:
                self.log("→ AV1: la Quest 3 lo decodifica in hardware, ma verifica che il player lo apra "
                         "(DeoVR sì; su altri player va provato).\n")
            elif "hev1" in blob:
                self.log("→ ATTENZIONE: codec tag 'hev1'. Molti player Quest non lo leggono: "
                         "va ricodificato con -tag:v hvc1.\n")
            elif not any(t in blob for t in ("hvc1", "avc1", "av01")):
                self.log("→ NOTA: codec tag inatteso. Per la Quest l'HEVC deve essere 'hvc1', non 'hev1'.\n")

        threading.Thread(target=_do, daemon=True).start()

    # ------------------------------------------------------- helpers
    def cancel(self):
        self.cancel_flag.set()
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self._set_status("Annullamento…")

    def _set_progress(self, v):
        self.root.after(0, lambda: self.progress.config(value=v))

    def _set_indeterminate(self, on):
        def _apply():
            if on:
                self.progress.config(mode="indeterminate")
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.config(mode="determinate", value=0)
        self.root.after(0, _apply)

    def _set_status(self, s):
        self.root.after(0, lambda: self.lbl_status.config(text=s))

    def _reset_buttons(self):
        self.root.after(0, lambda: (self.btn_run.config(state="normal"),
                                    self.btn_cancel.config(state="disabled"),
                                    self.progress.config(value=0)))


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    EncoderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()