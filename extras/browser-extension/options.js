const DEFAULT_SERVER = 'https://down.dpifiles.org';
const input = document.querySelector('#server');
const status = document.querySelector('#status');

chrome.storage.sync.get({crateServer: DEFAULT_SERVER}).then(value => {
  input.value = value.crateServer;
});

document.querySelector('#save').onclick = async () => {
  try {
    const parsed = new URL(input.value.trim());
    if (!['https:','http:'].includes(parsed.protocol)) throw new Error('Use an HTTP or HTTPS URL.');
    const value = parsed.origin + parsed.pathname.replace(/\/$/, '');
    await chrome.storage.sync.set({crateServer: value});
    status.textContent = 'Saved.';
  } catch (error) {
    status.textContent = error.message || 'Enter a valid Crate URL.';
  }
};
