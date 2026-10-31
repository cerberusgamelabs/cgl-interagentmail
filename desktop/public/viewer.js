const image = document.getElementById('image');
window.iam.call('viewer-image').then(item => {
  document.getElementById('name').textContent = item.name;
  image.alt = item.name; image.src = item.data;
}).catch(error => { document.getElementById('error').textContent = error.message; });
document.getElementById('fit').onclick = () => document.querySelector('main').classList.remove('actual');
document.getElementById('actual').onclick = () => document.querySelector('main').classList.add('actual');
document.getElementById('download').onclick = async event => {
  const button = event.currentTarget; button.disabled = true;
  try {
    const saved = await window.iam.call('viewer-image-save');
    document.getElementById('error').textContent = saved ? 'Photo saved.' : '';
  } catch (error) { document.getElementById('error').textContent = error.message; }
  finally { button.disabled = false; }
};
