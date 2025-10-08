// نسخ للنصوص (الاسم/الوصف)
document.addEventListener('click', (e)=>{
  const btn = e.target.closest('.copy-btn');
  if(!btn) return;
  const text = btn.getAttribute('data-copy') || '';
  navigator.clipboard.writeText(text).then(()=>{
    btn.innerHTML = '<i class="fa-solid fa-check"></i>';
    setTimeout(()=> btn.innerHTML = '<i class="fa-regular fa-copy"></i>', 1000);
  });
});
