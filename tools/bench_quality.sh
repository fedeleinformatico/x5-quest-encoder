#!/bin/zsh
# Confronto oggettivo fra encoder sulle proprie clip (VMAF contro il master).
#
#   SRC=/percorso/master_prores.mov SS=150 ./tools/bench_quality.sh
#
# SS = secondo di partenza, T = durata del campione. Serve un ffmpeg con
# libvmaf (brew install ffmpeg). Gli mp4 di prova vengono cancellati dopo
# la misura: resta solo la riga di risultato.
# Il riferimento è il master, quindi i punteggi sono confrontabili solo fra
# righe della stessa esecuzione.
SRC=${SRC:?indica il master: SRC=/percorso/master.mov}
SS=${SS:-60}; T=${T:-6}
B="keyint=60:min-keyint=60:aq-mode=3:psy-rdoq=1.0:sao=0:vbv-init=0.1"
W=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=p=0 "$SRC")
H=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=p=0 "$SRC")
echo "master ${W}x${H} — campione ${T}s da ${SS}s"

enc() {
  local n=$1; shift
  local tag=(-tag:v hvc1)
  [[ "$*" == *libsvtav1* ]] && tag=()
  ffmpeg -v error -y -ss $SS -t $T -i "$SRC" -an "$@" $tag $n.mp4 || { echo "FAIL $n"; return; }
  ffmpeg -hide_banner -i $n.mp4 -ss $SS -t $T -i "$SRC" -lavfi \
    "[0:v]setpts=PTS-STARTPTS,scale=${W}:${H}:flags=bicubic,format=yuv420p10le[d];[1:v]setpts=PTS-STARTPTS,format=yuv420p10le[r];[d][r]libvmaf=n_threads=10:log_fmt=json:log_path=$n.json" \
    -f null - 2>vmaf_$n.log || { echo "VMAF fallito per $n (vedi vmaf_$n.log)"; return; }
  python3 -c "
import json,sys,statistics as st,subprocess
n=sys.argv[1]
v=[f['metrics']['vmaf'] for f in json.load(open(n+'.json'))['frames']]
# i primi fotogrammi partono con il buffer VBV quasi vuoto: non sono rappresentativi
v=v[30:] if len(v)>60 else v
br=int(subprocess.run(['ffprobe','-v','error','-show_entries','format=bit_rate','-of','csv=p=0',n+'.mp4'],capture_output=True,text=True).stdout)
s=sorted(v)
print(f'{n:22} {br/1e6:6.1f} Mbps  VMAF {st.mean(v):5.2f}  peggior5% {s[int(len(s)*.05)]:5.2f}', flush=True)" $n
  rm -f $n.mp4
}
for br in 130 160 200; do
  enc vt8k_$br -c:v hevc_videotoolbox -profile:v main10 -b:v ${br}M -pix_fmt p010le
done
for br in 120 160 200; do
  enc x265fast8k_$br -c:v libx265 -preset fast -crf 16 -pix_fmt yuv420p10le \
    -x265-params "$B:bframes=4:psy-rd=1.5:rc-lookahead=25:vbv-maxrate=${br}000:vbv-bufsize=$((br*2))000"
done
enc x265med8k_160  -c:v libx265 -preset medium -crf 16 -pix_fmt yuv420p10le -x265-params "$B:bframes=4:psy-rd=1.5:rc-lookahead=25:vbv-maxrate=160000:vbv-bufsize=320000"
enc x265slow8k_120 -c:v libx265 -preset slow -crf 16 -pix_fmt yuv420p10le -x265-params "$B:bframes=3:psy-rd=2.0:rc-lookahead=40:vbv-maxrate=120000:vbv-bufsize=240000"
enc x265slow8k_160 -c:v libx265 -preset slow -crf 16 -pix_fmt yuv420p10le -x265-params "$B:bframes=4:psy-rd=1.5:rc-lookahead=25:vbv-maxrate=160000:vbv-bufsize=320000"
enc x265slow8k_crf12 -c:v libx265 -preset slow -crf 12 -pix_fmt yuv420p10le -x265-params "$B:bframes=4:psy-rd=1.5:rc-lookahead=25:vbv-maxrate=400000:vbv-bufsize=800000"

# AV1: SVT-AV1 non accetta bitrate target sopra 100 Mbps, si usa il CRF.
enc av1_p8_crf28 -c:v libsvtav1 -preset 8 -crf 28 -b:v 0 -pix_fmt yuv420p10le -svtav1-params "keyint=60"
