# Keeping and inspecting the baseline

Keep the entire printed `cp0d-...` directory outside the working repository, preferably
with a second backup. The small `-summary.tar.gz` is for review; it does not contain
the source archive or Git bundle and cannot restore the baseline.

- `snapshot.tar.gz`: source/dependency bytes plus selected evidence and physical inputs,
  stored beneath `repo/`. Available symlink targets are materialized; their original link values
  are in `source-selection.json`. Exact reviewed unresolved optional links are
  stored as JSON records in `snapshot-metadata/unresolved-links.json` beside the
  extracted `repo/` tree, and as `unresolved-links.json` beside the archive. These
  records preserve the original path and target, including absolute upstream paths.
  They are not automatically recreated as symlinks on extraction, and their missing
  target bytes are not part of the snapshot. No simulator plugin or Qucs-S symbol
  file should be inferred to exist from the metadata. Git metadata and the listed output/cache directories
  are excluded. Exact selected historical outputs are added back explicitly.
- `repository.bundle`: root repository history reachable from HEAD. Submodule working
  bytes and revisions are recorded; nested Git histories are not independently bundled.
- `git.json`: original HEAD, branch, staging-index hash, worktree status and submodule revisions.
- `worktree.patch`, `index.patch`: recorded root repository differences from HEAD.
- `files.json`: every captured file's content hash, size and permissions.
- `tools.json`: installed tool paths and hashes. External tool/runtime installations
  are not copied. Keep the existing native tool environments.
- `baseline.json`: accepted development scope, physical hashes and measured comparison data.

Verify the retained directory with `shasum -a 256 -c SHA256SUMS` from that directory.
To inspect a source copy, create an empty destination and unpack `snapshot.tar.gz`
there. Its `repo/` subdirectory contains the captured bytes. Do not unpack over the
active CNN checkout. This source copy is not an automatically restored Git/submodule
checkout, and paths in native environment scripts may need regeneration after relocation.

To inspect the historical Git commit separately, use
`git clone repository.bundle NEW_EMPTY_DIRECTORY`; the bundled HEAD does not include
uncommitted modifications. The working-state archive is authoritative for those.
Preserve staged and untracked work using the saved status, patches and source files
if reconstructing a Git checkout. Do not assume that switching to the old HEAD alone
restores the development baseline.

A clean-source-to-layout rebuild has not been performed by the freeze command.
