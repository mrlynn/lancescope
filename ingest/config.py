"""Shared paths and tuning constants for the ingest pipeline."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"          # downloaded mp4 + vtt, one dir per talk
WORK = DATA / "work"        # segmented mp4s + extracted frames
LANCE = DATA / "lance"      # the LanceDB database

# Blob V2 gives a row its own dedicated extent at roughly >=8 MB; below that rows are
# packed and reading one drags in its neighbours. See FINDINGS.md. Talk bitrates vary
# a lot, so we target a byte size and derive the duration per talk rather than fixing
# the duration and hoping.
TARGET_SEGMENT_MB = 16
MIN_SEGMENT_SECONDS = 90
MAX_SEGMENT_SECONDS = 600

# Source talks are 1080p at ~3 Mbps. Transcoding to this keeps slide text sharp at
# roughly a third of the bytes, which is the difference between a corpus that fits
# on a laptop and one that does not.
TRANSCODE_HEIGHT = 720
TRANSCODE_BITRATE = "700k"

# Keyframes. Slide-heavy talks change slowly, so a coarse sample plus scene detection
# gets us the slide transitions without drowning in near-duplicates.
FRAME_INTERVAL_S = 3.0
SCENE_THRESHOLD = 0.02      # mean abs pixel delta (0-1) that counts as a new scene
                            # (p85 of observed deltas; ~3 moments/min on slide talks)
THUMB_WIDTH = 384

# SigLIP: image+text in one embedding space, small enough to run on stage.
MODEL_NAME = "ViT-B-16-SigLIP"
MODEL_PRETRAINED = "webli"
EMBED_DIM = 768

# Transcript windows attached to each keyframe.
TRANSCRIPT_WINDOW_S = 30.0

for _p in (RAW, WORK, LANCE):
    _p.mkdir(parents=True, exist_ok=True)
