/* Drawer for the app shell below 1024px (SHELL-001). On wider screens the
   sidebar is always shown and none of this has any visible effect. */
(function(){
  var nav = document.querySelector('[data-shell-nav]');
  if(!nav){ return; }
  var openers = document.querySelectorAll('[data-shell-open]');
  var backdrop = document.querySelector('.hs-backdrop');
  var lastOpener = null;

  function setOpen(open, opener){
    nav.classList.toggle('is-open', open);
    document.body.classList.toggle('hs-drawer-open', open);
    if(backdrop){ backdrop.hidden = !open; }
    openers.forEach(function(button){ button.setAttribute('aria-expanded', open ? 'true' : 'false'); });
    if(open){
      lastOpener = opener || null;
      var first = nav.querySelector('.hs-nav__link');
      if(first){ first.focus(); }
    } else if(lastOpener){
      lastOpener.focus();
      lastOpener = null;
    }
  }

  openers.forEach(function(button){
    button.addEventListener('click', function(){ setOpen(!nav.classList.contains('is-open'), button); });
  });
  document.querySelectorAll('[data-shell-close]').forEach(function(el){
    el.addEventListener('click', function(){ setOpen(false); });
  });
  document.addEventListener('keydown', function(event){
    if(event.key === 'Escape' && nav.classList.contains('is-open')){ setOpen(false); }
  });
})();

/* Rail toggle from 1024px up (SHELL-003). The choice is remembered per
   browser; with no choice saved, the rail is the default on 1024-1279px. */
(function(){
  var toggle = document.querySelector('[data-rail-toggle]');
  if(!toggle){ return; }
  var body = document.body;
  function saved(){ try { return localStorage.getItem('hesba_sidebar'); } catch(e){ return null; } }
  function sync(){
    var rail = body.classList.contains('hs-rail');
    var label = rail ? toggle.dataset.labelExpand : toggle.dataset.labelCollapse;
    toggle.setAttribute('aria-expanded', rail ? 'false' : 'true');
    toggle.setAttribute('aria-label', label);
    toggle.setAttribute('title', label);
  }
  toggle.addEventListener('click', function(){
    var rail = !body.classList.contains('hs-rail');
    body.classList.toggle('hs-rail', rail);
    try { localStorage.setItem('hesba_sidebar', rail ? 'rail' : 'full'); } catch(e){}
    sync();
  });
  window.addEventListener('resize', function(){
    if(saved()){ return; }
    body.classList.toggle('hs-rail', window.innerWidth >= 1024 && window.innerWidth < 1280);
    sync();
  });
  sync();
})();
