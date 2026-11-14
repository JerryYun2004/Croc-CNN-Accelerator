# Keep the full baseline

The full baseline was generated on your Mac at:
~/projects/croc-cnn-baselines/cp0d-20261023-125611-6o876n9f

It does not need to be downloaded. ChatGPT received only the small summary, which
omits snapshot.tar.gz, repository.bundle and the two Git patch files. Downloading
that summary cannot reconstruct your full baseline.

Retain this whole directory outside the active Croc checkout, and copy the whole
directory to a second physical drive or a backup service. Keep SHA256SUMS and all
other files together. A second folder on the same Mac is convenient but does not
protect against loss of the Mac. After copying, run `shasum -a 256 -c SHA256SUMS`
from inside the copied directory. All lines should say OK.

Alternatively, the package-level backup-baseline.py copies the entire directory
and verifies every recorded file hash before and after the copy. Supply an existing
backup destination directory. It refuses to overwrite an existing baseline copy.
It does not delete a partial copy if interrupted; retain or inspect that copy before
retrying with a fresh destination.

Do not push the full baseline, source snapshot, PDK trees, tool binaries or bulk
EDA results to the source repository. A Git push of HEAD alone would also omit
uncommitted and ignored working files captured in snapshot.tar.gz. Keep source,
small model files, specifications, test vectors and selected baseline metadata in
Git instead. cnn/baseline-pin.json records the baseline identity without embedding
large historical artifacts. Avoid `git add .` while the repo still has many native
helpers/results to review; explicitly stage the intended project paths.

The freeze preserves source/evidence bytes and root Git history, but does not bundle
installed build-tool environments or prove a clean physical rebuild. Keep the
existing native tools and the baseline's RESTORE.md for that scope.
