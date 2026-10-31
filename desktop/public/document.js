window.iam.call('viewer-document').then(item => {
  document.getElementById('name').textContent = item.name;
  document.getElementById('content').textContent = item.text;
}).catch(error => { document.getElementById('content').textContent = error.message; });
document.getElementById('download').onclick = async event => {
  const button = event.currentTarget; button.disabled = true;
  try {
    const saved = await window.iam.call('viewer-document-save');
    document.getElementById('downloadStatus').textContent = saved ? 'Document saved.' : '';
  } catch (error) { document.getElementById('downloadStatus').textContent = error.message; }
  finally { button.disabled = false; }
};
