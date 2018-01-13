#!/bin/bash
set -x

INPUT_DEVICES=""
FILESYSTEM=""

while getopts ":f:i:" opt; do
  case $opt in
    f)
      FILESYSTEM=$OPTARG
      ;;
    i)
      INPUT_DEVICES=$OPTARG
      ;;
    \?)
      echo "Invalid option: -$OPTARG" >&2
      exit 1
      ;;
    :)
      echo "Option -$OPTARG requires an argument." >&2
      exit 1
      ;;
  esac
done

[[ -z "$FILESYSTEM" ]] && { echo "Error: filesystem with -f is not set"  >&2; exit 1; }
[[ -z "$INPUT_DEVICES" ]] && { echo "Error: input devices with -i is not set"  >&2; exit 1; }

ADDED_DEVICES=$(btrfs filesystem show $FILESYSTEM | grep -Eo "/dev/[A-z]{3}[0-9]*")
[[ "$?" != 0 ]] && { echo "Getting devices returned error for $FILESYSTEM" >&2; exit 1; }

NEW_DEVICE="false"
for ID in $INPUT_DEVICES; do
    echo $ID | grep -qw "$ADDED_DEVICES"
    [[ "$?" != 0 ]] && { NEW_DEVICE="true"; btrfs device add $ID $FILESYSTEM || exit $?; }
done

[[ "$NEW_DEVICE" == "true" ]] && { btrfs balance start -dconvert=raid1 -mconvert=raid1 $FILESYSTEM; exit $?; }
exit 0