/* Show the release and exact Git revision currently serving this page. */
fetch('/api/version', {headers: {'X-Crate-Request': '1'}})
  .then(response => response.ok ? response.json() : Promise.reject())
  .then(version => {
    const element = document.querySelector('#build-version');
    if (element && version.label) element.textContent = `  ${version.label}`;
  })
  .catch(() => {});
