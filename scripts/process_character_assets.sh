#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
source_dir="$project_dir/vertew_character"
output_dir="$project_dir/frontend/assets/character/processed"

mkdir -p "$output_dir"

# The supplied PNG files contain a flattened checkerboard rather than real
# transparency. Recover an approximate alpha channel from saturation, dark
# outlines and highlights while keeping the official source files untouched.
process_asset() {
  local name="$1"
  local input="$source_dir/$name.png"
  local output="$output_dir/$name.png"

  magick "$input" -write mpr:source +delete \
    \( mpr:source -colorspace HSL -channel G -separate +channel -threshold 10% \) \
    \( mpr:source -colorspace gray -threshold 18% -negate \) \
    \( mpr:source -colorspace gray -threshold 62% \) \
    -evaluate-sequence max \
    -morphology Close Disk:2 \
    mpr:source +swap -alpha off -compose CopyOpacity -composite \
    "$output"
}

for asset in question sweat fruit_halo; do
  process_asset "$asset"
done

# Use the approved hand-free derivative when present. It has a native black
# hologram background, so no checkerboard recovery is required.
if [[ -f "$source_dir/reference_no_hand.png" ]]; then
  cp "$source_dir/reference_no_hand.png" "$output_dir/reference.png"
else
  process_asset reference
fi

echo "Processed character assets written to $output_dir"
