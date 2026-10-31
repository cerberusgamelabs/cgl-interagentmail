// Resize only the input: no focus(), selection changes, or page scrolling.
export function fitDraft(input){
  if(!input?.style)return;
  const scroll=input.scrollTop;
  input.style.height='44px';
  input.style.height=`${Math.min(120,Math.max(44,input.scrollHeight+2))}px`;
  input.scrollTop=scroll;
}
