function contextMenuItems(params, download) {
  const flags = params.editFlags || {};
  const items = [];
  const add = (role, flag) => items.push({role, enabled: flags[flag] === true});
  if (params.isEditable) {
    add('undo', 'canUndo'); add('redo', 'canRedo');
    items.push({type:'separator'});
    add('cut', 'canCut'); add('copy', 'canCopy'); add('paste', 'canPaste');
  } else {
    add('copy', 'canCopy');
  }
  add('selectAll', 'canSelectAll');
  if (download) {
    items.push({type:'separator'}, {label:'Download attachment…', click:download});
  }
  return items;
}
module.exports = {contextMenuItems};
