# Export video 360° → Meta Quest 3 / 3S

Riferimento operativo unificato per una catena di produzione 360:
stitching → montaggio in Premiere → master ProRes → codifica FFmpeg → metadati
spaziali → riproduzione nel visore.

**Sorgenti coperte:** Insta360 **X6** (8K50 / 6K60), **X5** (5.7K60), **Pro 2** (8K60).
**Piattaforma di lavoro:** macOS Apple Silicon (M4).
**Destinazione:** Meta Quest 3 / 3S, file locale sideloadato, player DeoVR / Pigasus.

> Sostituisce i due documenti precedenti (workflow X5 e workflow X5 + Pro 2),
> consultabili nella storia git di questo file. La sezione 14 elenca cosa è
> cambiato e perché, per chi conosceva le versioni vecchie.

---

## 1. La regola che governa tutto: bit per pixel

Tutte le decisioni su risoluzione e framerate discendono da un unico numero:
**quanti bit spendi per ogni pixel effettivamente codificato.**

```
bit/pixel = bitrate ÷ (larghezza × altezza × fps)
```

Il punto è che risoluzione e framerate **consumano entrambi lo stesso budget**.
Raddoppiare il framerate dimezza i bit per pixel esattamente come raddoppiare i
pixel. E il budget non è elastico: è il tetto che la Quest riesce a ingerire.

La conseguenza pratica, contro-intuitiva:

> **Un 8K strozzato è peggio di un 6K pieno.** Se imposti CRF 16 con un cap a
> 120 Mbps su un 8K60, il CRF non viene mai raggiunto — il VBV strozza prima.
> Quello che ottieni non è "8K a CRF 16", è "8K a 120 Mbps forzati", cioè circa
> la metà dei bit per pixel di un 5.7K60. Sulle scene complesse — fogliame,
> acqua, folla — vedi blocchi proprio dove l'8K dovrebbe servire.

