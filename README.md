# X5 → Quest Encoder

GUI per macOS che trasforma un master **ProRes 360° equirettangolare** in un **MP4
HEVC pronto per Meta Quest 3 / 3S**, con i metadati spaziali già iniettati.

Pensata per il materiale Insta360 **X5**, **X6** e **Pro 2**, già stitchato ed
esportato da Premiere. Un solo file Python, nessuna dipendenza pip.

> 🇬🇧 *Tkinter GUI for macOS / Apple Silicon that encodes stitched 360°
> equirectangular ProRes masters to HEVC MP4 for Meta Quest 3/3S (FFmpeg) and
> injects spherical metadata (ExifTool). UI and docs are in Italian.*

## Installazione

```bash
brew install ffmpeg exiftool
```

```bash
python3 x5_quest_encoder.py
```

Serve Python 3 con Tkinter (incluso nel Python di python.org e in quello di
Homebrew con `brew install python-tk`).

## Cosa fa

- **Batch** di file ProRes/MOV → `<nome>_quest.mp4`, anche su un disco diverso.
- **Tre encoder**: HEVC hardware VideoToolbox (default, 120 Mbps), HEVC software
  libx265 con parametri tarati per fascia di risoluzione, H.264 per compatibilità.
- **Ridimensionamento** lanczos con i preset nativi (8K, 6K 6016×3008, 5.7K) —
  solo verso il basso, l'upscale viene bloccato.
- **Rilevamento colore** dalla sorgente (SDR BT.709 / HDR10 PQ / HLG).
- **Metadati 360** (mono, top-bottom, side-by-side) con `LargeFileSupport` per i
  file oltre i 4 GB, più "solo metadati" e "verifica" su MP4 esistenti.
- **Controllo dello spazio disco** prima di ogni file e pulizia degli output
  troncati.
- **Mostra comando**: tutti i comandi ffmpeg/exiftool sono visibili e
  copia-incollabili, per usarli anche senza GUI.

## La guida

[docs/Workflow_360_Quest3.md](docs/Workflow_360_Quest3.md) è il riferimento
completo della catena: stitching → Premiere → ProRes → FFmpeg → metadati →
visore. Spiega il perché di ogni scelta (bit per pixel, 6K60 vs 8K30, limiti della
Quest, spazio colore, Dolby Vision della X6) e contiene i comandi da usare a mano.

In breve:

1. Stitch nel plugin Insta360 — mai dare `.insv` / `.ins` a FFmpeg.
2. Premiere → **ProRes 422** alla risoluzione nativa.
3. **6K60** per il movimento, **8K30** per la nitidezza. Mai upscale.
4. Verifica il colore della sorgente.
5. HEVC `-tag:v hvc1`, metadati con `exiftool -api LargeFileSupport=1`.
6. Prova nel visore (DeoVR / Pigasus), con la testa in movimento.

## Storia

Le versioni precedenti dello script (v1 → v7) e dei documenti di workflow sono
nella storia git:

```bash
git log --oneline -- x5_quest_encoder.py
```

## Limiti

- Solo macOS (usa VideoToolbox per l'encoding hardware).
- Nessun tone-mapping HDR→SDR: va fatto in Premiere.
- Nessun metadato di audio ambisonico (SA3D): usare
  [google/spatial-media](https://github.com/google/spatial-media).
- Le soglie sulla Quest vengono da test di terze parti: Meta non pubblica
  specifiche ufficiali di decodifica.
