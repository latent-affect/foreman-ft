#!/bin/bash
# Seeded corpus file. INTENTIONAL defects, catalogued in manifest.json.
# SEED: cross-unit contract (check 6), reasoning-only.
#   This script's own comment promises to persist the pod id to .pod_id,
#   and start-session.sh / stop-session.sh both read that file -- but the
#   write never happens. Every unit is internally valid; the SEAM is broken.
# SEED: silent-failure, reasoning-only -- no `set -euo pipefail`, so a failed
#   create continues and downstream steps run against an empty POD_ID.

echo "Creating pod and saving id to .pod_id ..."
POD_ID=$(runpodctl pod create --image "$IMAGE" --gpu-id "$GPU" | grep -o 'id: [a-z0-9]*' | cut -d' ' -f2)
echo "Pod created: $POD_ID"
