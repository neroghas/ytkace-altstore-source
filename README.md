# YTKACE AltStore/SideStore Source

A minimal personal source that automatically tracks official IPA releases from
[`itzzace/ytkace`](https://github.com/itzzace/ytkace).

## Source URL

```text
https://raw.githubusercontent.com/neroghas/ytkace-altstore-source/main/source.json
```

Add this URL to SideStore. Install YTKACE as a standalone app through SideStore.
The same source can be added to LiveContainer when an older listed release is
needed.

## Guarantees

- IPA links point directly to official `itzzace/ytkace` GitHub Release assets.
- IPAs are never rehosted, patched, or rebuilt here.
- Every release with an IPA remains in version history.
- If a release has multiple IPAs, the newer iOS/YouTube build is preferred.
- `source.json` is checked every six hours and updated only when it changes.

The source uses the app's real bundle identifier, version, minimum iOS version,
and byte size verified from each official IPA.
