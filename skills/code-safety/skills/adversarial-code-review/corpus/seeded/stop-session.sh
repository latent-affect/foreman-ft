#!/bin/bash
# Seeded corpus file. Reads the .pod_id that provision.sh never writes.
# SEED: cost blast radius (check 4), reasoning-only -- if this silently fails,
#   a GPU pod bills continuously. No verification the stop actually took effect.
POD_ID=$(cat .pod_id)
runpodctl pod stop "$POD_ID"
