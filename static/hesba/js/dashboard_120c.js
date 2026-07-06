(function () {
  var params = new URLSearchParams(window.location.search);
  var saved = localStorage.getItem('hesba_dashboard_lang');
  var lang = params.get('lang') === 'en' ? 'en' : (params.get('lang') === 'ar' ? 'ar' : (saved === 'en' ? 'en' : 'ar'));

  var dict = {
    ar: {
      title: 'لوحة التحكم - حِسْبَة', menuAria: 'القائمة', notificationsAria: 'التنبيهات', language: 'العربية', dashboardTitle: 'لوحة التحكم', userName: 'أحمد محمد', userRole: 'صاحب الحساب', heroTitle: 'صباح الخير، أحمد', heroLead: 'أداء أعمالك في تحسن مستمر، استمر بنفس الزخم!', statusPill: 'أداء ممتاز!', healthTitle: 'مؤشر صحة الأعمال', healthSales: 'المبيعات', healthProfit: 'الربحية', healthCash: 'التدفقات النقدية', healthSatisfaction: 'رضا العملاء', scoreFrom: 'من 100', kpiSales: 'مبيعات اليوم', kpiProfit: 'صافي الربح', kpiCashbox: 'رصيد الصندوق', kpiReceivables: 'مستحقات العملاء', kpiPayables: 'مستحقات الموردين', kpiExpenses: 'مصروفات اليوم', currency: 'ريال سعودي', thanYesterday: 'عن أمس', alertsTitle: 'التنبيهات الذكية', urgent: 'عاجل', medium: 'متوسطة', info: 'معلومة', alertOne: 'رصيد الخزنة الرئيسي منخفض، تحديث قبل 35 دقيقة.', alertTwo: '3 فواتير عملاء مستحقة اليوم بقيمة 8,750 ريال.', alertThree: '12 صنفًا على وشك نفاد المخزون، تحقق من المخزون.', viewAlerts: 'عرض جميع التنبيهات', actionsTitle: 'إجراءات سريعة', actionInvoice: 'فاتورة جديدة', actionCustomer: 'إضافة عميل', actionSupplier: 'مورد جديد', actionRegister: 'تسجيل عملية', actionCashbox: 'حركة خزنة', actionReport: 'طباعة تقرير', chartSales: 'اتجاه المبيعات', lastSeven: 'آخر 7 أيام', fullReport: 'عرض التقرير الكامل', chartCashCredit: 'نقدي مقابل آجل', cash: 'نقدي', credit: 'آجل', details: 'عرض التفاصيل', chartTop: 'أعلى المنتجات / الخدمات', topOne: 'خدمة تصميم', topTwo: 'منتج ب', topThree: 'منتج ج', topFour: 'خدمة استشارية', topFive: 'منتج د', allProducts: 'عرض جميع المنتجات', chartCustomers: 'مستحقات العملاء', current: 'جارية', late: 'متأخرة', ended: 'منتهية', mockReady: 'منطقة جاهزة للإخفاء حسب الصلاحيات لاحقًا', chartSuppliers: 'مستحقات الموردين', invoice: 'قائمة', mockOnly: 'بيانات وهمية للمعاينة فقط', chartInventory: 'المخزون والمصروفات', lowStock: 'قرب النفاد', expenses: 'مصروفات', inventoryValue: 'قيمة المخزون', protectedValue: 'محمي', permissionReady: 'جاهز لإخفاء التكلفة والهامش وقيمة المخزون حسب الصلاحيات.', onboardingTitle: 'ابدأ تجربة حسبة في 4 خطوات', onboardingLead: 'قم بإعداد حسابك وربط أعمالك بسهولة.', stepBusiness: 'بيانات نشاطك', stepParties: 'عملاء وموردين', stepProducts: 'منتجات وخدمات', stepFirst: 'أول عملية', startNow: 'ابدأ الآن'
    },
    en: {
      title: 'Dashboard - Hesba', menuAria: 'Menu', notificationsAria: 'Notifications', language: 'English', dashboardTitle: 'Dashboard', userName: 'Ahmed Mohamed', userRole: 'Account owner', heroTitle: 'Good morning, Ahmed', heroLead: 'Your business performance is improving. Keep the momentum!', statusPill: 'Excellent performance!', healthTitle: 'Business Health Score', healthSales: 'Sales', healthProfit: 'Profitability', healthCash: 'Cash flow', healthSatisfaction: 'Customer satisfaction', scoreFrom: 'out of 100', kpiSales: 'Today sales', kpiProfit: 'Net profit', kpiCashbox: 'Cashbox balance', kpiReceivables: 'Customer receivables', kpiPayables: 'Supplier payables', kpiExpenses: 'Today expenses', currency: 'SAR', thanYesterday: 'vs yesterday', alertsTitle: 'Smart alerts', urgent: 'Urgent', medium: 'Medium', info: 'Info', alertOne: 'Main cashbox balance is low, updated 35 minutes ago.', alertTwo: '3 customer invoices are due today for SAR 8,750.', alertThree: '12 items are close to stockout. Check inventory.', viewAlerts: 'View all alerts', actionsTitle: 'Quick actions', actionInvoice: 'New invoice', actionCustomer: 'Add customer', actionSupplier: 'New supplier', actionRegister: 'Register transaction', actionCashbox: 'Cashbox movement', actionReport: 'Print report', chartSales: 'Sales trend', lastSeven: 'Last 7 days', fullReport: 'View full report', chartCashCredit: 'Cash vs credit', cash: 'Cash', credit: 'Credit', details: 'View details', chartTop: 'Top products / services', topOne: 'Design service', topTwo: 'Product B', topThree: 'Product C', topFour: 'Consulting service', topFive: 'Product D', allProducts: 'View all products', chartCustomers: 'Customer receivables', current: 'Current', late: 'Late', ended: 'Overdue', mockReady: 'Ready for later permission-based hiding.', chartSuppliers: 'Supplier payables', invoice: 'Invoice', mockOnly: 'Mock data for preview only', chartInventory: 'Inventory and expenses', lowStock: 'Low stock', expenses: 'Expenses', inventoryValue: 'Inventory value', protectedValue: 'Protected', permissionReady: 'Ready to hide cost, margin, and inventory value by permission.', onboardingTitle: 'Start Hesba in 4 steps', onboardingLead: 'Set up your account and connect your business easily.', stepBusiness: 'Business data', stepParties: 'Customers & suppliers', stepProducts: 'Products & services', stepFirst: 'First transaction', startNow: 'Start now'
    }
  };

  function updateClock() {
    var now = new Date();
    var locale = lang === 'en' ? 'en-GB' : 'ar-EG';
    var time = now.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
    var longDate = now.toLocaleDateString(locale, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    var shortDate = now.toLocaleDateString(locale, { day: 'numeric', month: 'short', year: 'numeric' });
    document.querySelectorAll('[data-time], [data-time-copy]').forEach(function (node) { node.textContent = time; });
    document.querySelectorAll('[data-date]').forEach(function (node) { node.textContent = longDate; });
    document.querySelectorAll('[data-date-short]').forEach(function (node) { node.textContent = shortDate; });
  }

  function applyLanguage(nextLang) {
    lang = nextLang === 'en' ? 'en' : 'ar';
    localStorage.setItem('hesba_dashboard_lang', lang);
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === 'en' ? 'ltr' : 'rtl';
    document.body.dataset.lang = lang;
    document.title = dict[lang].title;
    document.querySelectorAll('[data-i18n]').forEach(function (node) {
      var key = node.getAttribute('data-i18n');
      if (dict[lang][key]) node.textContent = dict[lang][key];
    });
    document.querySelectorAll('[data-i18n-aria]').forEach(function (node) {
      var key = node.getAttribute('data-i18n-aria');
      if (dict[lang][key]) node.setAttribute('aria-label', dict[lang][key]);
    });
    document.querySelectorAll('[data-dashboard-link]').forEach(function (link) {
      link.href = '/dashboard/?lang=' + lang;
    });
    updateClock();
    var url = new URL(window.location.href);
    url.searchParams.set('lang', lang);
    window.history.replaceState({}, '', url.pathname + url.search + url.hash);
  }

  document.querySelectorAll('[data-lang-toggle]').forEach(function (button) {
    button.addEventListener('click', function () { applyLanguage(lang === 'ar' ? 'en' : 'ar'); });
  });
  applyLanguage(lang);
  setInterval(updateClock, 60000);
}());
