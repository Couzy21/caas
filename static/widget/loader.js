(() => {
  'use strict';
  const script = document.currentScript;
  const bot = script.dataset.bot;
  if (!/^[0-9a-f-]{36}$/i.test(bot || '')) return;
  const base = new URL(script.src).origin;
  const button = document.createElement('button');
  button.type = 'button'; button.textContent = 'Chat with us';
  Object.assign(button.style, {position:'fixed',bottom:'20px',right:'20px',padding:'14px',zIndex:'2147483000'});
  let frame;
  button.addEventListener('click', () => {
    if (!frame) {
      frame = document.createElement('iframe');
      frame.src = `${base}/widget/${bot}/?origin=${encodeURIComponent(location.origin)}`;
      frame.title = 'Business chat'; frame.referrerPolicy = 'strict-origin-when-cross-origin';
      Object.assign(frame.style,{position:'fixed',bottom:'80px',right:'20px',width:'min(360px, 92vw)',height:'480px',border:'1px solid #ccc',borderRadius:'12px',zIndex:'2147483000',background:'white'});
      document.body.appendChild(frame);
    } else frame.hidden = !frame.hidden;
  });
  document.body.appendChild(button);
})();
