(()=>{
  const notice=document.querySelector('#cookie-notice');
  if(!notice)return;
  notice.querySelector('[data-cookie-notice-dismiss]').addEventListener('click',()=>{
    const secure=window.location.protocol==='https:'?'; Secure':'';
    document.cookie=`trud_cookie_notice=1; Max-Age=31536000; Path=/; SameSite=Lax${secure}`;
    notice.remove();
  });
})();
