#!/bin/sh
set -e

# Define source (in image) and destination (in volume)
DATA_SRC="/app/TECHXPO_init/Booking_data"
DATA_DEST="/app/TECHXPO/Booking_data"

# If destination is empty (first run with empty volume), copy initial data
if [ -d "$DATA_SRC" ] && [ -d "$DATA_DEST" ] && [ ! "$(ls -A $DATA_DEST)" ]; then
  echo "Initializing Booking_data volume from image..."
  cp -a $DATA_SRC/* $DATA_DEST/
fi

# Execute the original command
exec "$@"
