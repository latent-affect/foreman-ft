#!/bin/bash
# Seeded corpus file. Reads the .pod_id that provision.sh never writes.
POD_ID=$(cat .pod_id)
runpodctl pod start "$POD_ID"
