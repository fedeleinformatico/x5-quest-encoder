#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
X5 → Quest Encoder
GUI per codificare video 360 equirettangolari (Insta360 X5) verso Meta Quest 3/3S
e iniettare i metadati spaziali. Pensata per macOS / Apple Silicon.

Dipendenze esterne (riga di comando):
  - ffmpeg / ffprobe  (brew install ffmpeg)
  - exiftool          (brew install exiftool)   -> solo per i metadati 360

Avvio:  python3 x5_quest_encoder.py
"""

import os
import re
import shlex
import shutil
import subprocess
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "X5 → Quest Encoder"

# Parametri x265 "tuned" definiti per il 360 mono 5.7K60.
X265_PARAMS = (
    "keyint=60:min-keyint=60:bframes=3:aq-mode=3:"
    "psy-rd=2.0:psy-rdoq=1.0:sao=0:rc-lookahead=40:"
    "vbv-maxrate=120000:vbv-bufsize=240000"
)

# Risoluzioni output (equirettangolari 2:1). None = mantieni originale.
RESOLUTIONS = {
    "Originale (nessun ridimensionamento)": None,
    "8K — 7680×3840 (nitidezza max, 8K60 a rischio stutter)": (7680, 3840),
    "7K — 6656×3328 (quasi 8K, vicino ai limiti Quest)": (6656, 3328),
    "6K — 6144×3072 (intermedio, buon compromesso 60fps)": (6144, 3072),
    "5.7K — 5760×2880 (fluido a 60fps, sicuro sulla Quest)": (5760, 2880),
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


def which(cmd):
    return shutil.which(cmd)


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


class EncoderApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("820x1100")
        root.minsize(760, 940)

        self.proc = None
        self.worker = None
        self.log_q = queue.Queue()
        self.cancel_flag = threading.Event()

        self._build_ui()
        self._check_deps()
        self.root.after(100, self._drain_log)

    # ---------------------------------------------------------------- UI
    def _hint(self, parent, text, **grid):
        """Etichetta-guida grigia sotto un controllo."""
        lbl = ttk.Label(parent, text=text, foreground="#7a7a7a",
                        font=("", 10), wraplength=760, justify="left")
        if grid:
            lbl.grid(**grid)
        else:
            lbl.pack(anchor="w", padx=8, pady=(0, 4))
        return lbl

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # --- File input ---
        frm_in = ttk.LabelFrame(self.root, text="File sorgente (ProRes / MOV)")
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
                           "L'output esce nella stessa cartella con suffisso _quest.mp4. Più file = batch.")

        # --- Encoder ---
        frm_enc = ttk.LabelFrame(self.root, text="Encoder")
        frm_enc.pack(fill="x", **pad)

        self.encoder = tk.StringVar(value="hw")
        ttk.Radiobutton(frm_enc, text="Hardware HEVC — hevc_videotoolbox (veloce, ~4 min/clip) ★ consigliato",
                        variable=self.encoder, value="hw",
                        command=self._sync_enc_widgets).grid(row=0, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        ttk.Radiobutton(frm_enc, text="Software HEVC — libx265 (più bello, più lento)",
                        variable=self.encoder, value="sw",
                        command=self._sync_enc_widgets).grid(row=1, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        ttk.Radiobutton(frm_enc, text="H.264 old-style — h264_videotoolbox (massima compatibilità)",
                        variable=self.encoder, value="h264",
                        command=self._sync_enc_widgets).grid(row=2, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        self._hint(frm_enc, "→ HEVC HW = default: veloce, leggero, decodifica sicura sulla Quest 3. Software = resa migliore "
                            "su fogliame/cieli ma 15-40 min/clip. H.264 = solo per compatibilità con player/dispositivi "
                            "vecchi — vedi i limiti nel riquadro H.264 più sotto.",
                   row=3, column=0, columnspan=4, sticky="w", padx=6)

        # preset (solo software)
        ttk.Label(frm_enc, text="Preset x265:").grid(row=4, column=0, sticky="e", padx=6)
        self.preset = tk.StringVar(value="fast")
        self.cmb_preset = ttk.Combobox(frm_enc, textvariable=self.preset, width=10, state="readonly",
                                       values=["ultrafast", "fast", "medium", "slow", "slower"])
        self.cmb_preset.grid(row=4, column=1, sticky="w", padx=6, pady=2)

        ttk.Label(frm_enc, text="CRF (sw):").grid(row=4, column=2, sticky="e", padx=6)
        self.crf = tk.StringVar(value="16")
        self.spn_crf = ttk.Spinbox(frm_enc, from_=10, to=28, textvariable=self.crf, width=6)
        self.spn_crf.grid(row=4, column=3, sticky="w", padx=6, pady=2)
        self._hint(frm_enc, "→ Preset: 'fast' ottimo compromesso, 'medium' un filo meglio, 'slower' inutile per il 360 "
                            "(ore di attesa). CRF: 16 = altissima qualità; più basso (14) = più pesante, più alto (18-20) = più leggero.",
                   row=5, column=0, columnspan=4, sticky="w", padx=6)

        # bitrate (hardware HEVC e H264)
        ttk.Label(frm_enc, text="Bitrate (Mbps):").grid(row=6, column=0, sticky="e", padx=6)
        self.bitrate = tk.StringVar(value="100")
        self.spn_br = ttk.Spinbox(frm_enc, from_=40, to=250, textvariable=self.bitrate, width=6)
        self.spn_br.grid(row=6, column=1, sticky="w", padx=6, pady=2)
        self._hint(frm_enc, "→ Per HEVC HW: 100 Mbps su paesaggi (120-140 se vedi blocchi su acqua/foglie). "
                            "Per H.264 old-style: 200 Mbps è il valore classico Quest. Cambiando encoder il valore "
                            "consigliato si imposta da solo.",
                   row=7, column=0, columnspan=4, sticky="w", padx=6)

        # --- Riquadro limiti H.264 ---
        frm_h264 = ttk.LabelFrame(self.root, text="ℹ︎ H.264 old-style — limiti da sapere")
        frm_h264.pack(fill="x", **pad)
        self._hint(frm_h264,
                   "• La Quest 3 decodifica HEVC in hardware fino all'8K, ma per l'H.264 il limite hardware è più basso: "
                   "il 5.7K60 è al confine e può ricadere in decodifica software → stutter/frame drop nel visore.\n"
                   "• 200 Mbps è uno spike alto: il buffer della Quest è limitato e i picchi fanno scattare il 360 più del "
                   "bitrate medio. Qui si usa bufsize ridotto per attenuarlo, ma il file resta pesante.\n"
                   "• Solo 8-bit SDR: l'H.264 old-style esce a yuv420p. Se la sorgente è HDR, va consegnata in HEVC.\n"
                   "• Quando usarlo: solo per riprodurre su player datati o dispositivi che NON supportano HEVC. "
                   "Per la Quest 3/3S, l'HEVC è sempre la scelta migliore (più leggero E più fluido).")

        # --- Colore ---
        frm_col = ttk.LabelFrame(self.root, text="Spazio colore (deve combaciare con la sorgente!)")
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
        frm_res = ttk.LabelFrame(self.root, text="Risoluzione output (ridimensionamento)")
        frm_res.pack(fill="x", **pad)
        row_res = ttk.Frame(frm_res)
        row_res.pack(fill="x")
        self.resolution = tk.StringVar(value="Originale (nessun ridimensionamento)")
        ttk.Combobox(row_res, textvariable=self.resolution, state="readonly",
                     values=list(RESOLUTIONS.keys()), width=48,
                     ).pack(side="left", padx=6, pady=6)
        self.resolution.trace_add("write", lambda *a: self._sync_res_widgets())
        ttk.Label(row_res, text="Larghezza:").pack(side="left", padx=(12, 2))
        self.custom_w = tk.StringVar(value="6144")
        self.spn_w = ttk.Spinbox(row_res, from_=1024, to=8192, increment=128,
                                 textvariable=self.custom_w, width=7, state="disabled")
        self.spn_w.pack(side="left")
        ttk.Label(row_res, text="× metà (2:1 automatico)").pack(side="left", padx=(2, 6))
        self._hint(frm_res, "→ 'Originale' mantiene la risoluzione del ProRes. Usa 5.7K/6K se l'8K60 scatta nelle curve "
                            "sulla Quest (il decoder scala col numero di pixel: 5.7K≈56%, 6K≈64%, 7K≈75% dell'8K). "
                            "'Personalizzata' = scrivi la larghezza, l'altezza è sempre la metà (equirettangolare 2:1). "
                            "Scala lanczos, senza riesportare da Premiere. Puoi solo scendere, non inventare dettaglio.")

        # --- Metadati 360 ---
        frm_meta = ttk.LabelFrame(self.root, text="Metadati 360")
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
        self._hint(frm_meta, "→ Mono per la X5 standard (un solo punto di vista). Top-Bottom / Side-by-Side solo se hai "
                            "girato/montato in 3D stereoscopico (occhio sx e dx affiancati o sovrapposti nel frame). "
                            "Sbagliare qui fa vedere doppio o piatto nel visore.",
                   row=2, column=0, columnspan=4, sticky="w", padx=6)

        self.overwrite_meta = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_meta, text="Sovrascrivi senza creare backup _original",
                        variable=self.overwrite_meta).grid(row=3, column=0, columnspan=4, sticky="w", padx=6, pady=2)
        self._hint(frm_meta, "→ Spuntato: niente file di backup (più ordine). Tolto: exiftool tiene una copia "
                            "_original di sicurezza accanto al file.",
                   row=4, column=0, columnspan=4, sticky="w", padx=6)

        ttk.Button(frm_meta, text="Solo metadati su MP4 esistente…",
                   command=self.meta_only).grid(row=5, column=0, sticky="w", padx=6, pady=4)
        ttk.Button(frm_meta, text="Verifica metadati di un file…",
                   command=self.verify_meta).grid(row=5, column=1, sticky="w", padx=6, pady=4)
        self._hint(frm_meta, "→ 'Solo metadati' inietta su un MP4 già pronto senza ricodificare (usa la Modalità 3D qui sopra). "
                            "'Verifica' mostra cosa contiene già un file: cerca 'Spherical' e i tag colore.",
                   row=6, column=0, columnspan=4, sticky="w", padx=6)

        # --- Azioni ---
        frm_act = ttk.Frame(self.root)
        frm_act.pack(fill="x", **pad)
        self.btn_run = ttk.Button(frm_act, text="▶  Avvia", command=self.start)
        self.btn_run.pack(side="left", padx=6)
        self.btn_preview = ttk.Button(frm_act, text="⌗  Mostra comando", command=self.preview_cmd)
        self.btn_preview.pack(side="left", padx=6)
        self.btn_cancel = ttk.Button(frm_act, text="■  Annulla", command=self.cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(frm_act, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True, padx=6)
        self.lbl_status = ttk.Label(frm_act, text="Pronto", width=18)
        self.lbl_status.pack(side="right", padx=6)

        # --- Log ---
        frm_log = ttk.LabelFrame(self.root, text="Log")
        frm_log.pack(fill="both", expand=True, **pad)
        self.txt = tk.Text(frm_log, height=10, wrap="word", state="disabled",
                           background="#111", foreground="#ddd", insertbackground="#ddd")
        self.txt.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb = ttk.Scrollbar(frm_log, command=self.txt.yview)
        sb.pack(side="right", fill="y", pady=6)
        self.txt.config(yscrollcommand=sb.set)

        self._sync_enc_widgets()

    def _sync_res_widgets(self):
        is_custom = RESOLUTIONS.get(self.resolution.get()) == "custom"
        self.spn_w.config(state="normal" if is_custom else "disabled")

    def _sync_enc_widgets(self):
        enc = self.encoder.get()
        sw = enc == "sw"
        self.cmb_preset.config(state="readonly" if sw else "disabled")
        self.spn_crf.config(state="normal" if sw else "disabled")
        # bitrate attivo per HEVC hw e H264, non per software (che usa CRF)
        self.spn_br.config(state="disabled" if sw else "normal")
        # bitrate consigliato per encoder, solo se l'utente non l'ha "personalizzato"
        cur = self.bitrate.get().strip()
        if enc == "h264" and cur in ("", "100"):
            self.bitrate.set("200")
        elif enc == "hw" and cur in ("", "200"):
            self.bitrate.set("100")

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
                self.txt.insert("end", msg)
                self.txt.see("end")
                self.txt.config(state="disabled")
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

    def remove_selected(self):
        for i in reversed(self.lst_files.curselection()):
            self.lst_files.delete(i)

    def clear_files(self):
        self.lst_files.delete(0, "end")

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
    def build_cmd(self, src, dst):
        cmd = ["ffmpeg", "-y", "-i", src]
        # ridimensionamento opzionale (scala lanczos, mantiene il 2:1)
        res = RESOLUTIONS.get(self.resolution.get())
        if res == "custom":
            try:
                w = int(self.custom_w.get())
                w -= w % 2          # larghezza pari
                h = w // 2
                h -= h % 2          # altezza pari
                res = (w, h)
            except ValueError:
                res = None
        if res and res != "custom":
            w, h = res
            cmd += ["-vf", f"scale={w}:{h}:flags=lanczos"]
        enc = self.encoder.get()
        br = self.bitrate.get().strip() or "100"
        if enc == "hw":
            cmd += ["-c:v", "hevc_videotoolbox", "-profile:v", "main10",
                    "-b:v", f"{br}M", "-pix_fmt", "p010le"]
            tag = "hvc1"
        elif enc == "h264":
            # old-style: 8-bit, GOP 1s, bufsize ridotto per limitare gli spike sulla Quest
            cmd += ["-c:v", "h264_videotoolbox",
                    "-b:v", f"{br}M", "-maxrate", f"{br}M",
                    "-bufsize", f"{max(50, int(float(br) / 2))}M",
                    "-pix_fmt", "yuv420p", "-g", "60"]
            tag = "avc1"
        else:
            cmd += ["-c:v", "libx265", "-preset", self.preset.get(),
                    "-crf", self.crf.get().strip() or "16",
                    "-pix_fmt", "yuv420p10le",
                    "-x265-params", X265_PARAMS]
            tag = "hvc1"
        cmd += COLOR_TAGS[self.color.get()]
        cmd += ["-tag:v", tag, "-c:a", "aac", "-b:a", "320k", dst]
        return cmd

    def exiftool_cmd(self, path):
        # XMP-GSpherical:StereoMode -> mono / top-bottom / left-right
        stereo_map = {
            "Mono (2D)": "mono",
            "Stereo Top-Bottom (TB)": "top-bottom",
            "Stereo Side-by-Side (SBS)": "left-right",
        }
        stereo = stereo_map.get(self.stereo.get(), "mono")
        cmd = ["exiftool"]
        if self.overwrite_meta.get():
            cmd.append("-overwrite_original")
        cmd += ['-XMP-GSpherical:Spherical=true',
                '-XMP-GSpherical:Stitched=true',
                '-XMP-GSpherical:ProjectionType=equirectangular',
                f'-XMP-GSpherical:StereoMode={stereo}',
                path]
        return cmd

    # ----------------------------------------------------------- run
    def preview_cmd(self):
        """Mostra i comandi che verrebbero eseguiti, senza avviarli."""
        files = list(self.lst_files.get(0, "end"))
        if not files:
            messagebox.showinfo(APP_TITLE, "Aggiungi almeno un file per vedere il comando.")
            return
        self.log("\n========== ANTEPRIMA COMANDI (non eseguiti) ==========\n")
        for src in files:
            base, _ = os.path.splitext(src)
            dst = base + "_quest.mp4"
            self.log(f"\n# {os.path.basename(src)}\n")
            self.log(cmd_to_str(self.build_cmd(src, dst)) + "\n")
            if self.inject_meta.get():
                self.log(cmd_to_str(self.exiftool_cmd(dst)) + "\n")
        self.log("======================================================\n")

    def start(self):
        files = list(self.lst_files.get(0, "end"))
        if not files:
            messagebox.showinfo(APP_TITLE, "Aggiungi almeno un file.")
            return
        if not which("ffmpeg"):
            messagebox.showerror(APP_TITLE, "ffmpeg non trovato. brew install ffmpeg")
            return
        self.cancel_flag.clear()
        self.btn_run.config(state="disabled")
        self.btn_cancel.config(state="normal")
        self.worker = threading.Thread(target=self._run_batch, args=(files,), daemon=True)
        self.worker.start()

    def _run_batch(self, files):
        for idx, src in enumerate(files, 1):
            if self.cancel_flag.is_set():
                break
            base, _ = os.path.splitext(src)
            dst = base + "_quest.mp4"
            self.log(f"\n=== [{idx}/{len(files)}] {os.path.basename(src)} ===\n")
            self._set_status(f"Encoding {idx}/{len(files)}")
            ok = self._encode(src, dst)
            if ok and self.inject_meta.get():
                self._inject(dst)
        self._set_status("Pronto")
        self._reset_buttons()
        self.log("\n--- Fine ---\n")

    def _encode(self, src, dst):
        dur = ffprobe_duration(src)
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
        for line in self.proc.stdout:
            if self.cancel_flag.is_set():
                self.proc.terminate()
                self.log("\n[annullato]\n")
                return False
            m = time_re.search(line)
            if m and dur:
                h, mn, s = m.groups()
                t = int(h) * 3600 + int(mn) * 60 + float(s)
                self._set_progress(min(100, t / dur * 100))
                self._set_status(f"{t/dur*100:4.0f}%")
            elif "frame=" in line or "fps=" in line:
                pass  # rumore di progress, non lo stampo riga per riga
            else:
                self.log(line)
        self.proc.wait()
        rc = self.proc.returncode
        self.proc = None
        self._set_progress(100)
        if rc == 0:
            self.log(f"[OK] {dst}\n")
            return True
        self.log(f"[FFmpeg uscito con codice {rc}]\n")
        return False

    def _inject(self, path):
        if not which("exiftool"):
            self.log("[metadati] exiftool mancante, salto l'iniezione.\n")
            return
        cmd = self.exiftool_cmd(path)
        self.log("\n┌─ COMANDO EXIFTOOL ───────────────────────────────────────\n")
        self.log(cmd_to_str(cmd) + "\n")
        self.log("└──────────────────────────────────────────────────────────\n")
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            self.log(out.stdout + out.stderr + "\n")
        except Exception as e:
            self.log(f"[ERRORE metadati] {e}\n")

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
        threading.Thread(target=self._inject, args=(path,), daemon=True).start()

    def verify_meta(self):
        path = filedialog.askopenfilename(
            title="File da verificare",
            filetypes=[("Video", "*.mp4 *.mov"), ("Tutti", "*.*")])
        if not path:
            return

        def _do():
            try:
                out = subprocess.run(["ffmpeg", "-i", path],
                                     capture_output=True, text=True, timeout=30)
                blob = out.stderr
            except Exception as e:
                self.log(f"[verifica] errore: {e}\n")
                return
            found = [l for l in blob.splitlines()
                     if re.search(r"spherical|projection|color|transfer|primaries", l, re.I)]
            self.log(f"\n[verifica] {os.path.basename(path)}\n")
            self.log(("\n".join(found) if found else "Nessun metadato 360 / colore trovato.") + "\n")

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
