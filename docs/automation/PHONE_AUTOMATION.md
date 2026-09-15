# Phone automation

Crate v6 supports mobile handoff in two ways without QR codes or browser notifications.

## Android / PWA share target

Install Crate from the browser as a PWA. Android can then expose **Crate** in the system Share sheet. Sharing a page or media URL opens Crate with that URL prefilled.

## Apple Shortcuts

Create an API token in Crate Admin with `jobs:read` and `jobs:write`, then build a Shortcut with these actions:

1. **Receive** URLs from Share Sheet.
2. **Get Contents of URL**: `https://down.dpifiles.org/api/jobs`.
3. Method: `POST`; JSON body: `url` = Shortcut Input, `format` = `mp4`, `quality` = `best`.
4. Headers: `Authorization: Bearer <token>` and `X-Crate-Request: 1`.
5. Show the returned job title/status.

Keep the token in the Shortcut itself or another private local secret store. Never publish it.
