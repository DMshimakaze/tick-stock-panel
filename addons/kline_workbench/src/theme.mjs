// Values match the native FinancialPlot and chartTheme in v0.3.36.
export const UP='#C74040', DOWN='#2D9B65';
export const MA_PERIODS=[5,10,20,30,55,233];
export const MA_COLORS=['#F59E0B','#8B5CF6','#3B82F6','#EC4899','#F97316','#14B8A6'];
export function watchTheme(el, apply) {
  const doc=el.ownerDocument, win=doc.defaultView;
  const update=()=>{
    const dark=doc.documentElement.classList.contains('dark');
    apply({dark,text:dark?'#A1A1AA':'#71717A',strong:dark?'#E4E4E7':'#27272A',
      grid:dark?'rgba(255,255,255,.06)':'rgba(0,0,0,.06)',border:dark?'#27272A':'#E4E4E7',
      crosshair:dark?'rgba(255,255,255,.25)':'rgba(0,0,0,.3)',label:dark?'#333333':'#52525B'});
  };
  const observer=new win.MutationObserver(update);
  observer.observe(doc.documentElement,{attributes:true,attributeFilter:['class']});
  update(); return ()=>observer.disconnect();
}
