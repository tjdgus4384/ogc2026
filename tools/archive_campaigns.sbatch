#!/usr/bin/env bash
#SBATCH -J archive
#SBATCH -N 1
#SBATCH -c 8
#SBATCH --mem=16G
#SBATCH -t 00:30:00
#
# Fold the 08-07..11 campaign trees into one compressed archive. Their
# verdicts and the one surviving measurement live in docs/CAMPAIGN_LEDGER.md;
# what is left here is 1.3 GB of sealing/CAS/manifest provenance for campaigns
# that adopted nothing. Compress first, verify, and only then remove.
set -euo pipefail
REPO="${SLURM_SUBMIT_DIR:-$(pwd)}"
cd "$REPO"
OUT=archive/campaigns-0807-0811.tar.zst
mkdir -p archive
tar --sort=name -cf - campaign | zstd -T8 -12 -o "$OUT" -f
zstd -t "$OUT"
BEFORE=$(du -sb campaign | cut -f1)
AFTER=$(stat -c%s "$OUT")
echo "campaign/ $BEFORE B -> $OUT $AFTER B ($((BEFORE / (AFTER ? AFTER : 1)))x)"
# Verify the archive lists every file before deleting the originals.
# Count symlinks too: tar stores them as entries, so comparing against
# `find -type f` alone reports a spurious mismatch and refuses the cleanup.
N_SRC=$(find campaign \( -type f -o -type l \) | wc -l)
N_TAR=$(tar -I zstd -tf "$OUT" | grep -vc '/$')
echo "files: source=$N_SRC archived=$N_TAR"
[ "$N_SRC" -eq "$N_TAR" ] || { echo "file count mismatch -- keeping campaign/"; exit 1; }
sha256sum "$OUT"
rm -rf campaign
echo "campaign/ removed; restore with: tar -I zstd -xf $OUT"
