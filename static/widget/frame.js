(() => {
  'use strict';
  const bot = document.body.dataset.bot, origin = document.body.dataset.origin;
  let token, conversation, pending;
  const form = document.querySelector('#chat'), input = document.querySelector('#question'), send = document.querySelector('#send');
  const line = (text, type) => { const p = document.createElement('p'); p.className=type; p.textContent=text; document.querySelector('#messages').appendChild(p); p.scrollIntoView(); };
  async function session() {
    const response = await fetch(`/api/v1/widget/${bot}/session/`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({origin})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.message + ' ' + JSON.stringify(result.errors || ''));
    token = result.data.token;
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const question = input.value.trim(); if (!question) return;
    send.disabled=true;
    try {
      if (!token) await session();
      // Network retries reuse the original key and payload, even if input changes.
      if (!pending) { pending={key:crypto.randomUUID(),body:{message:question,...(conversation?{conversation_id:conversation}:{})}}; line(question,'user'); }
      const response = await fetch(`/api/v1/widget/${bot}/chat/`,{method:'POST',headers:{'Content-Type':'application/json','X-Visitor-Token':token,'X-Website-Origin':origin,'Idempotency-Key':pending.key},body:JSON.stringify(pending.body)});
      const result = await response.json();
      if (!response.ok) {
        if (response.status !== 409 && response.status !== 503) pending=null;
        throw new Error(result.message+' '+JSON.stringify(result.errors || 'Please retry the same message.'));
      }
      conversation=result.data.conversation_id; line(result.data.answer,'assistant');
      if (result.data.sources.length) line('Sources: '+result.data.sources.map(s=>s.title).join(', '),'sources');
      input.value=''; pending=null;
    } catch(error) { line(error.message || 'Connection failed. Retry to check the same request.','error'); }
    finally { send.disabled=false; input.focus(); }
  });
  line('Welcome. What would you like to know?','assistant');
})();