Tabella di riferimento (FOV = pixel reali dentro il campo visivo di ~100°,
che è ciò che l'occhio vede davvero; il resto della sfera è dietro la testa):

| Formato | px nel FOV | Mpx/s | bit/px @100 | @120 | @150 |
|---------|-----------:|------:|------------:|-----:|-----:|
| 4K 60 (3840×1920) | 1067 | 442 | 0,226 | 0,271 | 0,339 |
| 4K 100 | 1067 | 737 | 0,136 | 0,163 | 0,203 |
| 5.7K 60 (5760×2880) | 1600 | 995 | 0,100 | 0,121 | 0,151 |
| **6K 60 (6016×3008)** | **1671** | 1086 | 0,092 | **0,111** | 0,138 |
| 6K 50 | 1671 | 905 | 0,111 | 0,133 | 0,166 |
| 7K 60 (6656×3328) | 1849 | 1329 | 0,075 | 0,090 | 0,113 |
| **8K 30 (7680×3840)** | **2133** | 885 | 0,113 | **0,136** | 0,170 |
| 8K 50 | 2133 | 1475 | 0,068 | 0,081 | 0,102 |
| 8K 60 | 2133 | 1769 | 0,057 | 0,068 | 0,085 |

**Soglia pratica: sotto ~0,09 bit/pixel si entra nella zona in cui è il contenuto
a decidere** se il file regge o si sbriciola. Un panorama statico passa, una folla
in movimento no.

### I due punti ottimali

| | scelta | perché |
|---|---|---|
| **Movimento, presenza "live"** | **6K 60** | 0,111 bit/px, fluidità piena, decodifica sicura |
| **Nitidezza, scene contemplative** | **8K 30** | 0,136 bit/px — più nitido E meglio codificato del 6K60 |

L'**8K30 non è un ripiego: è la configurazione di massima qualità dell'intera
catena.** Stessa nitidezza nel FOV dell'8K60 (2133 px), ma con *più* bit per pixel
di quanti ne abbia un 5.7K60. L'unico motivo per non usarlo è il movimento.

La scelta non è mai "quanti pixel regge la Quest". È **60 fps o nitidezza — non entrambi.**

---

## 2. Cosa regge davvero la Quest 3

### Decodifica

Lo Snapdragon XR2 Gen 2 della Quest 3 arriva, secondo i test della comunità, a:

| fps | risoluzione massima |
|----:|---------------------|
| 30 | 8192 × 8192 |
| 60 | 8192 × 4096 |
| 90 | 6688 × 3344 |
| 120 | 5792 × 2896 |

**Il decoder non è il collo di bottiglia.** Anche l'8K60 rientra. Il limite è il
bitrate necessario a riempirlo, non i pixel.

> ⚠️ **Meta non pubblica specifiche ufficiali di decodifica.** Questa tabella viene
> da test di terze parti. Trattala come ordine di grandezza, non come contratto.

### Bitrate

Due riferimenti che vanno tenuti distinti:

- **Guide pubblicate** (explorations360, Bitmovin): 30-50 Mbps per il mono,
  50-80 per lo stereo, **~100 Mbps come massimo** consigliato. Sono pensate
  soprattutto per la distribuzione e lo streaming.
- **File locale sideloadato** (il caso di kiosk e installazioni): il bitrate non
  passa da rete né da buffer di streaming. **120-150 Mbps sono praticabili
  nella nostra esperienza** — non è un dato documentato, va confermato nel
  visore con la propria clip difficile (sezione 13).

**Non estendere le raccomandazioni di streaming a un file su disco locale.**

### Codec

- **HEVC**: la scelta di riferimento. Decodifica hardware fino all'8K.
- **AV1**: decodificato in hardware dalla Quest 3, **~30% di bitrate in meno a
  parità di qualità**. È la leva che sbloccherebbe davvero le risoluzioni alte
  (stima: a 100 Mbps un 7K AV1 dovrebbe stare sopra un 5.7K HEVC). Due prezzi:
  nessuna accelerazione VideoToolbox per l'encoding AV1 su Mac → tempi lunghi in
  CPU (`libsvtav1`); e va **verificato** che il player usato lo riproduca.
  Da valutare, non ancora in produzione.
- **H.264**: solo compatibilità con hardware datato. Vedi sezione 9.

---

## 3. Le sorgenti — modi nativi

### Insta360 X6 (attuale)

File `.insv`, H.265, fino a 240 Mbps in camera, sensori 1/1,1".

| modo | risoluzione | framerate disponibili |
|------|-------------|-----------------------|
| 8K | 7680 × 3840 | **50 / 48 / 30 / 25 / 24** |
| 6K | 6016 × 3008 | **60** / 50 / 48 / 30 / 25 / 24 |
| 4K | 3840 × 1920 | 100 / 60 / 50 / 48 / 30 / 25 / 24 |

Profili colore: **Standard, Dolby Vision, I-Log (10-bit)**.

Due cose da tenere a mente:

- **Non esiste l'8K60 sulla X6.** Il massimo in 8K è 50 fps. Tutto il dibattito
  sull'8K60 riguarda solo la Pro 2.
- **Il 6K della X6 è 6016×3008, non 6144×3072.** Sono risoluzioni diverse:
  scalare a 6144 è un **upscale del 2,1%** — pixel inventati e bitrate sprecato.
  Usare sempre il preset nativo.

La X6 gira nativamente entrambi i punti ottimali della sezione 1: **6K60 e 8K30.**
Nessun ridimensionamento necessario.

### Insta360 X5

File `.insv`, H.265, ~180-200 Mbps in camera.

| modo | risoluzione | framerate disponibili |
|------|-------------|-----------------------|
| 8K | 7680 × 3840 | **30** / 25 / 24 |
| 5.7K | 5760 × 2880 | **60** / 50 / 48 / 30 / 25 / 24 |
| 4K | 3840 × 1920 | 120 / 100 / 60 / 50 / 48 / 30 / 25 / 24 |

Anche la X5 gira l'**8K30 nativo**: per la nitidezza non serve un'altra camera.
Per il movimento il suo nativo è il 5.7K60 (non c'è un 6K).

### Insta360 Pro 2

6 lenti, una microSD per lente. Unica sorgente con **8K60 nativo** (mono,
7680×3840) — e l'unico caso in cui ha senso porsi il problema (vedi sezione 5).
Gira anche in **3D stereoscopico**: 8K 3D a 30 fps (7680×7680, top-bottom),
6K 3D a 60 fps.

### Step 0 — Stitching, sempre prima di tutto

**I file grezzi non vanno mai dati a FFmpeg né alla GUI.**

- **`.insv` (X5 / X6)**: contiene i due fisheye separati, non è ancora un 360.
  Darlo a FFmpeg produce un singolo fisheye deformato — FFmpeg non ha la
  calibrazione ottica Insta360.
- **Cartella Pro 2**: contiene il progetto `pro.prj`, le proxy e i sei
  `origin_*.mp4`, uno per lente (sulle microSD). Non è un video 360 finché non
  viene cucito.

Lo stitching lo fanno i tool Insta360: **plugin per Premiere o Insta360 Studio**
per X5/X6, **Insta360 Stitcher** per la Pro 2. L'input della catena è **sempre
il materiale già cucito**.

---

## 4. Il flusso

```
Stitching plugin Insta360
   → Premiere (montaggio)
   → export ProRes 422
   → verifica colore
   → FFmpeg (HEVC + scaling se serve)
   → metadati 360
   → test nel visore
```

### Perché il ProRes intermedio non è uno spreco

Sembra un giro a vuoto (100 → ~1000 → 100 Mbps), ma i tre numeri non descrivono
lo stesso contenuto: l'originale è **grezzo e non montato**, il file finale è
**stitchato e montato**. La trasformazione pesante deve materializzarsi da qualche
parte prima di FFmpeg.

Il motivo vero è **evitare la doppia compressione**: se Premiere esportasse HEVC
e poi FFmpeg ricomprimesse HEVC, il secondo encoder lavorerebbe sugli artefatti
del primo e il 360 si spappola. Il ProRes rompe la catena — **una sola**
compressione lossy seria, quella finale, con i parametri buoni.

Il bitrate da solo non misura la qualità: 100 Mbps di ProRes sono scarsi,
100 Mbps di HEVC tunato sono ottimi. Costo reale: solo spazio disco temporaneo.

> La "pipe" Premiere → FFmpeg senza file intermedio **non è applicabile**:
> Premiere è una GUI, esporta solo su file. Vale su Mac e Windows uguale.

### Quale ProRes

**ProRes 422 standard.** Non LT, non HQ.

- 422 standard preserva tutta l'informazione utile e su M4 esce con accelerazione
  hardware dedicata (veloce).
- 422 HQ raddoppia lo spazio senza guadagno visibile.
- **422 LT**: accettabile solo con vincoli seri di spazio, e **più rischioso di
  quanto dicessero i doc vecchi** — vedi nota 10-bit qui sotto.

> **Nota 10-bit (novità X6).** I documenti precedenti giustificavano il ProRes 422
> con "la sorgente è H265 8-bit, nessun ProRes aggiunge dettaglio oltre". **Con la
> X6 in I-Log la registrazione è 10-bit** (e il Dolby Vision è HDR), quindi quel
> ragionamento non vale più. Il 422 standard resta la scelta giusta (è 10-bit), ma per un motivo
> diverso — e il 422 LT diventa più esposto sui gradienti.

Prima di esportare: sequenza impostata alla **risoluzione e al framerate nativi**
della sorgente, per evitare riscali accidentali. Non attivare "Use Maximum Render
Quality" se export = risoluzione sequenza.

---

## 5. Scegliere risoluzione e framerate

**La scelta si può fare in due punti**: in export da Premiere, oppure nella GUI,
che ora ha uno scaler lanczos. Due regole:

1. **Si può solo scendere.** Scalare verso l'alto inventa pixel e spreca bitrate.
2. **Preferire il nativo quando coincide con il target.** Uno scaling, anche in
   discesa, costa sempre qualcosa.

### Per sorgente

| sorgente | movimento → | nitidezza → |
|----------|-------------|-------------|
| **X6** | 6K60 nativo (6016×3008) | 8K30 nativo |
| **X5** | 5.7K60 nativo | 8K30 nativo |
| **Pro 2** | 8K60 → scalare a 6K60 | 8K30 (scarto frame) o 8K60 testato |
| **Pro 2 3D** | 6K 3D 60 | 8K 3D 30 (7680×7680 TB) |

### Il caso Pro 2 8K60

È l'unico materiale con 8K60 nativo, quindi scendere butta via pixel veri. Ma a
0,068 bit/pixel il file è strozzato. Tre strade, in ordine di preferenza:

1. **8K30** — se il contenuto lo consente, è la resa migliore in assoluto.
2. **Scalare a 6K60** — fluidità piena, 0,111 bit/px, nessun rischio.
3. **8K60 a 150-160 Mbps** — solo dopo test nel visore, con i parametri x265
   dedicati della sezione 7.

---

## 6. Codifica FFmpeg — stringa di produzione

### HEVC hardware (VideoToolbox) — default

Veloce, ~0,5× realtime, ~4 min per clip da 2 min.

```bash
ffmpeg -i input_prores.mov \
  -map 0:v:0 -map 0:a:0? \
  -c:v hevc_videotoolbox -profile:v main10 -b:v 120M \
  -pix_fmt p010le \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 \
  -tag:v hvc1 \
  -c:a aac -b:a 320k -ac 2 \
  -movflags +faststart \
  output.mp4
```

Punti obbligatori:

- **`-tag:v hvc1`** — senza, molti player su Quest non leggono lo stream.
  Il tag `hev1` non va: è la prima cosa da verificare se un file "non si apre".
- **`-map 0:v:0 -map 0:a:0?`** — evita che tracce timecode o dati del ProRes
  finiscano nell'MP4, e non fallisce se la sorgente è muta.
- **`-movflags +faststart`** — moov in testa, la Quest apre il file più in fretta.
- I tag colore devono combaciare con la sorgente (sezione 8).

Bitrate: **120 Mbps** per 6K60 e 8K30 da file locale. Salire a 140-150 se compare
blocking su fogliame o acqua.

### Ridimensionamento

Quando serve scendere di risoluzione, aggiungere prima dell'encoder:

```bash
  -vf scale=6016:3008:flags=lanczos
```

Per il mono sempre 2:1 (equirettangolare): l'altezza è esattamente metà della
larghezza. Per lo stereo top-bottom il frame è 1:1 (es. `scale=3840:3840`), per il
side-by-side 4:1.

---

## 7. Alternativa software — libx265

Resa migliore a parità di bitrate, tempi molto più lunghi. VideoToolbox è "buono
ma non bello"; `libx265 -preset fast` è il compromesso quando la resa conta più
della velocità.

**I parametri x265 vanno tarati sulla risoluzione.** Un'unica stringa fissa non
funziona: il `vbv-maxrate` tarato sul 5.7K strozza tutto quello che sta sopra.

La fascia si decide sui **pixel codificati al secondo** (colonna Mpx/s della
sezione 1), non sul nome della risoluzione: un 8K30 (885 Mpx/s) sta nella fascia
bassa, un 7K60 (1329 Mpx/s) in quella alta. La GUI sceglie da sola (soglia 1200 Mpx/s).

### Fascia standard — fino a ~1150 Mpx/s (5.7K60, 6K60, 8K30)

```
keyint=60:min-keyint=60:bframes=3:aq-mode=3:
psy-rd=2.0:psy-rdoq=1.0:sao=0:rc-lookahead=40:
vbv-maxrate=120000:vbv-bufsize=240000
```

### Fascia alta — sopra ~1300 Mpx/s (7K60, 8K50, 8K60)

```
keyint=60:min-keyint=60:bframes=4:aq-mode=3:
psy-rd=1.5:psy-rdoq=1.0:sao=0:rc-lookahead=25:
vbv-maxrate=160000:vbv-bufsize=320000
```

Cosa cambia e perché:

- **`vbv-maxrate` 160000** — 160 Mbps è il massimo che la Quest digerisce con
  margine da storage locale. Non arriva ai ~214 Mbps che servirebbero per pareggiare
  i bit/pixel del 5.7K, ma recupera un terzo del divario.
- **Level e tier: non forzarli.** Con questi parametri x265 (4.x) sceglie da solo
  **Level 6.1 High tier** per l'8K60 (verificato), e il VBV a 160 Mbps resta
  intatto. Aggiungere `level-idc=6.1` è inutile per l'8K 2:1 e **fa fallire**
  l'encode dei frame più grandi, come il 3D top-bottom 7680×7680 della Pro 2
  (`picture dimensions are out of range for specified level`). Da evitare anche
  `no-high-tier`: x265 salirebbe a Level 7.1 Main tier.
- **`psy-rd` 1.5** invece di 2.0 — con un VBV stretto, psy-rd aggressivo ruba bit
  alle zone piatte per dare "texture", e ad alta risoluzione fa più danni che bene.
- **`rc-lookahead` 25** invece di 40 — a 8K 10-bit il lookahead è il primo divoratore
  di RAM.
- **`bframes` 4** — un B-frame in più recupera efficienza, la Quest li decodifica
  senza problemi.

### Gli altri parametri, spiegati

- `crf 16` — qualità altissima. **Ricorda che il VBV può impedirne il
  raggiungimento** (sezione 1): se sei strozzato, il CRF è un desiderio, non un
  risultato.
- `sao=0` — disattiva il Sample Adaptive Offset: recupera dettaglio fine su
  fogliame e texture.
- `aq-mode=3` — distribuisce i bit verso zone scure e uniformi (cieli, gradienti →
  meno banding).
- `10-bit` (`yuv420p10le`) — riduce il banding sui gradienti anche da sorgente 8-bit.
- `keyint=60` — keyframe ogni secondo: seek fluido, meno carico sul decoder.

### Nota strutturale sull'equirettangolare

Nell'equirettangolare **zenit e nadir sono enormemente sovracampionati**: una fetta
consistente del bitrate finisce a codificare cielo e treppiede stirati ai poli.
Nessun parametro x265 lo risolve. È il motivo strutturale per cui il 6K rende
meglio del previsto rispetto all'8K.

### Tempi indicativi (M4, preset fast)

| risoluzione | velocità | clip da 6 min |
|-------------|---------:|--------------:|
| 6.6K | ~6,8 fps | ~55 min |
| 8K | ~4-5 fps | ~80-90 min |

Per testare impostazioni senza attendere: **`-t 15`** subito dopo `-i input.mov`
codifica solo 15 secondi.

> Nella riga di progress, `fps=30` è la **velocità di codifica**, non il framerate
> del file. Il framerate reale è nello stream di output.

---

## 8. Spazio colore — il punto più delicato

I tag colore **devono combaciare con la sorgente**. Se sbagli, la stringa FFmpeg
è comunque "valida": l'errore è **silenzioso** e si vede solo nel visore, dove i
colori escono slavati o sballati.

Verifica cosa c'è davvero nel ProRes:

```bash
ffprobe -v error -select_streams v:0 \
  -show_entries stream=color_primaries,color_transfer,color_space \
  -of default=noprint_wrappers=1 input_prores.mov
```

| Cosa leggi | Significato | Tag FFmpeg |
|------------|-------------|------------|
| `bt709` | SDR | `-color_primaries bt709 -color_trc bt709 -colorspace bt709` |
| `bt2020` + `smpte2084` | HDR10 / PQ | `-color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc` |
| `bt2020` + `arib-std-b67` | HDR HLG | `-color_primaries bt2020 -color_trc arib-std-b67 -colorspace bt2020nc` |

### Dolby Vision (X6) — decisione da prendere prima di girare

La X6 offre un HDR vero in camera, ma il **Dolby Vision usa metadati dinamici che
non sopravvivono alla catena**: passando per ProRes e FFmpeg si perdono comunque.
Due strade, da scegliere in fase di ripresa:

1. **Girare Rec.709 o I-Log**, gradare in Premiere, consegnare **SDR BT.709.**
   È la strada raccomandata: massima affidabilità tra i player Quest.
2. **Girare Dolby Vision** sapendo che in uscita diventa al massimo **HDR10/PQ
   statico**, da etichettare `bt2020 + smpte2084` — e da verificare nel visore,
   perché il supporto HDR dipende dal player (DeoVR lo gestisce, la galleria di
   sistema meno bene).

Altre note:

- **I-Log ≠ HDR**: è un profilo log SDR da sviluppare in grading; l'output finale
  è SDR BT.709.
- L'**H.264 old-style è solo 8-bit SDR**: per qualsiasi HDR serve HEVC.
- La GUI **non fa tone-mapping HDR→SDR**: imposta i tag giusti, ma la conversione
  va fatta in Premiere.

---

## 9. H.264 old-style — solo compatibilità

Per player o dispositivi datati che **non** supportano HEVC. Per la Quest 3/3S non
è mai la scelta migliore.

```bash
ffmpeg -i input.mov \
  -map 0:v:0 -map 0:a:0? \
  -c:v h264_videotoolbox -b:v 200M -maxrate 200M -bufsize 100M \
  -pix_fmt yuv420p -g 60 \
  -tag:v avc1 \
  -c:a aac -b:a 320k -ac 2 \
  -movflags +faststart \
  output_h264.mp4
```

Limiti:

- **`h264_videotoolbox` non va oltre 4096 px per lato** (verificato su Apple
  Silicon): su un 5.7K/6K/8K l'encoder non si apre proprio. Il comando sopra vale
  quindi solo fino al 4K (aggiungere `-vf scale=3840:1920:flags=lanczos`); sopra
  serve `libx264` (software, lento), che la GUI usa in automatico.
- Il limite hardware H.264 della Quest è **più basso** di quello HEVC: il 5.7K60 è
  al confine → rischio decodifica software → stutter.
- 200 Mbps è uno spike alto per il buffer della Quest; `bufsize 100M` lo attenua,
  ma il file resta pesante.
- Solo 8-bit SDR.

---

## 10. Metadati 360

### Iniezione

```bash
exiftool -api LargeFileSupport=1 -overwrite_original \
  -XMP-GSpherical:Spherical=true \
  -XMP-GSpherical:Stitched=true \
  -XMP-GSpherical:StitchingSoftware="X5 Quest Encoder" \
  -XMP-GSpherical:ProjectionType=equirectangular \
  -XMP-GSpherical:StereoMode=mono \
  output.mp4
```

**`StitchingSoftware` serve davvero.** FFmpeg (e chi ne usa il parser) accetta il
box spherical solo se contiene anche quel tag: senza, `ffprobe` scrive
`Invalid spherical metadata found` e il file risulta piatto. Il valore è libero.

**`-api LargeFileSupport=1` va sempre messo.** Con exiftool 12.x l'opzione è
disattivata di default e, su un MP4 oltre i 4 GB, exiftool si ferma con:

```
Warning: [minor] No media data
Error: End of processing at large atom (LargeFileSupport not enabled)
```

`StereoMode`: `mono` per X5, X6 e Pro 2 in 2D. `top-bottom` per la Pro 2 in 3D;
`left-right` per il side-by-side.

### ⚠️ ExifTool riscrive l'intero file

Non è un'operazione di metadati leggera: **rigenera l'MP4 da capo.** Su un 360 da
10 GB significa diversi minuti e **altrettanti GB liberi** sul volume (il doppio se
non usi `-overwrite_original`, che tiene una copia `_original`).

Se il disco si riempie a metà, resta un file **`*_exiftool_tmp`** gigante da
cancellare a mano.

### Verifica

```bash
exiftool -api LargeFileSupport=1 \
  -XMP-GSpherical:all -CompressorID -ImageSize -VideoFrameRate \
  -ColorPrimaries -TransferCharacteristics -MatrixCoefficients \
  output.mp4
```

Controlla in una sola lettura i tag Spherical, il codec tag (**deve essere `hvc1`**)
e il colore. Per vedere il file come lo legge un player basato su FFmpeg:

```bash
ffprobe -v error -show_entries stream_side_data output.mp4
```

deve comparire `projection=equirectangular` (e `type=top and bottom` per il 3D TB).

Fallback: **DeoVR** riconosce il formato anche dal nome file — per esempio
`titolo_360_mono.mp4` o `titolo_360_TB.mp4`.

---

## 11. Spazio disco — la trappola pratica

Un 360 a 120 Mbps occupa **circa 0,9 GB al minuto**. Una nottata di batch riempie
un SSD esterno senza preavviso, e quando succede:

- ffmpeg muore con `No space left on device` lasciando **MP4 troncati** che occupano
  spazio e non servono a niente;
- exiftool lascia **`*_exiftool_tmp`** grandi quanto il file originale.

Prima di lanciare un batch notturno:

```bash
df -h /Volumes/NOME_DISCO
ls -lhS /Volumes/NOME_DISCO/ | head -20
find /Volumes/NOME_DISCO -name "*_exiftool_tmp"
```

**Regola: servono il doppio dei GB stimati** — una volta per l'encode, una volta
per la riscrittura di exiftool. La GUI stima e controlla entrambi (sezione 12).

---

## 12. Tool GUI — `x5_quest_encoder.py`

App Tkinter che automatizza FFmpeg + metadati su macOS.

```bash
python3 x5_quest_encoder.py
```

Dipendenze da riga di comando: `ffmpeg`, `ffprobe`, `exiftool`
(`brew install ffmpeg exiftool`). Nessuna dipendenza pip.

**Cosa fa:**

- **Batch** di file ProRes/MOV → `<nome>_quest.mp4`.
- **Cartella di destinazione separata** — utile per scrivere su un disco diverso da
  quello del sorgente (più veloce, e aggira il disco pieno).
- **Tre encoder**: HEVC hardware (★ default), HEVC software x265 tuned, H.264 old-style
  (VideoToolbox fino a 4096 px, libx264 sopra).
- **Ridimensionamento** con scaler lanczos, preset nativi (8K, 6K 6016×3008 della
  X6, 5.7K della X5) + larghezza personalizzata. L'altezza segue la modalità 3D:
  2:1 mono, 1:1 top-bottom, 4:1 side-by-side.
  **Solo in discesa**: se il target è più grande del sorgente (o uguale) lo scaling
  viene saltato e il log lo segnala.
- **Parametri x265 per fascia** (sezione 7), scelti in automatico da risoluzione di
  uscita e framerate del sorgente.
- **Rileva il colore dalla sorgente** con ffprobe e imposta SDR / PQ / HLG da solo.
- **Audio**: AAC stereo 320k, AAC multicanale 512k, o nessun audio.
- **Metadati 360** via exiftool con `LargeFileSupport` e `StitchingSoftware`,
  modalità Mono / TB / SBS.
- **Solo metadati** su un MP4 esistente (senza ricodificare) — la strada per
  recuperare un encode finito male.
- **Verifica metadati** via exiftool, con avviso se manca lo Spherical o se il
  codec tag è `hev1` invece di `hvc1`.
- **Stima spazio** dell'intero batch raggruppata per volume, senza codificare niente.
- **Controllo preventivo dello spazio** prima di ogni file: se non ci sta, salta e
  lo dice invece di scrivere un file troncato.
- **Pulizia automatica** degli output incompleti dopo un errore.
- **Mostra comando**: anteprima shell-quotata e copia-incollabile.
- Progress reale (legge `time=` contro la durata), batch, Annulla.

**Limiti noti:**

- Non fa tone-mapping HDR→SDR (va fatto in Premiere).
- Non scrive i metadati di audio ambisonico (SA3D): per quelli serve lo
  [spatial-media](https://github.com/google/spatial-media) di Google.
- Bitrate di default HEVC hardware: **120 Mbps** (sezione 6).

---

## 13. Nel visore — metà della qualità si gioca qui

- **DeoVR** o **Pigasus**, mai la galleria di sistema (riproduce a risoluzione ridotta).
- Alzare *sphere/texture resolution* o "quality" al massimo nel player.
- Refresh Quest a **90 Hz** — nella nostra esperienza a 120 Hz alcuni player
  abbassano la risoluzione di rendering (non documentato: verificare col proprio
  player).
- **File locale sideloadato**, non streaming (lo streaming reintroduce compressione
  e cap di bitrate).

### Il test che conta

**Verificare sempre nel visore con la testa in movimento**, su una clip difficile di
30 secondi. Sul monitor del Mac sembra sempre perfetto. Nessuna tabella di questo
documento sostituisce quel test.

### Limite fisico da accettare

Un equirettangolare mono avvolge 360° in orizzontale. Nel FOV (~100°) resta poco più
di un quarto della larghezza: 1671 px per un 6K, 2133 px per un 8K. **Sarà sempre un
po' morbido.** L'obiettivo non è la nitidezza assoluta, è non perdere pixel lungo la
catena e dare alla Quest un file che decodifica fluido.

---

## 14. Cosa è cambiato rispetto ai documenti precedenti

Per chi conosceva le versioni vecchie — correzioni sostanziali, non riscritture
cosmetiche.

**Corretto: il limite dei 5760 px.** I doc attribuivano il blocco di Media Encoder
al fatto che "5760 px è al limite di ciò che l'encoder HEVC hardware accetta". Ma
`hevc_videotoolbox` a 5760 px funziona, ed è lo stesso media engine: se passa da
FFmpeg, la larghezza non era il problema. **Era Media Encoder, non VideoToolbox.**
Di conseguenza cade anche il dubbio che "a 7680 px l'encoder hardware potrebbe non
agganciarsi" — e comunque si verifica in 15 secondi con `-t 15`.

**Corretto: `-api LargeFileSupport=1`.** Mancava in tutti i comandi exiftool dei doc
vecchi. Su qualsiasi file oltre i 4 GB — cioè praticamente ogni 360 — l'iniezione
falliva silenziosamente dopo l'encode.

**Corretto: l'8K30 non è un ripiego.** I doc lo davano come "alternativa per scene
lente". È invece la configurazione di massima qualità dell'intera catena (sezione 1).

**Corretto: il VBV va tarato sulla risoluzione.** La stringa x265 unica con
`vbv-maxrate=120000` strozzava silenziosamente tutto sopra il 6K, trasformando un
"CRF 16" in "120 Mbps forzati".

**Aggiornato: la risoluzione non si decide più solo in Premiere.** I doc dicevano
che "FFmpeg/GUI non ridimensionano". La GUI ora ha lo scaler lanczos, con il 6K
nativo X6 (6016×3008, non più 6144×3072) e il blocco dell'upscale.

**Aggiornato: la X6 e il 10-bit.** Nuova sorgente, nuovi modi nativi, Dolby Vision,
e la premessa "sorgente 8-bit" che non vale più per il ProRes.

**Corretto: le sorgenti.** La X5 gira anche l'8K30 nativo (i doc lo davano come
non disponibile). La Pro 2 non produce un file `.ins`: registra una cartella con
`pro.prj` e un `origin_*.mp4` per lente, e si cuce con Insta360 Stitcher. E non è
solo mono: ha anche i modi 3D.

**Corretto: H.264 hardware oltre il 4K.** `h264_videotoolbox` non apre l'encoder
sopra i 4096 px per lato: un H.264 5.7K da FFmpeg richiede `libx264`.

**Corretto: metadati letti da FFmpeg.** Senza `StitchingSoftware` FFmpeg scarta il
box spherical scritto da exiftool.

**Ridimensionato: `rc-lookahead`.** I doc usavano 60, la GUI 40, e a 8K conviene 25.
Allineato per fascia di risoluzione nella sezione 7.

---

## TL;DR

1. **Stitch con i tool Insta360** (plugin/Studio per X5/X6, Stitcher per la Pro 2) —
   mai il grezzo a FFmpeg.
2. **Premiere → ProRes 422 standard**, sequenza alla risoluzione nativa.
3. **Scegli il target**: **6K60** per il movimento, **8K30** per la nitidezza.
   Sulla X6 sono entrambi nativi; la X5 ha 8K30 e 5.7K60. Solo in discesa, mai upscale.
4. **Verifica il colore** della sorgente e imposta i tag giusti. Con Dolby Vision,
   decidi *prima di girare*.
5. **FFmpeg** → `hevc_videotoolbox -b:v 120M -profile:v main10 -tag:v hvc1`,
   oppure `libx265 -preset fast` con i parametri della **fascia giusta** (sezione 7).
6. **Metadati** → `exiftool -api LargeFileSupport=1` con `StitchingSoftware`,
   + verifica del tag `hvc1`.
   Tieni libero il doppio dello spazio.
7. **Nel visore**: DeoVR/Pigasus, quality al max, 90 Hz, file locale,
   **testa in movimento**.
8. Per automatizzare: **`x5_quest_encoder.py`**.

---

## Fonti e livello di affidabilità

**Verificato su documentazione ufficiale o stampa di settore:**
[Insta360 X6 — specifiche](https://www.insta360.com/specs/x6) ·
[Newsshooter — Insta360 X6](https://www.newsshooter.com/2026/08/12/insta360-x6-flagship-8k-360-camera/) ·
[Insta360 X5 — specifiche](https://onlinemanual.insta360.com/x5/en-us/specs/shooting-specs) ·
[Insta360 Pro 2 — manuale, stitching](https://onlinemanual.insta360.com/pro2/en-us/video/postproduction/1) ·
[Insta360 Pro 2 — prodotto](https://www.insta360.com/product/insta360-pro2) ·
[DeoVR — documentazione](https://deovr.com/documentation)

**Verificato con test** (M4, FFmpeg 8.1, x265 4.2, exiftool 12.85): comandi
VideoToolbox HEVC fino all'8K60, limite 4096 px di `h264_videotoolbox`, scelta
del level da parte di x265, lettura dei metadati spherical da parte di FFmpeg
(vedi anche il [parser in `libavformat/mov.c`](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/mov.c)).

**Test di terze parti, da trattare come ordine di grandezza** (Meta non pubblica
specifiche ufficiali di decodifica):
[explorations360 — Meta Quest 3 360° Video Encoding](https://explorations360.com/en/academy/meta-quest-3) ·
[Bitmovin — Encoding VR and 360 Immersive Video for Meta Quest](https://bitmovin.com/blog/best-encoding-settings-meta-vr-360-headsets/)

**Calcolato** (aritmetica su risoluzione × framerate ÷ bitrate): tutte le tabelle
bit/pixel. Sono matematica, non misure di qualità percepita.

**Da verificare sul campo**: ogni soglia di "regge / non regge". L'unico test valido
è la clip difficile di 30 secondi nel visore, con la testa in movimento.
