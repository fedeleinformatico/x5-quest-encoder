# Export video 360° (Insta360 X5 / Pro 2) → Meta Quest 3 / 3S

Riferimento operativo per esportare video equirettangolari mono verso Quest, con
montaggio in Premiere e codifica finale via FFmpeg su Mac (Apple Silicon M4).
Sorgenti coperte: **X5** (5.7K60) e **Insta360 Pro 2** (8K60), entrambe mono.

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

### Step 0 — Le sorgenti: stitching PRIMA di tutto

I file grezzi delle camere **non** vanno dati a FFmpeg/alla GUI: prima serve lo
**stitching**, che fa il plugin Insta360 (in Premiere o in Insta360 Studio).

- **Insta360 X5** → file `.insv`: contiene **2 fisheye HEVC 2880×2880** (lenti ant./post.),
  NON è ancora un 360. Il plugin cuce e riproietta in equirettangolare 5760×2880.
  Darlo a FFmpeg produrrebbe un singolo fisheye deformato (FFmpeg non ha la
  calibrazione ottica Insta360).
- **Insta360 Pro 2** → file `.ins`: è un **manifest di progetto da pochi KB** (4 KB),
  non il video. Punta ai file originali delle 6 lenti + parametri di stitch. Il plugin
  Insta360 Pro lo legge e presenta in timeline l'**8K equirettangolare 7680×3840 mono**,
  già cucito. Anche qui: non si dà l'`.ins` a FFmpeg.

In entrambi i casi l'input della catena è **sempre il ProRes/MP4 già stitchato**,
mai il file grezzo della camera.


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

### Risoluzione e framerate: cosa regge la Quest 3

La scelta della risoluzione si fa **in export da Premiere** (il ProRes determina la
risoluzione finale; FFmpeg/GUI non ridimensionano). Regola del decoder Quest 3
(XR2 Gen 2): l'HEVC arriva sulla carta all'8K, ma **8K30 è il tetto pratico affidabile**.

| Formato        | Nitidezza nel FOV | Fluidità | Decodifica Quest 3        | Quando                          |
|----------------|-------------------|----------|---------------------------|---------------------------------|
| 5.7K 60fps     | ~1600 px          | ottima   | sicura                    | movimento, presenza "live" (X5) |
| 8K 30fps       | ~2130 px          | media    | sicura (tetto pratico)    | scene lente/contemplative       |
| 8K 60fps       | ~2130 px          | ottima   | **a rischio stutter**     | solo da testare nel visore      |

- **8K60**: da provare, non dare per scontato. Doppio limite: (1) l'encoder hardware
  potrebbe non agganciarsi a 7680 px (test con `-t 15`); (2) anche se l'export riesce,
  la decodifica a 8K60 può scattare nel visore proprio nei momenti di movimento.
  Bitrate: **non 100M, ma 120-150M** (o CRF 18), altrimenti blocking.
- Se 8K60 scatta → ripiego **8K30** (nitidezza quasi identica) o **5.7K60** (fluidità piena).
- **Verifica sempre nel headset con la testa in movimento**, non sul monitor del Mac
  (sul monitor sembra sempre perfetto).

### Perché il ProRes intermedio NON è uno spreco (100 → ~1000 → 100 Mbps)

Sembra un giro a vuoto, ma i tre "100 Mbps" non sono lo stesso contenuto:
- l'originale è **grezzo/non montato** (X5: 2 fisheye; Pro 2: manifest+lenti);
- il file finale è **stitchato + montato**.
La trasformazione pesante (stitch, riproiezione, montaggio) deve materializzarsi da
qualche parte prima di FFmpeg → è il ProRes. Serve alto e quasi-lossless per **evitare
la doppia compressione**: se Premiere esportasse HEVC e poi FFmpeg ricomprimesse HEVC,
il secondo encoder lavorerebbe sugli artefatti del primo (il 360 "si spappola").
Il ProRes rompe la catena: **una sola** compressione lossy seria, quella finale con i
parametri buoni. Il bitrate da solo non misura la qualità (100M di ProRes sono scarsi,
100M di HEVC tunato sono ottimi). Costo reale: solo spazio disco temporaneo (~1 GB/clip),
che si cancella dopo.

> Nota: la "pipe" Premiere→FFmpeg senza file intermedio **non è applicabile**: Premiere
> è una GUI, esporta solo su file. La pipe serve a concatenare tool da riga di comando,
> non a saltare un export da un'app grafica. Vale su Mac e Windows uguale.

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

1. **Stitch nel plugin Insta360** (X5 `.insv` o Pro 2 `.ins`) → mai il grezzo a FFmpeg.
2. **Premiere → ProRes 422** (standard); la risoluzione di export decide 5.7K/8K.
3. **Verifica colore** sulla sorgente (`grep` bt709 vs bt2020) e imposta i tag giusti.
4. **FFmpeg → `hevc_videotoolbox` `-b:v 100M` (`120-150M` per 8K) `-profile:v main10` `-tag:v hvc1`**;
   oppure `libx265 -preset fast` per resa migliore.
5. **Check/inietta metadati 360** (`grep spherical`, `exiftool` con StereoMode; Mono per X5 e Pro 2).
6. **Nel visore**: DeoVR/Pigasus, quality al max, 90Hz, file locale. 8K60 → testa in movimento.
7. HEVC > H264 sulla Quest (decodifica più sicura, file più leggero).
8. Per automatizzare tutto: **`x5_quest_encoder.py`**.
