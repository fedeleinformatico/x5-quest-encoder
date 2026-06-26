# Export video 360° da Insta360 X5 → Meta Quest 3 / 3S

Riferimento operativo per il flusso di esportazione di video equirettangolari
mono 5.7K 60fps verso visore Quest, con montaggio base in Premiere e codifica
finale via FFmpeg su Mac (Apple Silicon M4).

---

## 1. Il problema di partenza

- Export HEVC (H265) da Media Encoder che si **bloccava** sul 5.7K 60fps, sia a
  200 che a 60 Mbps. H264 200 Mbps usciva regolarmente.
- Causa su **Apple Silicon**: Media Encoder usa **VideoToolbox**, non NVENC/QuickSync.
  L'accelerazione hardware HEVC non si agganciava per via della **risoluzione**
  (5760 px di larghezza è al limite di ciò che l'encoder HEVC hardware accetta;
  l'H264 hardware ha limiti diversi, per questo passava).

> Su Mac **Voukoder non esiste** (è solo Windows). Il flusso giusto è
> ProRes → FFmpeg.

---

## 2. Il flusso definitivo (3 step)

```
Premiere (montaggio) → Export ProRes 422 → verifica colore → FFmpeg HEVC → metadati 360
```

### Step 1 — Premiere: export master ProRes 422

- **ProRes 422 standard** (non LT, non HQ).
  - La sorgente X5 è H265 8-bit ~200 Mbps: nessun ProRes aggiunge dettaglio oltre.
  - 422 standard (~1 Gbps a 5.7K60) preserva tutta l'informazione utile.
  - 422 HQ raddoppia lo spazio disco senza guadagno visibile su sorgente 8-bit.
  - 422 LT accettabile solo con vincolo serio di spazio (piccolo rischio sui gradienti).
- Su M4 esce con **accelerazione hardware** (media engine ProRes dedicato): veloce.
- Sequenza a **5760×2880 / 59.94 fps** prima di esportare (no riscalo accidentale).
- **Non** attivare "Use Maximum Render Quality" se export = risoluzione sequenza.

### Step 2 — FFmpeg: codifica HEVC con accelerazione hardware (M4)

**Stringa di produzione (veloce, ~0.5x realtime → ~4 min per clip da 2 min):**

```bash
ffmpeg -i input_prores.mov \
  -c:v hevc_videotoolbox -profile:v main10 -b:v 100M \
  -pix_fmt p010le \
  -tag:v hvc1 \
  -c:a aac -b:a 320k \
  output.mp4
```

- `hevc_videotoolbox` = media engine M4. **Si aggancia all'hardware** anche a 5760 px.
- `-tag:v hvc1` **obbligatorio**: senza, molti player su Quest non leggono lo stream.
- 100 Mbps è un buon punto. Se nel visore compare blocking su fogliame/acqua,
  salire a `-b:v 120M -maxrate 140M -bufsize 280M`.

### Step 3 — Verifica/iniezione metadati 360

VideoToolbox a volte perde i metadati spaziali. Verifica:

```bash
ffmpeg -i output.mp4 2>&1 | grep -i spherical
```

Se manca `Spherical Mapping: equirectangular`, reinietta:

```bash
exiftool -XMP-GSpherical:Spherical="true" \
  -XMP-GSpherical:Stitched="true" \
  -XMP-GSpherical:ProjectionType="equirectangular" \
  output.mp4
```

Fallback rapido: **DeoVR** riconosce il 360 anche dal nome file (`_360` per mono).

---

## 3. Come leggere l'avanzamento FFmpeg (per non confondersi)

Nella riga di progress, `fps=30` è la **velocità di codifica** (frame processati
al secondo), **non** il framerate del file finale. Il framerate reale è nello
stream di output: `5760x2880, 59.94 fps`. Il file esce sempre a 59.94.

| Encoder              | Velocità (fps) | Tempo per clip 2 min | Note                         |
|----------------------|----------------|----------------------|------------------------------|
| x265 `slower` (sw)   | ~0.6           | ~3,5 ore             | qualità inutile per il FOV   |
| x265 `medium` (sw)   | ~5-8x più veloce | ~30-40 min         | max qualità ragionevole      |
| `hevc_videotoolbox`  | ~30 (0.5x rt)  | ~4 min               | **scelta definitiva**        |

Trucco per testare impostazioni senza attendere: aggiungere **`-t 15`** subito
dopo `-i input.mov` per codificare solo 15 secondi.

---

## 4. Massimizzare la qualità percepita nel visore

### Limite fisico
Un equirettangolare mono 5.7K avvolge 360° in orizzontale; nel FOV (~90-100°)
la risoluzione reale è ~1600 px. Sarà sempre un po' morbido: l'obiettivo è non
perdere pixel lungo la catena e dare alla Quest un file che decodifica fluido.

### Lato visore (metà della qualità percepita si gioca qui)
- Usare **DeoVR** o **Pigasus**, non la galleria di sistema (riproduce a risoluzione ridotta).
- Alzare *sphere/texture resolution* o "quality" al massimo nel player.
- Refresh Quest a **90Hz** (a 120Hz alcuni player abbassano la risoluzione di rendering).
- Riprodurre da **file locale sideloadato**, non in streaming (lo streaming
  reintroduce compressione e cap di bitrate).

### Perché HEVC e non H264 a 200M
- La Quest 3 ha decoder hardware **HEVC fino all'8K**; per **H264 il limite è più
  basso** e il 5.7K60 è al confine → rischio decodifica software → stutter.
- Spike di bitrate alti (200M) fanno scattare il 360 più del bitrate medio
  (buffer Quest limitato). HEVC a 100M = più leggero e più sicuro da decodificare.
- H264 hardware ha senso solo come fallback se l'HEVC hardware non si aggancia.

### Variante 8K30 (alternativa)
Per scene lente/contemplative, l'**8K30** dell'X5 è percettivamente più nitido
del 5.7K60 (+33% di risoluzione lineare nel FOV). Il 5.7K60 resta giusto quando
c'è movimento o si vuole la sensazione di presenza/live.

---

## 5. Alternativa max qualità software (se non serve velocità)

Se per clip specifiche si vuole la massima efficienza/qualità e si accettano
~30-40 min a clip, x265 software con `preset medium` (NON `slower`):

```bash
ffmpeg -i input_prores.mov \
  -c:v libx265 -preset medium -crf 16 \
  -pix_fmt yuv420p10le \
  -x265-params "keyint=60:min-keyint=60:bframes=3:aq-mode=3:psy-rd=2.0:psy-rdoq=1.0:sao=0:rc-lookahead=60:vbv-maxrate=120000:vbv-bufsize=240000" \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
  -tag:v hvc1 \
  -c:a aac -b:a 320k \
  output_max.mp4
```

Parametri chiave e perché:
- `crf 16` + `vbv-maxrate=120000`: qualità altissima ma cap sui picchi (evita stutter).
- `sao=0`: disattiva il Sample Adaptive Offset → recupera dettaglio fine (fogliame, texture).
- `psy-rd=2.0` / `psy-rdoq=1.0`: preservano micro-contrasto e texture.
- `aq-mode=3`: distribuisce i bit verso zone scure/uniformi (cieli, gradienti → meno banding).
- `10-bit`: riduce il banding sui gradienti anche se la sorgente è 8-bit.
- `keyint=60`: keyframe ogni secondo → seek fluido, meno carico sul decoder.

Nota: x265 (software) è più efficiente per bit di VideoToolbox (hardware). A
parità di bitrate la qualità è migliore, ma i tempi non sono sostenibili su molte clip.
In pratica VideoToolbox è "buono ma non bello"; `libx265 -preset fast` è il
compromesso che torna utile quando la resa conta più della velocità.

---

## 6. Spazio colore — il punto più delicato (HDR vs SDR)

I tag colore **devono combaciare con la sorgente**, altrimenti nel visore i colori
escono slavati o sballati. La stringa è "valida" come comando anche se i tag sono
sbagliati: l'errore è silenzioso, si vede solo nel headset.

Verifica cosa c'è davvero nel ProRes:

```bash
ffmpeg -i input_prores.mov 2>&1 | grep -iE "bt2020|smpte2084|arib|bt709|color"
```

| Cosa leggi nella sorgente        | Significato        | Tag FFmpeg da usare                                            |
|----------------------------------|--------------------|---------------------------------------------------------------|
| `bt709`                          | SDR                | `-color_primaries bt709 -color_trc bt709 -colorspace bt709`   |
| `bt2020` + `smpte2084`           | HDR10 / PQ         | `-color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc` |
| `bt2020` + `arib-std-b67`        | HDR HLG            | `-color_primaries bt2020 -color_trc arib-std-b67 -colorspace bt2020nc` |

Note importanti:
- **i-Log / "flat" dell'X5 ≠ HDR**: è un profilo log SDR da sviluppare in grading;
  l'output finale è SDR BT.709.
- **HDR sulla Quest** è gestito ma dipende dal player (DeoVR sì, galleria di sistema
  meno bene) e va etichettato bene. Molti, per affidabilità tra player, consegnano
  **SDR BT.709** anche da girato HDR (tone-mapping in Premiere).
- L'**H.264 old-style è solo 8-bit SDR**: per HDR usare sempre HEVC.

---

## 7. H.264 old-style — opzione di compatibilità (con limiti)

Disponibile per riprodurre su player/dispositivi datati che **non** supportano HEVC.
Per la Quest 3/3S NON è la scelta migliore.

```bash
ffmpeg -i input.mov \
  -c:v h264_videotoolbox -b:v 200M -maxrate 200M -bufsize 100M \
  -pix_fmt yuv420p -g 60 \
  -tag:v avc1 \
  -c:a aac -b:a 320k \
  output_h264.mp4
```

Limiti da conoscere:
- La Quest 3 decodifica **HEVC fino all'8K**, ma per **H.264 il limite hardware è
  più basso**: il 5.7K60 è al confine → può ricadere in decodifica software → stutter.
- 200 Mbps è uno spike alto per il buffer limitato della Quest; `bufsize 100M` lo
  attenua, ma il file resta pesante.
- Solo 8-bit SDR (`yuv420p`), non adatto a sorgenti HDR.
- **Conclusione**: usare solo per compatibilità con hardware vecchio; per la Quest
  l'HEVC è sempre più leggero E più fluido.

---

## 8. Tool GUI — `x5_quest_encoder.py`

App Tkinter che automatizza tutto il flusso FFmpeg + metadati su macOS.

Avvio:

```bash
python3 x5_quest_encoder.py
```

Dipendenze (riga di comando): `ffmpeg` / `ffprobe` ed `exiftool`
(`brew install ffmpeg exiftool`). Nessuna dipendenza pip.

Cosa fa:
- **Batch** di file ProRes/MOV; output `<nome>_quest.mp4` nella stessa cartella.
- **Tre encoder**: HEVC hardware (★ default), HEVC software (x265 tuned), H.264 old-style.
- **Bitrate auto-suggerito**: 100 Mbps per HEVC HW, 200 per H.264 (modificabile).
- **Rileva colore dalla sorgente** (ffprobe) e imposta SDR / PQ / HLG da solo.
- **Metadati 360** via exiftool con modalità 3D: Mono / Top-Bottom / Side-by-Side
  (campo `XMP-GSpherical:StereoMode`). Mono per la X5 standard.
- **Solo metadati** su un MP4 esistente (senza ricodificare) e **Verifica metadati**.
- **Mostra comando**: anteprima dei comandi FFmpeg/exiftool (shell-quotati,
  copia-incollabili) senza eseguirli; gli stessi comandi compaiono nel log all'avvio.
- **Note-guida** accanto a ogni scelta + riquadro dedicato ai limiti H.264.
- Progress reale (legge `time=` vs durata), batch e pulsante Annulla.

Limite noto: la GUI **non fa tone-mapping HDR→SDR**. Imposta i tag colore giusti,
ma la conversione HDR→SDR va fatta in Premiere (o con un filtro `zscale/tonemap`
non incluso).

---

## TL;DR

1. **Premiere → ProRes 422** (standard).
2. **Verifica colore** sulla sorgente (`grep` bt709 vs bt2020) e imposta i tag giusti.
3. **FFmpeg → `hevc_videotoolbox` `-b:v 100M` `-profile:v main10` `-tag:v hvc1`** (~4 min/clip);
   oppure `libx265 -preset fast` se vuoi resa migliore.
4. **Check/inietta metadati 360** (`grep spherical`, `exiftool` con StereoMode).
5. **Nel visore**: DeoVR/Pigasus, quality al max, 90Hz, file locale.
6. HEVC > H264 a 5.7K60 sulla Quest (decodifica più sicura, file più leggero).
7. Per automatizzare tutto: **`x5_quest_encoder.py`**.
