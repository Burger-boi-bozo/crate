const DEFAULT_SERVER = 'https://down.dpifiles.org';
const status = document.querySelector('#status');

async function crateServer() {
  const value = await chrome.storage.sync.get({crateServer: DEFAULT_SERVER});
  return value.crateServer.replace(/\/$/, '');
}
document.querySelector('#send').onclick = async () => {
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    if (!tab?.url || !/^https?:\/\//i.test(tab.url)) throw new Error('This tab is not a public web page.');
    const base = await crateServer();
    await chrome.tabs.create({url: `${base}/?url=${encodeURIComponent(tab.url)}`});
    window.close();
  } catch (error) {
    status.textContent = error.message;
  }
};

document.querySelector('#settings').onclick = () => chrome.runtime.openOptionsPage();
