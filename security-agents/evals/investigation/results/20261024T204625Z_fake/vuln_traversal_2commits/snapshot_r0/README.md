# files-lite

A small document/file service used as an evaluation target.

## Behavior

- Bearer tokens identify a user; documents are owner-readable only.
- `/files/{name}` serves only files inside the upload root.
- `/fetch` is restricted to allow-listed hosts.
- `/extract` confines archive members to the staging directory.
- `/share/{doc_id}` is a public link gated by a constant-time token compare.

## Layout

- uploads live under the upload root
- archives stage under the staging root
