# Send to Crate browser extension

This Manifest V3 extension opens the current public page in Crate with its URL prefilled. It does not receive an API token and cannot access Crate admin controls.

## Install locally

1. Open your browser's extensions page and enable developer mode.
2. Choose **Load unpacked** and select this folder.
3. Open the extension settings if your Crate server is not `https://down.dpifiles.org`.
4. Click **Send to Crate** while viewing a public media page.

The extension only requests `activeTab` and `storage` permissions.
