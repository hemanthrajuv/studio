#!/usr/bin/env bash
# Start the studio voice server if it isn't already running. Safe to call repeatedly.
if curl -s -m 2 http://100.107.204.122:8191/health >/dev/null; then echo "already running"; exit 0; fi
source /home/dev/miniconda3/etc/profile.d/conda.sh
conda activate qwen3tts
export HF_HOME=/mnt/d1/tts/qwen3tts/hf_cache
setsid nohup python /mnt/d1/tts/bin/studio_tts_server.py >> /mnt/d1/tts/studio_tts.log 2>&1 < /dev/null &
echo "started"
