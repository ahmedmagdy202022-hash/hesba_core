(function(){
  var params = new URLSearchParams(window.location.search);
  var stored = localStorage.getItem('hesba_lang');
  var lang = params.get('lang') === 'en' ? 'en' : (params.get('lang') === 'ar' ? 'ar' : (stored === 'en' ? 'en' : 'ar'));
  var isEn = lang === 'en';
  localStorage.setItem('hesba_lang', lang);
  document.documentElement.lang = lang;
  document.documentElement.dir = isEn ? 'ltr' : 'rtl';
  document.body.dataset.lang = lang;

  var exact = {
    en: {
      'حِسْبَة Core': 'Hesba Core',
      'فتح لوحة Admin': 'Open Admin',
      'خريطة تشغيل أول شاشة UI': 'First UI Screen Map',
      'شاشة بسيطة وآمنة للتنقل داخل حِسْبَة.': 'A simple and safe screen for navigating Hesba.',
      'شاشة بسيطة وآمنة للتنقل داخل حِسْبَة. الهدف منها ترتيب دورة العمل قبل بناء شاشات الإدخال الحقيقية، بدون تغيير أي منطق داتا أو حسابات مالية.': 'A simple and safe navigation screen. Its purpose is to organize the business cycle before building real input screens, without changing data or accounting logic.',
      'قواعد محمية في هذه المرحلة': 'Protected rules in this phase',
      'المبيعات لا تنشئ مستحقات للموردين.': 'Sales do not create supplier dues.',
      'المشتريات لا تنشئ مديونية للعملاء.': 'Purchases do not create customer dues.',
      'الخزن تتحرك بالمبلغ المدفوع فعليًا فقط.': 'Cashboxes move only by actual paid amounts.',
      'المخزون يتحرك من خلال حركات مخزون قابلة للتتبع.': 'Inventory moves through traceable stock movements.',
      'التقارير قراءة فقط وليست مكان إدخال بيانات.': 'Reports are read-only and are not an input area.',
      'Navigation Map فقط. الإدخال الفعلي من Admin مؤقتًا.': 'Navigation map only. Actual input is temporarily through Admin.',
      'هذه الشاشة Navigation Map فقط. الإدخال الفعلي ما زال من Admin لحد ما نثبت أول شاشة Transaction آمنة.': 'This is only a navigation map. Actual input remains in Admin until the first safe transaction screen is finalized.',
      'Dashboard Snapshot قراءة فقط': 'Read-only Dashboard Snapshot',
      'أول لقطة داشبورد آمنة بعد تسجيل الدخول.': 'First safe dashboard snapshot after login.',
      'Read-only Snapshot فقط. الربح والتكلفة تظهر بعد صلاحيات حقيقية.': 'Read-only snapshot only. Profit and cost appear after real permissions.',
      'مركز التقارير قراءة فقط': 'Read-only Report Hub',
      'تقارير لا تعدل أرصدة ولا تنشئ حركات.': 'Reports do not change balances or create movements.',
      'التقارير Read-only فقط.': 'Reports are read-only only.',
      'تقرير حالة آمن موسّع': 'Expanded Safe Status Report',
      'تقرير قراءة فقط بأعداد فعلية غير حساسة بعد تسجيل الدخول.': 'Read-only report with actual non-sensitive counts after login.',
      'Status آمن قبل تعميم الهوية': 'Safe status before applying the identity',
      'حالة الشاشة': 'Screen status',
      'قراءة فقط': 'Read only',
      'بدون أرصدة': 'No balances',
      'بدون تكلفة': 'No cost',
      'بدون ربح': 'No profit',
      'أعداد تشغيلية فقط': 'Operational counts only',
      'بدون مبالغ أو أرصدة': 'No amounts or balances',
      'التكلفة محمية': 'Cost protected',
      'الربح محمي': 'Profit protected',
      'دورة حِسْبَة الأساسية': 'Core Hesba cycle',
      'كل شاشة لازم تفضل مرتبطة بالدورة الكاملة': 'Every screen must stay connected to the full cycle',
      'قواعد الحماية': 'Protection rules',
      'هذه الشاشة Status Counts فقط. الهدف قياس وجود البيانات بدون كشف finance حساس.': 'This is only Status Counts. The goal is to verify data existence without exposing sensitive finance.',
      'التقرير قراءة فقط.': 'The report is read-only.',
      'لا يعرض مبالغ أو أرصدة مالية.': 'It does not show amounts or financial balances.',
      'لا يعرض تكلفة أو ربح.': 'It does not show cost or profit.',
      'لا ينشئ فواتير أو حركات مخزون أو حركات خزنة.': 'It does not create invoices, stock movements, or cashbox movements.',
      '١) البيانات الأساسية': '1) Master data',
      'تجهيز الموردين والعملاء والأصناف والمواقع والخزن قبل أي حركة.': 'Prepare suppliers, customers, items, locations, and cashboxes before any movement.',
      'الموردين': 'Suppliers',
      'العملاء': 'Customers',
      'الأصناف': 'Items',
      'المخازن / المواقع': 'Stores / Locations',
      'الخزن': 'Cashboxes',
      'طرف الشراء فقط': 'Purchase side only',
      'طرف البيع فقط': 'Sales side only',
      'كود / اسم / تكلفة محمية': 'Code / name / protected cost',
      'المخزون = صنف + موقع': 'Inventory = item + location',
      'تتأثر بالمدفوع فقط': 'Affected only by paid amounts',
      '٢) الشراء من المورد': '2) Purchase from supplier',
      'فاتورة شراء متعددة السطور تزود المخزون وتثبت مستحق المورد فقط بالمتبقي.': 'Multi-line purchase invoice increases inventory and records only remaining supplier due.',
      'فواتير الشراء': 'Purchase invoices',
      'سطور الشراء': 'Purchase lines',
      'مدفوعات الموردين': 'Supplier payments',
      'تقلل مستحق المورد': 'Reduces supplier due',
      '٣) المخزون حسب الموقع': '3) Inventory by location',
      'أي زيادة أو نقص مخزون لازم يظهر كحركة قابلة للتتبع.': 'Any stock increase or decrease must appear as a traceable movement.',
      'حركات المخزون': 'Stock movements',
      'تقرير المخزون': 'Inventory report',
      'شراء / بيع / تحويل / تسوية': 'Purchase / sale / transfer / adjustment',
      'قراءة فقط من الحركات': 'Read-only from movements',
      '٤) البيع للعميل': '4) Sale to customer',
      'فاتورة بيع متعددة السطور تخصم المخزون وتثبت مديونية العميل فقط بالمتبقي.': 'Multi-line sales invoice decreases inventory and records only remaining customer due.',
      'فواتير البيع': 'Sales invoices',
      'سطور البيع': 'Sales lines',
      'مدفوعات العملاء': 'Customer payments',
      'تكلفة وربح محميين': 'Cost and profit protected',
      'تقلل مديونية العميل': 'Reduces customer due',
      '٥) الخزنة والتقارير': '5) Cashbox and reports',
      'الخزنة تتأثر بالمبلغ المدفوع فعليًا فقط، والتقارير قراءة فقط.': 'Cashbox is affected only by actual paid amount, and reports are read-only.',
      'حركات الخزن': 'Cashbox movements',
      'ملخص قراءة فقط': 'Read-only summary',
      'مركز التقارير': 'Report hub',
      'أعداد آمنة': 'Safe counts',
      '١) حالة دورة العمل': '1) Business cycle status',
      'الدورة الأساسية جاهزة كمسار واحد قابل للتوسع.': 'The core cycle is ready as one scalable path.',
      'الدورة الكاملة': 'Full cycle',
      'مورد → شراء → مخزون → بيع → عميل → خزنة → تقارير': 'Supplier → Purchase → Inventory → Sale → Customer → Cashbox → Reports',
      'حالة الإدخال': 'Input status',
      'Admin مؤقتًا حتى شاشة Transaction آمنة': 'Temporarily Admin until a safe transaction screen',
      '٢) KPIs آمنة لاحقًا': '2) Safe KPIs later',
      'تجهيز أماكن الأرقام بدون عرض ربح أو تكلفة قبل الصلاحيات.': 'Prepare number areas without showing profit or cost before permissions.',
      'عدد الموردين': 'Supplier count',
      'عدد العملاء': 'Customer count',
      'حالة المخزون': 'Inventory status',
      'حالة الخزن': 'Cashbox status',
      '٣) حماية الأرقام الحساسة': '3) Sensitive number protection',
      'التكلفة والربح لا يظهروا قبل صلاحيات حقيقية.': 'Cost and profit do not appear before real permissions.',
      'الربح': 'Profit',
      'التكلفة': 'Cost',
      '٤) الخطوة الجاية': '4) Next step',
      'ربط أرقام قراءة فقط بعد ثبات reports/views والمايجريشن.': 'Connect read-only numbers after reports/views and migrations are stable.',
      'تقارير Read-only': 'Read-only reports',
      'تقرير Status': 'Status report',
      '١) تقارير الأطراف': '1) Party reports',
      'أرصدة العملاء والموردين تأتي من الفواتير والمدفوعات والمرتجعات فقط.': 'Customer and supplier balances come only from invoices, payments, and returns.',
      'مبيعات + مدفوعات عملاء فقط': 'Sales + customer payments only',
      'مشتريات + مدفوعات موردين فقط': 'Purchases + supplier payments only',
      '٢) تقارير الفواتير': '2) Invoice reports',
      'الفواتير Header + Lines، والحسابات لا تتغير من التقرير.': 'Invoices are header + lines, and accounts do not change from reports.',
      'مبيعات مدفوعة / جزئية / آجلة': 'Paid / partial / credit sales',
      'مشتريات مدفوعة / جزئية / آجلة': 'Paid / partial / credit purchases',
      'أعداد فعلية غير حساسة': 'Actual non-sensitive counts',
      '٣) تقارير التشغيل': '3) Operations reports',
      'المخزون والخزن مبنيين على حركات فعلية قابلة للتتبع.': 'Inventory and cashboxes are built on actual traceable movements.',
      'Paid_Now فقط': 'Paid_Now only',
      '٤) تقارير الربح المحمية': '4) Protected profit reports',
      'الربح = المبيعات - تكلفة البضاعة المباعة، ويظهر بعد صلاحيات حقيقية.': 'Profit = sales - cost of goods sold, and appears after real permissions.',
      'Owner permission required': 'Owner permission required'
    }
  };
  exact.ar = {};
  Object.keys(exact.en).forEach(function(k){ exact.ar[exact.en[k]] = k; });

  function normalize(s){ return (s || '').replace(/\s+/g,' ').trim(); }
  function translateTextNodes(root){
    var map = exact[lang] || {};
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode:function(node){
        var p = node.parentElement;
        if(!p || ['SCRIPT','STYLE','TEXTAREA','INPUT','OPTION'].indexOf(p.tagName) >= 0) return NodeFilter.FILTER_REJECT;
        return normalize(node.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    var nodes = [];
    while(walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(function(node){
      var text = normalize(node.nodeValue);
      if(map[text]) node.nodeValue = node.nodeValue.replace(text, map[text]);
    });
  }

  function ensureSwitcher(){
    if(document.querySelector('.hesba-global-lang')) return;
    var nav = document.createElement('nav');
    nav.className = 'hesba-global-lang';
    nav.setAttribute('aria-label', isEn ? 'Language' : 'اختيار اللغة');
    nav.innerHTML = '<a data-lang-option="ar" href="#">عربي</a><a data-lang-option="en" href="#">English</a>';
    document.body.insertBefore(nav, document.body.firstChild);
  }

  function addStyle(){
    if(document.getElementById('hesba-i18n-style')) return;
    var style = document.createElement('style');
    style.id = 'hesba-i18n-style';
    style.textContent = '.hesba-global-lang{position:fixed;z-index:9999;top:max(10px,env(safe-area-inset-top));left:max(10px,env(safe-area-inset-left));display:flex;gap:4px;padding:4px;border-radius:999px;background:rgba(255,255,255,.80);backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);box-shadow:0 12px 28px rgba(5,38,67,.12);direction:ltr}.hesba-global-lang a{display:block;text-decoration:none;color:#526173;font:900 13px/1 Tahoma,Arial,sans-serif;padding:10px 13px;border-radius:999px}.hesba-global-lang a.active{background:#052643;color:#fff}body[data-lang="en"]{direction:ltr}body[data-lang="en"] .cycle-steps{direction:ltr}body[data-lang="en"] .rules ul{padding:0 0 0 22px}body[data-lang="en"] .arrow{transform:scaleX(-1)}@media(max-width:520px){.hesba-global-lang{transform:scale(.9);transform-origin:left top}}';
    document.head.appendChild(style);
  }

  function setSwitcherLinks(){
    document.querySelectorAll('[data-lang-option]').forEach(function(a){
      var target = a.dataset.langOption;
      a.classList.toggle('active', target === lang);
      var u = new URL(window.location.href);
      u.searchParams.set('lang', target);
      a.href = u.pathname + u.search + u.hash;
      a.addEventListener('click', function(){ localStorage.setItem('hesba_lang', target); });
    });
  }

  function carryLangInLinks(){
    document.querySelectorAll('a[href]').forEach(function(a){
      var href = a.getAttribute('href');
      if(!href || href.startsWith('#') || href.startsWith('mailto:') || href.startsWith('tel:') || href.startsWith('javascript:')) return;
      try{
        var u = new URL(href, window.location.origin);
        if(u.origin !== window.location.origin) return;
        if(u.pathname.startsWith('/admin/')) return;
        u.searchParams.set('lang', lang);
        a.setAttribute('href', u.pathname + u.search + u.hash);
      }catch(e){}
    });
  }

  document.addEventListener('DOMContentLoaded', function(){
    addStyle();
    ensureSwitcher();
    setSwitcherLinks();
    translateTextNodes(document.body);
    carryLangInLinks();
    if(isEn && document.title.indexOf('حِسْبَة') >= 0) document.title = document.title.replace('حِسْبَة Core', 'Hesba Core');
  });
})();
