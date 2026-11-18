/* ============================================================================
 * start/catalog.js — каталог владельца: товары, услуги, объекты, позиции меню (ветка 1811-niche-admin-speed).
 * ОДИН код для Старта (start-bot.html → «База знаний» → каталог) и Profi (agent-knowledge.html → «Каталог»):
 * коллекции, плитки и таблица, фильтры и сортировка, готовность к показу, массовые действия, правка цены и
 * остатка прямо в таблице, позиции из фото и вставкой списком, окно позиции (размеры и цвета с остатком,
 * скидка, фото, характеристики, превью карточки), справочник коллекций. Ниша (localStorage.vf_business)
 * меняет слова и поля: «Услуги» с длительностью, «Букеты», «Объекты», «Автомобили» и т.д.
 *
 * Подключение: StartCatalog.init({ kb, reload }) — kb: { entries, collections, storage, files } (тот же объект,
 * что у страницы; читается в момент вызова), reload(): перезагрузить данные и перерисовать. Дальше
 * StartCatalog.draw(box). Данные — /api/agent/knowledge* через StartApp.api (у Profi — с agent_id).
 * ========================================================================== */
(function () {
  'use strict';
  var S = window.StartApp;
  var esc = S.esc, icon = S.icon;
  var kb = { entries: [], collections: [], storage: true, files: [] };
  var loadKb = function () { return Promise.resolve(); };
  var catOpts = {};   // sheetHref — куда ведёт «Из таблицы» (у Старта раздел бота #s-sheet, у Profi — настройки агента)

  var CURRENCIES = [['KGS', 'сом'], ['USD', 'долл.'], ['RUB', 'руб.'], ['KZT', 'тенге'], ['EUR', 'евро']];
  function curWord(c) {
    var f = CURRENCIES.filter(function (x) { return x[0] === (c || 'KGS'); })[0];
    return f ? f[1] : c;
  }
  function priceText(e) { return e.price != null && e.price !== '' ? Number(e.price).toLocaleString('ru-RU') + ' ' + curWord(e.currency) : 'цена не указана'; }

  function mediaThumb(url, isVideo) {
    if (!isVideo) return '<img src="' + esc(url) + '" alt="" loading="lazy">';
    return '<video class="st-vthumb" src="' + esc(url) + '#t=0.1" preload="metadata" muted playsinline onerror="this.style.visibility=\'hidden\'"></video>' +
      '<span class="st-play">' + icon('play') + '</span>';
  }

  // Просмотр всех фото и видео позиции каталога.
  function mediaViewer(e) {
    var list = (e && e.media) || [];
    if (!list.length) return;
    var d = S.dialog({
      title: e.title || 'Фото и видео', size: 'lg',
      body: '<div class="st-viewer">' + list.map(function (m) {
        return m.is_video
          ? '<video src="' + esc(m.url) + '" controls playsinline preload="metadata"></video>'
          : '<img src="' + esc(m.url) + '" alt="">';
      }).join('') + '</div><div class="hint" style="margin-top:8px">Если видео не играет в браузере (например, .mov с iPhone), клиенту оно всё равно уйдёт файлом. Для просмотра на сайте лучше MP4.</div>',
      foot: '<button class="btn" type="button" data-c>Закрыть</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
  }

  // ══════════════════════════════ КАТАЛОГ КАК У МАГАЗИНА (13.11.2026) ══════════════════════
  // Коллекции, поиск, фильтры, плитки или таблица, остаток по размерам и цветам, массовые действия,
  // правка цены и остатка прямо в таблице, позиции из пачки фото, вставка списком, подсказки и превью
  // карточки, которую бот пришлёт клиенту. Владельцы (норка, детская одежда) путались в куче крупных
  // плиток и не понимали, что уже заполнено.
  var NO_COL = '__none';
  var CAT_PAGE = 60;
  var LOW = 3;
  // Каталог под нишу владельца (ветка 1811-niche-admin-speed): название («Услуги», «Букеты», «Объекты»),
  // наборы размеров/вариантов, цвета, быстрые характеристики, учёт остатка или длительность услуги —
  // из /api/users/me → business.catalog (sidebar.js кладёт его в localStorage.vf_business).
  // Ниша не выбрана — как раньше (магазин одежды).
  var BIZ = (function () { try { return JSON.parse(localStorage.getItem('vf_business') || 'null'); } catch (e) { return null; } })();
  var CATCFG = (BIZ && BIZ.niche && BIZ.catalog) || {};
  var CAT_TITLE = CATCFG.title || 'Каталог';
  var CAT_ONE = CATCFG.one || 'позиция';
  var SHOW_SIZES = !CATCFG.kind || !!CATCFG.variants_label;
  var SIZES_LABEL = CATCFG.variants_label || 'Размеры';
  var SHOW_STOCK = !CATCFG.kind || CATCFG.stock !== false;
  var SHOW_DURATION = !!CATCFG.duration;
  var SIZE_PRESETS = (CATCFG.size_presets && CATCFG.size_presets.length) ? CATCFG.size_presets : [
    ['Одежда XS–XXL', ['XS', 'S', 'M', 'L', 'XL', 'XXL']],
    ['Одежда 42–56', ['42', '44', '46', '48', '50', '52', '54', '56']],
    ['Головные уборы 54–62', ['54', '55', '56', '57', '58', '59', '60', '61', '62']],
    ['Детская по росту', ['80', '86', '92', '98', '104', '110', '116', '122', '128', '134', '140', '146', '152']],
    ['Обувь 35–45', ['35', '36', '37', '38', '39', '40', '41', '42', '43', '44', '45']]
  ];
  var COLOR_QUICK = (CATCFG.colors && CATCFG.colors.length) ? CATCFG.colors :
    (CATCFG.kind ? [] : ['чёрный', 'белый', 'серый', 'коричневый', 'бежевый', 'синий', 'красный', 'розовый', 'зелёный']);
  var ATTR_QUICK = (CATCFG.attr_keys && CATCFG.attr_keys.length) ? CATCFG.attr_keys : ['Цвет', 'Материал', 'Сезон', 'Пол', 'Возраст', 'Бренд', 'Страна'];
  var CAT_HINT = {
    services: 'Услуги с ценой, длительностью, фото и описанием. Бот называет цену и длительность и записывает клиента.',
    objects: 'Объекты с ценой, районом, площадью, фото и видео. Бот подбирает их по запросу клиента и присылает карточкой.',
    cars: 'Автомобили с ценой под ключ, годом, пробегом, фото и видео. Бот подбирает их по запросу клиента.',
    menu: 'Блюда с ценой, составом и фото. Бот принимает заказ по меню.',
    courses: 'Курсы с ценой, расписанием и форматом. Бот рассказывает и записывает на пробный урок.',
    tours: 'Туры с датами, ценой и тем, что входит. Бот подбирает тур под запрос клиента.',
    offers: 'Ваши услуги и предложения с ценой и описанием. Бот отвечает по ним клиентам.'
  }[CATCFG.kind] || 'Товары с ценой, размерами, остатком, фото и видео. Бот показывает их клиентам карточкой: фото, цена, скидка, размеры в наличии.';
  var CAT_FILTERS = [['all', 'Все позиции'], ['in', 'В наличии'], ['out', 'Нет в наличии'], ['low', 'Мало осталось (до 3 шт.)'],
    ['sale', 'Со скидкой'], ['nophoto', 'Без фото'], ['noprice', 'Без цены'], ['off', 'Выключенные']];
  var CAT_SORTS = [['new', 'Сначала новые'], ['name', 'По названию'], ['price_up', 'Дешевле'], ['price_down', 'Дороже'], ['stock', 'Меньше остаток']];
  var cat = (function () {
    var d = { col: '', q: '', filter: 'all', sort: 'new', view: 'grid', shown: CAT_PAGE, sel: {} };
    try { var x = JSON.parse(localStorage.getItem('vf_catalog_view') || '{}'); d.view = x.view === 'list' ? 'list' : 'grid'; d.sort = x.sort || 'new'; } catch (e) {}
    return d;
  })();
  function catSave() { try { localStorage.setItem('vf_catalog_view', JSON.stringify({ view: cat.view, sort: cat.sort })); } catch (e) {} }
  function catProducts() { return kb.entries.filter(function (e) { return e.kind === 'product'; }); }
  function catById(id) { return kb.entries.filter(function (x) { return x.id === id; })[0]; }
  function catCollections() {
    var map = {};
    catProducts().forEach(function (e) { var c = (e.collection || '').trim(); if (c) map[c] = (map[c] || 0) + 1; });
    // Порядок — справочник владельца (пустые коллекции тоже показываем), потом найденные у товаров
    var order = (kb.collections || []).slice();
    Object.keys(map).sort(function (a, b) { return a.localeCompare(b, 'ru'); }).forEach(function (k) { if (order.indexOf(k) < 0) order.push(k); });
    return order.map(function (k) { return [k, map[k] || 0]; });
  }
  function articleOf(e) {
    var a = e.attributes || {};
    for (var k in a) if (/^(артикул|код|sku)$/i.test(k)) return String(a[k] || '');
    return '';
  }
  function money(v, c) { return Number(v).toLocaleString('ru-RU') + ' ' + curWord(c); }
  function hasPrice(e) { return e.price != null && e.price !== ''; }
  function hasPhoto(e) { return (e.media || []).length > 0 || !!(e.attributes || {})._photo_url; }
  function discount(e) {
    if (!e.old_price || !hasPrice(e) || Number(e.old_price) <= Number(e.price)) return 0;
    return Math.round((1 - Number(e.price) / Number(e.old_price)) * 100);
  }
  function sizesInStock(e) {
    var v = e.variants || [];
    if (v.length) {
      var out = [];
      v.forEach(function (x) { if (Number(x.qty) > 0 && x.size && out.indexOf(x.size) < 0) out.push(x.size); });
      return out;
    }
    return e.in_stock === false ? [] : (e.sizes || []);
  }
  function stockBadge(e) {
    if (e.in_stock === false) return '<span class="st-cbadge out">нет в наличии</span>';
    if (e.stock_qty != null && e.stock_qty <= LOW) return '<span class="st-cbadge low">осталось ' + e.stock_qty + ' шт.</span>';
    if (e.stock_qty != null) return '<span class="st-cbadge ok">' + e.stock_qty + ' шт.</span>';
    return '';
  }
  function catVisible() {
    var q = cat.q.trim().toLowerCase();
    var list = catProducts().filter(function (e) {
      if (cat.col === NO_COL) { if ((e.collection || '').trim()) return false; }
      else if (cat.col && (e.collection || '').trim() !== cat.col) return false;
      switch (cat.filter) {
        case 'in': if (e.in_stock === false) return false; break;
        case 'out': if (e.in_stock !== false) return false; break;
        case 'low': if (!(e.stock_qty != null && e.stock_qty > 0 && e.stock_qty <= LOW)) return false; break;
        case 'sale': if (!discount(e)) return false; break;
        case 'nophoto': if (hasPhoto(e)) return false; break;
        case 'noprice': if (hasPrice(e)) return false; break;
        case 'off': if (e.is_active !== false) return false; break;
      }
      if (!q) return true;
      var hay = [e.title, e.body, e.keywords, e.collection, (e.sizes || []).join(' '), articleOf(e),
        (e.variants || []).map(function (v) { return v.color || ''; }).join(' '),
        Object.keys(e.attributes || {}).filter(function (k) { return k.charAt(0) !== '_'; }).map(function (k) { return e.attributes[k]; }).join(' ')
      ].join(' ').toLowerCase();
      return q.split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
    });
    var num = function (v) { return v == null || v === '' ? null : Number(v); };
    list.sort(function (a, b) {
      switch (cat.sort) {
        case 'name': return (a.title || '').localeCompare(b.title || '', 'ru');
        case 'price_up': return (num(a.price) == null) - (num(b.price) == null) || (num(a.price) || 0) - (num(b.price) || 0);
        case 'price_down': return (num(a.price) == null) - (num(b.price) == null) || (num(b.price) || 0) - (num(a.price) || 0);
        case 'stock': return (a.stock_qty == null) - (b.stock_qty == null) || (a.stock_qty || 0) - (b.stock_qty || 0);
        default: return String(b.created_at || b.updated_at || '').localeCompare(String(a.created_at || a.updated_at || ''));
      }
    });
    return list;
  }
  function selIds() { return Object.keys(cat.sel).filter(function (id) { return cat.sel[id] && catById(id); }); }

  function drawCatalog(box) {
    var all = catProducts();
    var cols = catCollections();
    var noCol = all.filter(function (e) { return !(e.collection || '').trim(); }).length;
    if (cat.col && cat.col !== NO_COL && !cols.some(function (c) { return c[0] === cat.col; })) cat.col = '';
    var chip = function (key, label, n) {
      return '<button class="st-colchip' + (cat.col === key ? ' on' : '') + '" type="button" data-col="' + esc(key) + '">' + esc(label) +
        '<span class="n">' + n + '</span></button>';
    };
    var curCol = cat.col && cat.col !== NO_COL ? cat.col : '';
    box.innerHTML =
      '<div class="st-cathead"><div><b>' + esc(CAT_TITLE) + '</b><span class="muted small">' + esc(CAT_HINT) + '</span></div>' +
      '<div class="st-catadd"><button class="btn btn-sm" id="cat-photos" type="button" title="Каждое фото станет позицией: потом допишете цены прямо в таблице">' + icon('image') + '<span>Позиции из фото</span></button>' +
      '<button class="btn btn-sm" id="cat-paste" type="button" title="Название; цена; размеры; количество — по строке на товар">' + icon('list') + '<span>Вставить списком</span></button>' +
      '<a class="btn btn-sm" href="' + esc(catOpts.sheetHref || '#s-sheet') + '" title="Импорт из Google Таблицы">' + icon('layers') + '<span>Из таблицы</span></a>' +
      '<button class="btn btn-primary btn-sm" id="kb-add" type="button">' + icon('plus') + '<span>Добавить' + (curCol ? ' в «' + esc(curCol) + '»' : ' позицию') + '</span></button></div></div>' +
      '<div class="st-colbar">' + chip('', 'Все', all.length) + cols.map(function (c) { return chip(c[0], c[0], c[1]); }).join('') +
      (cols.length && noCol ? chip(NO_COL, 'Без коллекции', noCol) : '') +
      '<button class="st-colchip add" type="button" id="cat-newcol">' + icon('plus') + 'Коллекция</button>' +
      '<button class="btn btn-ghost btn-sm" type="button" id="cat-colmenu" title="Создать, переименовать, убрать коллекции и поменять порядок">' + icon('settings') + '<span>Коллекции</span></button>' +
      '</div>' +
      '<div id="cat-health"></div>' +
      '<div class="st-cattools"><div class="st-search">' + icon('search') + '<input class="input" id="cat-q" placeholder="Найти: название, размер, цвет, артикул" value="' + esc(cat.q) + '"></div>' +
      '<select class="select-native" id="cat-filter">' + CAT_FILTERS.map(function (f) { return '<option value="' + f[0] + '"' + (cat.filter === f[0] ? ' selected' : '') + '>' + f[1] + '</option>'; }).join('') + '</select>' +
      '<select class="select-native" id="cat-sort">' + CAT_SORTS.map(function (f) { return '<option value="' + f[0] + '"' + (cat.sort === f[0] ? ' selected' : '') + '>' + f[1] + '</option>'; }).join('') + '</select>' +
      '<div class="st-seg"><button type="button" data-cview="grid" class="' + (cat.view === 'grid' ? 'on' : '') + '" title="Плитки">' + icon('layout-grid') + '</button>' +
      '<button type="button" data-cview="list" class="' + (cat.view === 'list' ? 'on' : '') + '" title="Таблица: цену и остаток можно править прямо в ней">' + icon('list') + '</button></div></div>' +
      '<div class="st-catsel"><label class="st-chk"><input type="checkbox" id="cat-selall"><span>Выбрать все на экране</span></label><span id="cat-stats" class="muted small"></span></div>' +
      '<div id="cat-items"></div><div id="cat-bulk"></div>';

    S.$('#kb-add').addEventListener('click', function () { productDialog(null, curCol ? { collection: curCol } : null); });
    S.$('#cat-photos').addEventListener('click', function () { productsFromPhotos(curCol); });
    S.$('#cat-paste').addEventListener('click', function () { pasteDialog(curCol); });
    S.$$('[data-col]', box).forEach(function (b) { b.addEventListener('click', function () { cat.col = b.dataset.col; cat.shown = CAT_PAGE; drawCatalog(box); }); });
    S.$('#cat-newcol').addEventListener('click', function () { newCollectionDialog(box); });
    var cm = S.$('#cat-colmenu'); if (cm) cm.addEventListener('click', function () { collectionsManager(); });
    var qi = S.$('#cat-q');
    qi.addEventListener('input', S.debounce(function () { cat.q = qi.value; cat.shown = CAT_PAGE; drawCatItems(); }, 150));
    S.$('#cat-filter').addEventListener('change', function (e) { cat.filter = e.target.value; cat.shown = CAT_PAGE; drawCatItems(); });
    S.$('#cat-sort').addEventListener('change', function (e) { cat.sort = e.target.value; catSave(); drawCatItems(); });
    S.$$('[data-cview]', box).forEach(function (b) {
      b.addEventListener('click', function () { cat.view = b.dataset.cview; catSave(); S.$$('[data-cview]', box).forEach(function (x) { x.classList.toggle('on', x === b); }); drawCatItems(); });
    });
    S.$('#cat-selall').addEventListener('change', function () {
      var on = this.checked;
      catVisible().slice(0, cat.shown).forEach(function (e) { if (on) cat.sel[e.id] = true; else delete cat.sel[e.id]; });
      drawCatItems();
    });
    drawHealth();
    drawCatItems();
  }

  // Что ещё заполнить — с кнопкой, которая сразу показывает эти позиции
  function drawHealth() {
    var box = S.$('#cat-health'); if (!box) return;
    var all = catProducts().filter(function (e) { return e.is_active !== false; });
    if (!all.length) { box.innerHTML = ''; return; }
    var tips = [];
    var n = all.filter(function (e) { return !hasPhoto(e); }).length;
    if (n) tips.push(['nophoto', n + ' без фото', 'бот не сможет показать их клиенту']);
    n = all.filter(function (e) { return !hasPrice(e); }).length;
    if (n) tips.push(['noprice', n + ' без цены', 'бот не назовёт цену']);
    n = all.filter(function (e) { return e.in_stock === false; }).length;
    if (n) tips.push(['out', n + ' нет в наличии', 'бот скажет, что их нет']);
    n = all.filter(function (e) { return e.stock_qty != null && e.stock_qty > 0 && e.stock_qty <= LOW; }).length;
    if (n) tips.push(['low', n + ' заканчиваются', 'осталось до 3 шт.']);
    var ready = all.filter(function (e) { return hasPhoto(e) && hasPrice(e); }).length;
    var pct = Math.round(ready * 100 / all.length);
    box.innerHTML = '<div class="st-health"><div class="st-health-ring" style="--p:' + pct + '"><span>' + pct + '%</span></div>' +
      '<div class="st-health-t"><b>' + (pct === 100 ? 'Каталог готов: у всех позиций есть фото и цена' : 'Готово к показу клиентам: ' + ready + ' из ' + all.length) + '</b>' +
      (tips.length ? '<div class="st-health-tips">' + tips.map(function (t) {
        return '<button type="button" data-hf="' + t[0] + '" class="' + (cat.filter === t[0] ? 'on' : '') + '"><b>' + esc(t[1]) + '</b> · ' + esc(t[2]) + '</button>';
      }).join('') + '</div>' : '<span class="muted small">Бот покажет любую позицию карточкой с фото и ценой.</span>') + '</div></div>';
    S.$$('[data-hf]', box).forEach(function (b) {
      b.addEventListener('click', function () {
        cat.filter = cat.filter === b.dataset.hf ? 'all' : b.dataset.hf;
        var f = S.$('#cat-filter'); if (f) f.value = cat.filter;
        cat.shown = CAT_PAGE; drawHealth(); drawCatItems();
      });
    });
  }

  function drawCatItems() {
    var wrap = S.$('#cat-items'); if (!wrap) return;
    var all = catProducts();
    var list = catVisible();
    S.$('#cat-stats').textContent = all.length ? 'Показано ' + Math.min(list.length, cat.shown) + ' из ' + list.length : '';
    drawBulkBar();
    var sa = S.$('#cat-selall');
    if (sa) {
      var page0 = list.slice(0, cat.shown);
      sa.checked = page0.length > 0 && page0.every(function (e) { return cat.sel[e.id]; });
      sa.parentNode.style.visibility = list.length ? '' : 'hidden';
    }
    if (!all.length) {
      wrap.innerHTML = '<div class="st-catempty">' + icon('package') + '<b>Каталог пуст</b><span>Самый быстрый способ — «Позиции из фото»: выберите сразу все фото товаров, каждое станет позицией, а цены и размеры допишете в таблице. Или «Вставить списком» из Excel и WhatsApp.</span></div>';
      return;
    }
    if (!list.length) {
      wrap.innerHTML = '<div class="st-empty">' + (cat.col && cat.col !== NO_COL && !cat.q && cat.filter === 'all'
        ? 'В коллекции «' + esc(cat.col) + '» пока нет позиций. Нажмите «Добавить».' : 'Ничего не нашлось. Измените поиск или фильтр.') + '</div>';
      return;
    }
    var page = list.slice(0, cat.shown);
    var showCol = !cat.col;
    var selBox = function (e) { return '<label class="st-sel" title="Выбрать"><input type="checkbox" data-sel="' + esc(e.id) + '"' + (cat.sel[e.id] ? ' checked' : '') + '><span></span></label>'; };
    var html;
    if (cat.view === 'list') {
      html = '<div class="st-ctable"><div class="st-crow head"><span></span><span></span><span>Позиция</span><span>Цена</span><span>Размеры в наличии</span><span>Остаток</span><span></span></div>' +
        page.map(function (e) {
          var ph = (e.media || [])[0];
          var d = discount(e);
          var stockCell = (e.variants || []).length
            ? '<button class="st-cell" type="button" data-cedit="' + esc(e.id) + '" title="Остаток по размерам">' + (stockBadge(e) || '—') + '</button>'
            : '<button class="st-cell" type="button" data-istock="' + esc(e.id) + '" title="Изменить остаток">' + (stockBadge(e) || '<span class="st-cbadge ok">есть</span>') + '</button>';
          return '<div class="st-crow' + (e.is_active === false ? ' off' : '') + (cat.sel[e.id] ? ' picked' : '') + '" data-open="' + esc(e.id) + '">' + selBox(e) +
            '<span class="th">' + (ph && ph.url ? mediaThumb(ph.url, ph.is_video) : icon('image')) + '</span>' +
            '<span class="nm"><b>' + esc(e.title || '—') + '</b><small>' + esc([showCol ? e.collection : '', articleOf(e) ? 'арт. ' + articleOf(e) : ''].filter(Boolean).join(' · ')) + '</small></span>' +
            '<span class="pr"><button class="st-cell" type="button" data-iprice="' + esc(e.id) + '" title="Изменить цену">' + (hasPrice(e) ? esc(money(e.price, e.currency)) : '<span class="st-need">указать цену</span>') + '</button>' +
            (d ? '<small><s>' + esc(money(e.old_price, e.currency)) + '</s> −' + d + '%</small>' : '') + '</span>' +
            '<span class="sz">' + esc(sizesInStock(e).join(', ')) + '</span>' +
            '<span>' + stockCell + '</span>' +
            '<span class="acts">' + catActions(e) + '</span></div>';
        }).join('') + '</div>';
    } else {
      html = '<div class="st-cgrid">' + page.map(function (e) {
        var ph = (e.media || [])[0];
        var d = discount(e);
        var sizes = sizesInStock(e);
        return '<div class="st-ccard' + (e.is_active === false ? ' off' : '') + (cat.sel[e.id] ? ' picked' : '') + '" data-open="' + esc(e.id) + '">' +
          '<div class="pic">' + (ph && ph.url ? mediaThumb(ph.url, ph.is_video) : '<span class="st-ph">' + icon('image') + '<small>нет фото</small></span>') + selBox(e) +
          (d ? '<span class="sale">−' + d + '%</span>' : '') +
          ((e.media || []).length > 1 ? '<span class="cnt">' + icon('image') + (e.media || []).length + '</span>' : '') +
          (ph && ph.url ? '<button class="zoom" type="button" data-view="' + esc(e.id) + '" title="Посмотреть фото и видео">' + icon('search') + '</button>' : '') + '</div>' +
          '<div class="bd">' + (showCol ? '<div class="col">' + esc(e.collection || '') + '</div>' : '') +
          '<div class="t">' + esc(e.title || '—') + '</div>' +
          '<div class="pr">' + (hasPrice(e) ? esc(money(e.price, e.currency)) : '<span class="st-need">цена не указана</span>') +
          (d ? ' <s>' + esc(money(e.old_price, e.currency)) + '</s>' : '') + '</div>' +
          (sizes.length ? '<div class="sz">' + sizes.slice(0, 6).map(function (x) { return '<span>' + esc(x) + '</span>'; }).join('') + (sizes.length > 6 ? '<span>+' + (sizes.length - 6) + '</span>' : '') + '</div>' : '') +
          '<div class="ft">' + stockBadge(e) + '<span class="acts">' + catActions(e) + '</span></div></div></div>';
      }).join('') + '</div>';
    }
    if (list.length > cat.shown) html += '<div style="text-align:center;margin-top:14px"><button class="btn" type="button" id="cat-more">Показать ещё ' + Math.min(CAT_PAGE, list.length - cat.shown) + '</button></div>';
    wrap.innerHTML = html;
    S.$$('[data-open]', wrap).forEach(function (el) {
      el.addEventListener('click', function (ev) { if (ev.target.closest('button,label,input')) return; productDialog(catById(el.dataset.open)); });
    });
    S.$$('[data-sel]', wrap).forEach(function (cb) {
      cb.addEventListener('change', function () {
        if (cb.checked) cat.sel[cb.dataset.sel] = true; else delete cat.sel[cb.dataset.sel];
        var card = cb.closest('[data-open]'); if (card) card.classList.toggle('picked', cb.checked);
        drawBulkBar();
        var sa2 = S.$('#cat-selall'); if (sa2) { var p = catVisible().slice(0, cat.shown); sa2.checked = p.every(function (e) { return cat.sel[e.id]; }); }
      });
    });
    S.$$('[data-view]', wrap).forEach(function (b) { b.addEventListener('click', function () { mediaViewer(catById(b.dataset.view)); }); });
    S.$$('[data-cedit]', wrap).forEach(function (b) { b.addEventListener('click', function () { productDialog(catById(b.dataset.cedit)); }); });
    S.$$('[data-iprice]', wrap).forEach(function (b) { b.addEventListener('click', function () { inlineEdit(b, catById(b.dataset.iprice), 'price'); }); });
    S.$$('[data-istock]', wrap).forEach(function (b) { b.addEventListener('click', function () { inlineEdit(b, catById(b.dataset.istock), 'stock_qty'); }); });
    S.$$('[data-ccopy]', wrap).forEach(function (b) {
      b.addEventListener('click', function () {
        var src = catById(b.dataset.ccopy); if (!src) return;
        var copy = JSON.parse(JSON.stringify(src)); delete copy.id; copy.title = (src.title || '') + ' (копия)';
        productDialog(null, copy);
      });
    });
    S.$$('[data-cdel]', wrap).forEach(function (b) {
      b.addEventListener('click', function () {
        S.confirm({ title: 'Удалить позицию?', message: 'Бот больше не будет её предлагать. Фото и видео останутся в файлах.', confirmText: 'Удалить', danger: true }).then(function (ok) {
          if (!ok) return;
          S.api('/knowledge/' + b.dataset.cdel, { method: 'DELETE' }).then(function (r) {
            if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
            delete cat.sel[b.dataset.cdel];
            loadKb();
          });
        });
      });
    });
    var more = S.$('#cat-more'); if (more) more.addEventListener('click', function () { cat.shown += CAT_PAGE; drawCatItems(); });
  }
  function catActions(e) {
    return '<button class="btn btn-ghost btn-icon btn-sm" data-cedit="' + esc(e.id) + '" type="button" title="Изменить">' + icon('pen') + '</button>' +
      '<button class="btn btn-ghost btn-icon btn-sm" data-ccopy="' + esc(e.id) + '" type="button" title="Копировать: похожая позиция">' + icon('copy') + '</button>' +
      '<button class="btn btn-ghost btn-icon btn-sm" data-cdel="' + esc(e.id) + '" type="button" title="Удалить">' + icon('trash-2') + '</button>';
  }

  // Цена или остаток прямо в таблице: Enter — сохранить, Esc — отменить
  function inlineEdit(btn, e, field) {
    if (!e || btn.dataset.busy) return;
    var cur = field === 'price' ? (hasPrice(e) ? e.price : '') : (e.stock_qty == null ? '' : e.stock_qty);
    var holder = document.createElement('span');
    holder.className = 'st-inline';
    holder.innerHTML = '<input class="input" type="number" min="0" step="' + (field === 'price' ? '0.01' : '1') + '" value="' + esc(cur) + '" placeholder="' + (field === 'price' ? 'Цена' : 'шт.') + '">';
    btn.replaceWith(holder);
    var inp = holder.querySelector('input');
    inp.focus(); inp.select();
    var done = false;
    var finish = function (save) {
      if (done) return; done = true;
      var v = inp.value.trim();
      if (!save || String(v) === String(cur)) { drawCatItems(); return; }
      var body = {};
      if (field === 'price') body.price = v === '' ? null : Number(v);
      else { body.stock_qty = v === '' ? null : Math.max(0, parseInt(v, 10) || 0); if (v === '') body.in_stock = true; }
      inp.disabled = true;
      S.api('/knowledge/' + e.id, { method: 'PUT', body: body }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); drawCatItems(); return; }
        var fresh = r.data.entry;
        var i = kb.entries.indexOf(e);
        if (i >= 0 && fresh) { fresh.media = e.media; kb.entries[i] = fresh; }
        drawHealth(); drawCatItems();
      });
    };
    inp.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); finish(true); }
      else if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); finish(false); }
    });
    inp.addEventListener('blur', function () { finish(true); });
  }

  // ── Массовые действия ──
  function drawBulkBar() {
    var bar = S.$('#cat-bulk'); if (!bar) return;
    var ids = selIds();
    if (!ids.length) { bar.innerHTML = ''; bar.className = ''; return; }
    bar.className = 'st-bulk';
    bar.innerHTML = '<b>Выбрано: ' + ids.length + '</b>' +
      '<button class="btn btn-sm" type="button" data-bk="collection">' + icon('layers') + '<span>В коллекцию</span></button>' +
      '<button class="btn btn-sm" type="button" data-bk="price">' + icon('coins') + '<span>Цена</span></button>' +
      '<button class="btn btn-sm" type="button" data-bk="discount">%<span>Скидка</span></button>' +
      '<button class="btn btn-sm" type="button" data-bk="stock">' + icon('package') + '<span>Наличие</span></button>' +
      '<button class="btn btn-sm" type="button" data-bk="active">' + icon('eye') + '<span>Показ</span></button>' +
      '<button class="btn btn-sm btn-danger-ghost" type="button" data-bk="delete">' + icon('trash-2') + '<span>Удалить</span></button>' +
      '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-bk="clear" title="Снять выбор">' + icon('x') + '</button>';
    S.$$('[data-bk]', bar).forEach(function (b) { b.addEventListener('click', function () { bulkAction(b.dataset.bk, ids); }); });
  }
  function bulkSend(ids, action, value, label) {
    return S.api('/knowledge/bulk', { method: 'POST', body: { ids: ids, action: action, value: value } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return false; }
      if (action === 'delete') ids.forEach(function (id) { delete cat.sel[id]; });
      S.toast((label || 'Готово') + ': ' + r.data.changed, 'success');
      loadKb();
      return true;
    });
  }
  function bulkAction(kind, ids) {
    if (kind === 'clear') { cat.sel = {}; drawCatItems(); return; }
    if (kind === 'delete') {
      S.confirm({ title: 'Удалить ' + ids.length + ' позиций?', message: 'Бот больше не будет их предлагать. Фото и видео останутся в файлах.', confirmText: 'Удалить', danger: true })
        .then(function (ok) { if (ok) bulkSend(ids, 'delete', null, 'Удалено'); });
      return;
    }
    var opts = {
      collection: { title: 'Перенести в коллекцию', body: '<div class="field"><label class="label">Коллекция</label>' + collectionSelect('bk-v', cat.col && cat.col !== NO_COL ? cat.col : '') + '<div class="hint">«Без коллекции» — убрать из коллекции.</div></div>',
        bind: function (d) { bindCollectionSelect(S.$('#bk-v', d.el)); },
        go: function (d) { var v = S.$('#bk-v', d.el).value; return bulkSend(ids, 'collection', v === '__new' ? '' : v, 'Перенесено'); } },
      price: { title: 'Изменить цену', body: '<div class="st-grid2"><div class="field"><label class="label">На сколько процентов</label><input class="input" id="bk-pct" type="number" placeholder="10 — дороже, −10 — дешевле"></div>' +
        '<div class="field"><label class="label">Или одна цена всем</label><input class="input" id="bk-set" type="number" min="0" placeholder="Например: 9500"></div></div><div class="hint">Новые цены округляются до 10 сом.</div>',
        go: function (d) {
          var pct = S.$('#bk-pct', d.el).value, set = S.$('#bk-set', d.el).value;
          if (set !== '') return bulkSend(ids, 'price_set', Number(set), 'Цена изменена');
          if (pct !== '' && Number(pct) !== 0) return bulkSend(ids, 'price_percent', Number(pct), 'Цена изменена');
          S.toast('Укажите процент или цену', 'error'); return Promise.resolve(false);
        } },
      discount: { title: 'Скидка', body: '<div class="field"><label class="label">Скидка, %</label><input class="input" id="bk-v" type="number" min="1" max="99" placeholder="Например: 20"></div>' +
        '<div class="hint">Старая цена сохранится и будет зачёркнута, бот назовёт обе. Чтобы вернуть цену — «Убрать скидку».</div>',
        extra: '<button class="btn" type="button" id="bk-off">Убрать скидку</button>',
        go: function (d) { var v = Number(S.$('#bk-v', d.el).value); if (!(v > 0 && v < 100)) { S.toast('Скидка от 1 до 99%', 'error'); return Promise.resolve(false); } return bulkSend(ids, 'discount', v, 'Скидка поставлена'); },
        bind: function (d) { S.$('#bk-off', d.el).addEventListener('click', function () { bulkSend(ids, 'discount_off', null, 'Скидка убрана').then(function (ok) { if (ok) d.close(); }); }); } },
      stock: { title: 'Наличие', body: '<div class="st-bkpick"><button class="btn" type="button" data-v="in_stock">' + icon('circle-check') + '<span>Есть в наличии</span></button>' +
        '<button class="btn" type="button" data-v="out_of_stock">' + icon('circle-x') + '<span>Нет в наличии</span></button></div><div class="hint">«Нет в наличии» обнуляет остатки. Владельцу придёт сообщение в Telegram, если включены оповещения о складе.</div>',
        pick: { in_stock: 'Есть в наличии', out_of_stock: 'Нет в наличии' } },
      active: { title: 'Показ клиентам', body: '<div class="st-bkpick"><button class="btn" type="button" data-v="activate">' + icon('eye') + '<span>Показывать</span></button>' +
        '<button class="btn" type="button" data-v="deactivate">' + icon('eye-off') + '<span>Скрыть от бота</span></button></div><div class="hint">Скрытые позиции бот не предлагает, но они остаются в каталоге.</div>',
        pick: { activate: 'Включено', deactivate: 'Скрыто' } }
    }[kind];
    if (!opts) return;
    var d = S.dialog({
      title: opts.title + ' · ' + ids.length + ' поз.', body: opts.body,
      foot: '<button class="btn" type="button" data-c>Отмена</button>' + (opts.extra || '') + (opts.go ? '<button class="btn btn-primary" type="button" id="bk-ok">Применить</button>' : '')
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    if (opts.go) S.$('#bk-ok', d.el).addEventListener('click', function () { var b = this; b.disabled = true; opts.go(d).then(function (ok) { b.disabled = false; if (ok) d.close(); }); });
    if (opts.bind) opts.bind(d);
    if (opts.pick) S.$$('[data-v]', d.el).forEach(function (b) { b.addEventListener('click', function () { bulkSend(ids, b.dataset.v, null, opts.pick[b.dataset.v]).then(function (ok) { if (ok) d.close(); }); }); });
    setTimeout(function () { var i = S.$('input', d.el); if (i) i.focus(); }, 60);
  }

  // ── Быстрая загрузка: позиции из фото и вставка списком ──
  function titleFromFile(name) {
    var t = String(name || '').replace(/\.[a-z0-9]{2,5}$/i, '').replace(/[_]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!t || /^(img|image|photo|dsc|pxl|whatsapp image|фото)[\s\-_0-9]*/i.test(t) && /\d{3,}/.test(t)) return '';
    return t.charAt(0).toUpperCase() + t.slice(1);
  }
  function productsFromPhotos(col) {
    pickFiles('image/*,video/*', true, function (rows) {
      if (!rows.length) return;
      var n = 0;
      var items = rows.map(function (f) {
        n += 1;
        return { title: titleFromFile(f.filename || f.title) || ('Новый товар ' + n), collection: col || null, media_file_ids: [f.id] };
      });
      S.api('/knowledge/products/batch', { method: 'POST', body: { items: items } }).then(function (r) {
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        S.toast('Создано позиций: ' + r.data.created + '. Допишите названия и цены прямо в таблице', 'success');
        cat.view = 'list'; cat.filter = 'noprice'; cat.q = ''; catSave();
        loadKb();
      });
    });
  }
  function parsePasted(text, col) {
    var out = [];
    String(text || '').split(/\r?\n/).forEach(function (line) {
      line = line.trim(); if (!line) return;
      var parts = line.indexOf('\t') >= 0 ? line.split('\t') : line.split(/\s*[;|]\s*/);
      if (parts.length === 1) {
        // «Шапка норка 9500» — цена последним числом в строке
        var m = line.match(/^(.*?)[\s:,-]+(\d[\d\s]{1,9})\s*(сом|с|kgs)?\.?$/i);
        parts = m ? [m[1], m[2]] : [line];
      }
      var title = (parts[0] || '').trim(); if (!title) return;
      var price = parts[1] ? Number(String(parts[1]).replace(/[^\d.,]/g, '').replace(',', '.')) : null;
      var it = { title: title, collection: col || null, price: price && !isNaN(price) ? price : null };
      if (parts[2]) it.sizes = String(parts[2]).split(/[,/ ]+/).filter(Boolean);
      if (parts[3] && /\d/.test(parts[3])) it.stock_qty = parseInt(String(parts[3]).replace(/\D/g, ''), 10);
      out.push(it);
    });
    return out;
  }
  function pasteDialog(col) {
    var d = S.dialog({
      title: 'Вставить списком' + (col ? ' в «' + col + '»' : ''), size: 'lg',
      body: '<div class="hint" style="margin-bottom:8px">По товару на строку. Можно скопировать прямо из Excel или переписать из WhatsApp:<br>' +
        '<code>Название; цена; размеры; количество</code> — всё, кроме названия, необязательно.</div>' +
        '<textarea class="textarea" id="pl-text" rows="9" placeholder="Шапка-ушанка норка; 9500; 56, 58; 5&#10;Берет норка; 8000&#10;Палантин норка 15500"></textarea>' +
        '<div id="pl-prev" class="st-plprev"></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="pl-ok" disabled>Добавить</button>'
    });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    var ta = S.$('#pl-text', d.el), prev = S.$('#pl-prev', d.el), ok = S.$('#pl-ok', d.el);
    var items = [];
    var redraw = function () {
      items = parsePasted(ta.value, col);
      ok.disabled = !items.length;
      ok.textContent = items.length ? 'Добавить ' + items.length : 'Добавить';
      prev.innerHTML = items.slice(0, 8).map(function (it) {
        return '<div><b>' + esc(it.title) + '</b><span>' + esc([it.price != null ? money(it.price, 'KGS') : 'без цены', (it.sizes || []).join(', '), it.stock_qty != null ? it.stock_qty + ' шт.' : ''].filter(Boolean).join(' · ')) + '</span></div>';
      }).join('') + (items.length > 8 ? '<div class="muted small">…и ещё ' + (items.length - 8) + '</div>' : '');
    };
    ta.addEventListener('input', S.debounce(redraw, 120));
    ok.addEventListener('click', function () {
      if (!items.length) return;
      ok.disabled = true;
      S.api('/knowledge/products/batch', { method: 'POST', body: { items: items.slice(0, 500) } }).then(function (r) {
        ok.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        d.close(); S.toast('Добавлено позиций: ' + r.data.created + '. Фото можно добавить в каждой позиции', 'success'); loadKb();
      });
    });
    setTimeout(function () { ta.focus(); }, 60);
  }

  // Справочник коллекций хранится на сервере (agent_configs.catalog_collections): коллекции есть и пустыми,
  // а в товаре выбираются из выпадающего списка.
  function saveCollections(list) {
    return S.api('/knowledge/collections/set', { method: 'POST', body: { collections: list } }).then(function (r) {
      if (!r.ok) { S.toast(S.apiError(r), 'error'); return null; }
      kb.collections = r.data.collections || list;
      return kb.collections;
    });
  }
  function addCollection(name) {
    var v = String(name || '').replace(/\s+/g, ' ').trim();
    if (!v) return Promise.resolve(null);
    var list = catCollections().map(function (c) { return c[0]; });
    var same = list.filter(function (c) { return c.toLowerCase() === v.toLowerCase(); })[0];
    if (same) return Promise.resolve(same);
    list.push(v);
    return saveCollections(list).then(function (r) { return r ? v : null; });
  }
  function newCollectionDialog(box) {
    var d = S.dialog({
      title: 'Новая коллекция',
      body: '<div class="field"><label class="label">Название</label><input class="input" id="nc-name" placeholder="Например: Женская, Мужская, Детская, Осень, Новинки"></div>' +
        '<div class="hint">Коллекция появится сверху каталога и в выпадающем списке у каждого товара.</div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button><button class="btn btn-primary" type="button" id="nc-ok">Создать</button>'
    });
    var go = function () {
      var v = S.$('#nc-name', d.el).value;
      if (!v.trim()) { S.toast('Введите название', 'error'); return; }
      addCollection(v).then(function (name) {
        if (!name) return;
        cat.col = name; cat.q = ''; cat.filter = 'all'; d.close(); drawCatalog(box);
      });
    };
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    S.$('#nc-ok', d.el).addEventListener('click', go);
    S.$('#nc-name', d.el).addEventListener('keydown', function (e) { if (e.key === 'Enter') go(); });
    setTimeout(function () { S.$('#nc-name', d.el).focus(); }, 60);
  }

  // Управление коллекциями: список с количеством товаров, порядок, переименование, удаление, новая
  function collectionsManager() {
    var d = S.dialog({ title: 'Коллекции каталога', size: 'lg', body: '<div id="cm-list"></div>' +
      '<div class="st-sizeadd" style="max-width:none;margin-top:12px"><input class="input" id="cm-new" placeholder="Новая коллекция: Женская, Мужская, Детская, Осень…"><button class="btn btn-primary btn-sm" type="button" id="cm-add">' + icon('plus') + '<span>Добавить</span></button></div>' +
      '<div class="hint" style="margin-top:8px">Порядок здесь = порядок вкладок над каталогом. Удалить коллекцию — товары не удаляются, они попадут в «Без коллекции».</div>',
      foot: '<button class="btn btn-primary" type="button" data-c>Готово</button>', onClose: function () { loadKb(); } });
    S.$('[data-c]', d.el).addEventListener('click', d.close);
    function draw() {
      var cols = catCollections();
      var box = S.$('#cm-list', d.el);
      box.innerHTML = cols.length ? '<div class="st-cmlist">' + cols.map(function (c, i) {
        return '<div class="st-cmrow" data-i="' + i + '"><span class="nm">' + esc(c[0]) + '</span><span class="muted small">' + c[1] + ' тов.</span>' +
          '<span class="acts"><button class="btn btn-ghost btn-icon btn-sm" type="button" data-up="' + i + '" title="Выше"' + (i ? '' : ' disabled') + '>' + icon('chevron-up') + '</button>' +
          '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-down="' + i + '" title="Ниже"' + (i < cols.length - 1 ? '' : ' disabled') + '>' + icon('chevron-down') + '</button>' +
          '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-ren="' + i + '" title="Переименовать">' + icon('pen') + '</button>' +
          '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-rm="' + i + '" title="Убрать">' + icon('trash-2') + '</button></span></div>';
      }).join('') + '</div>' : '<div class="st-empty" style="padding:16px">Коллекций пока нет. Добавьте первую ниже.</div>';
      var names = cols.map(function (c) { return c[0]; });
      var move = function (i, j) { var t = names[i]; names[i] = names[j]; names[j] = t; saveCollections(names).then(draw); };
      S.$$('[data-up]', box).forEach(function (b) { b.addEventListener('click', function () { var i = +b.dataset.up; move(i, i - 1); }); });
      S.$$('[data-down]', box).forEach(function (b) { b.addEventListener('click', function () { var i = +b.dataset.down; move(i, i + 1); }); });
      S.$$('[data-ren]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          var row = b.closest('.st-cmrow'), old = names[+b.dataset.ren];
          row.querySelector('.nm').innerHTML = '<input class="input" value="' + esc(old) + '">';
          var inp = row.querySelector('input'); inp.focus(); inp.select();
          var done = false;
          var fin = function (ok) {
            if (done) return; done = true;
            var v = inp.value.replace(/\s+/g, ' ').trim();
            if (!ok || !v || v === old) { draw(); return; }
            S.api('/knowledge/collections/rename', { method: 'POST', body: { old: old, new: v } }).then(function (r) {
              if (!r.ok) { S.toast(S.apiError(r), 'error'); draw(); return; }
              kb.collections = r.data.collections || kb.collections;
              kb.entries.forEach(function (e) { if (e.kind === 'product' && (e.collection || '') === old) e.collection = v; });
              if (cat.col === old) cat.col = v;
              draw();
            });
          };
          inp.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') fin(true); if (ev.key === 'Escape') { ev.stopPropagation(); fin(false); } });
          inp.addEventListener('blur', function () { fin(true); });
        });
      });
      S.$$('[data-rm]', box).forEach(function (b) {
        b.addEventListener('click', function () {
          var c = cols[+b.dataset.rm];
          var go = function () {
            S.api('/knowledge/collections/rename', { method: 'POST', body: { old: c[0], new: '' } }).then(function (r) {
              if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
              kb.collections = r.data.collections || kb.collections.filter(function (x) { return x !== c[0]; });
              kb.entries.forEach(function (e) { if (e.kind === 'product' && (e.collection || '') === c[0]) e.collection = ''; });
              if (cat.col === c[0]) cat.col = '';
              draw();
            });
          };
          if (!c[1]) { go(); return; }
          S.confirm({ title: 'Убрать коллекцию «' + c[0] + '»?', message: c[1] + ' товаров не удалятся, они попадут в «Без коллекции».', confirmText: 'Убрать' }).then(function (ok) { if (ok) go(); });
        });
      });
    }
    var add = function () {
      var inp = S.$('#cm-new', d.el);
      addCollection(inp.value).then(function (name) { if (name) { inp.value = ''; draw(); inp.focus(); } });
    };
    S.$('#cm-add', d.el).addEventListener('click', add);
    S.$('#cm-new', d.el).addEventListener('keydown', function (e) { if (e.key === 'Enter') add(); });
    draw();
    setTimeout(function () { S.$('#cm-new', d.el).focus(); }, 60);
  }

  // Выпадающий список коллекций (товар, массовый перенос): «Без коллекции», справочник, «+ Новая…»
  function collectionSelect(id, current) {
    var cols = catCollections().map(function (c) { return c[0]; });
    if (current && cols.indexOf(current) < 0) cols.push(current);
    return '<select class="select-native" id="' + id + '"><option value="">Без коллекции</option>' +
      cols.map(function (c) { return '<option value="' + esc(c) + '"' + (c === current ? ' selected' : '') + '>' + esc(c) + '</option>'; }).join('') +
      '<option value="__new">+ Новая коллекция…</option></select>';
  }
  function bindCollectionSelect(sel, onChange) {
    var prev = sel.value;
    sel.addEventListener('change', function () {
      if (sel.value !== '__new') { prev = sel.value; if (onChange) onChange(); return; }
      var name = window.prompt('Название новой коллекции: Женская, Мужская, Детская, Осень…', '');
      if (!name || !name.trim()) { sel.value = prev; return; }
      addCollection(name).then(function (v) {
        if (!v) { sel.value = prev; return; }
        if (![].some.call(sel.options, function (o) { return o.value === v; })) {
          var o = document.createElement('option'); o.value = v; o.textContent = v;
          sel.insertBefore(o, sel.querySelector('option[value="__new"]'));
        }
        sel.value = v; prev = v; if (onChange) onChange();
      });
    });
  }

  // ── Окно позиции каталога ──
  function productDialog(e, prefill, gapId) {
    var src = e || prefill || {};
    var picked = (src.media_file_ids || []).slice();
    var sizes = (src.sizes || []).slice();
    var colors = [];
    (src.variants || []).forEach(function (v) { if (v.color && colors.indexOf(v.color) < 0) colors.push(v.color); if (v.size && sizes.indexOf(v.size) < 0) sizes.push(v.size); });
    var qtyMap = {};   // "size|color" → остаток
    (src.variants || []).forEach(function (v) { qtyMap[(v.size || '') + '|' + (v.color || '')] = v.qty; });
    var stockMode = (src.variants || []).length ? 'variants' : 'total';
    var previewReady = false;
    var sku = articleOf(src);
    var attrs = Object.keys(src.attributes || {}).filter(function (k) { return k.charAt(0) !== '_' && !/^(артикул|код|sku)$/i.test(k); })
      .map(function (k) { return { k: k, v: String(src.attributes[k]) }; });
    var hidden = {};
    Object.keys(src.attributes || {}).forEach(function (k) { if (k.charAt(0) === '_') hidden[k] = src.attributes[k]; });
    var cols = catCollections().map(function (c) { return c[0]; });
    var val = function (v) { return v == null ? '' : esc(v); };
    var d = S.dialog({
      title: e ? 'Изменить: ' + CAT_ONE : 'Новая ' + (CAT_ONE === 'позиция' || CAT_ONE === 'услуга' ? CAT_ONE : 'позиция'), size: 'xl',
      body: '<div class="st-pwrap"><div class="st-pform">' +
        '<div class="st-psec"><div class="field"><label class="label">Название *</label><input class="input" id="p-title" value="' + val(src.title) + '" placeholder="Например: Шапка-ушанка из норки"></div>' +
        '<div class="st-grid2"><div class="field"><label class="label">Коллекция</label>' + collectionSelect('p-col', (src.collection || '').trim()) + '</div>' +
        '<div class="field"><label class="label">Артикул / код</label><input class="input" id="p-sku" value="' + val(sku) + '" placeholder="Необязательно"></div></div></div>' +

        '<div class="st-psec"><div class="st-psec-h">Фото и видео</div><div class="hint" style="margin-bottom:8px">Первое фото уйдёт клиенту в карточке товара. Можно выбрать сразу несколько файлов (каждый до 20 МБ).</div>' +
        '<div id="e-media"></div><div style="margin-top:8px"><button class="btn btn-ghost btn-sm" type="button" id="e-up">' + icon('upload') + '<span>Загрузить фото или видео</span></button></div></div>' +

        '<div class="st-psec"><div class="st-psec-h">Цена</div><div class="st-grid4">' +
        '<div class="field"><label class="label">Цена сейчас</label><input class="input" id="p-price" type="number" min="0" step="0.01" value="' + val(src.price) + '"></div>' +
        '<div class="field"><label class="label">Скидка, %</label><input class="input" id="p-pct" type="number" min="0" max="95" step="1" placeholder="0"></div>' +
        '<div class="field"><label class="label">Цена до скидки</label><input class="input" id="p-old" type="number" min="0" step="0.01" value="' + val(src.old_price) + '" placeholder="Считается сама"></div>' +
        '<div class="field"><label class="label">Валюта</label><select class="select-native" id="p-cur">' + CURRENCIES.map(function (c) { return '<option value="' + c[0] + '"' + ((src.currency || 'KGS') === c[0] ? ' selected' : '') + '>' + c[1] + '</option>'; }).join('') + '</select></div>' +
        '</div><div class="hint" id="p-disc"></div>' +
        (SHOW_DURATION ? '<div class="st-grid4" style="margin-top:8px"><div class="field"><label class="label">Длительность, мин</label><input class="input" id="p-dur" type="number" min="5" max="1440" step="5" value="' + val(src.duration_min) + '" placeholder="Например: 60"></div></div>' +
          '<div class="hint">По длительности онлайн-запись считает свободное время мастера.</div>' : '') +
        '</div>' +

        '<div class="st-psec"' + (SHOW_SIZES ? '' : ' style="display:none"') + '><div class="st-psec-h">' + esc(SIZES_LABEL) + '</div><div class="st-sizes" id="p-sizes"></div>' +
        '<div class="st-sizeadd"><input class="input" id="p-size" placeholder="Добавить размер и Enter: 46, M, 110"><button class="btn btn-sm" type="button" id="p-size-add">' + icon('plus') + '</button></div>' +
        '<div class="st-quick" style="margin-top:8px">' + SIZE_PRESETS.map(function (p, i) { return '<button type="button" data-ps="' + i + '">' + esc(p[0]) + '</button>'; }).join('') +
        '<button type="button" id="p-size-clear">Очистить</button></div></div>' +

        '<div class="st-psec"' + (SHOW_STOCK ? '' : ' style="display:none"') + '><div class="st-psec-h st-psec-hrow"><span>Остаток</span><div class="st-seg st-seg-text"><button type="button" data-sm="total">Одним числом</button><button type="button" data-sm="variants">По размерам и цветам</button></div></div>' +
        '<div id="p-stock-total"><div class="st-grid2">' +
        '<div class="field"><label class="label">Остаток, шт.</label><input class="input" id="p-stock" type="number" min="0" step="1" value="' + val(stockMode === 'total' ? src.stock_qty : '') + '" placeholder="Пусто — не считаем"></div>' +
        '<div class="field"><label class="label">Наличие</label><select class="select-native" id="p-instock"><option value="1">Есть в наличии</option><option value="0"' + (src.in_stock === false ? ' selected' : '') + '>Нет в наличии</option></select></div>' +
        '</div></div>' +
        '<div id="p-stock-var"><div class="field"><label class="label">Цвета (необязательно)</label><div class="st-sizes" id="p-colors"></div>' +
        '<div class="st-sizeadd"><input class="input" id="p-color" placeholder="Добавить цвет и Enter"><button class="btn btn-sm" type="button" id="p-color-add">' + icon('plus') + '</button></div>' +
        '<div class="st-quick" style="margin-top:6px">' + COLOR_QUICK.map(function (c) { return '<button type="button" data-qc="' + c + '">' + c + '</button>'; }).join('') + '</div></div>' +
        '<div id="p-matrix"></div></div>' +
        '<div class="hint" id="p-stock-hint"></div></div>' +

        '<div class="st-psec"><div class="st-psec-h">Описание и характеристики</div>' +
        '<div class="field"><label class="label">Описание</label><textarea class="textarea" id="p-body" rows="3" placeholder="Материал, фасон, для кого, уход. Бот возьмёт отсюда 1–2 фразы в карточку">' + val(src.body) + '</textarea></div>' +
        '<div class="field"><label class="label">Характеристики</label><div id="e-attrs"></div><button class="btn btn-ghost btn-sm" type="button" id="e-attr-add">' + icon('plus') + '<span>Добавить характеристику</span></button>' +
        '<div class="st-quick" style="margin-top:6px">' + ATTR_QUICK.map(function (k) { return '<button type="button" data-ak2="' + esc(k) + '">' + esc(k) + '</button>'; }).join('') + '</div></div>' +
        '<div class="field"><label class="label">Ключевые слова</label><input class="input" id="p-kw" value="' + val(src.keywords) + '" placeholder="через запятую: ушанка, зимняя шапка, мех">' +
        '<div class="hint">По ним бот находит позицию, если клиент назвал её по-другому.</div></div></div>' +

        '<div class="st-switch-row"><div class="t"><b>Позиция активна</b><div>Выключенную позицию бот не предлагает.</div></div><label class="switch"><input type="checkbox" id="p-active"' + (src.is_active !== false ? ' checked' : '') + '><span class="track"></span></label></div>' +
        '</div>' +
        '<aside class="st-pside"><div class="st-psec-h">Так клиент увидит товар</div><div class="st-phone"><div class="st-bubble"><div class="img" id="pv-img"></div><div class="cap" id="pv-cap"></div><div class="tm">12:30 ✓✓</div></div></div>' +
        '<div class="st-ready" id="pv-ready"></div></aside></div>',
      foot: '<button class="btn" type="button" data-c>Отмена</button>' +
        (!e ? '<button class="btn" type="button" id="p-save-more">Сохранить и добавить ещё</button>' : '') +
        '<button class="btn btn-primary" type="button" id="p-save">Сохранить</button>'
    });
    var $ = function (sel) { return S.$(sel, d.el); };
    $('[data-c]').addEventListener('click', d.close);

    function chips(boxSel, list, onRemove) {
      $(boxSel).innerHTML = list.length ? list.map(function (x, i) { return '<span class="st-sz">' + esc(x) + '<button type="button" data-rm="' + i + '" aria-label="Убрать">' + icon('x') + '</button></span>'; }).join('')
        : '<span class="hint">' + (boxSel === '#p-sizes' ? 'Размеров нет. Для товаров без размера оставьте пусто.' : 'Без цветов: остаток только по размерам.') + '</span>';
      S.$$(boxSel + ' [data-rm]', d.el).forEach(function (b) { b.addEventListener('click', function () { onRemove(+b.dataset.rm); }); });
    }
    function drawSizes() { chips('#p-sizes', sizes, function (i) { sizes.splice(i, 1); drawSizes(); }); drawMatrix(); }
    function drawColors() {
      // Первый цвет: остатки, введённые только по размерам, переходят к нему, а не обнуляются
      if (colors.length === 1) {
        (sizes.length ? sizes : ['']).forEach(function (s) {
          var from = s + '|', to = s + '|' + colors[0];
          if (qtyMap[from] != null && qtyMap[to] == null) qtyMap[to] = qtyMap[from];
        });
      }
      chips('#p-colors', colors, function (i) { colors.splice(i, 1); drawColors(); });
      drawMatrix();
    }
    function addTo(list, vals, redraw) {
      vals.forEach(function (x) {
        x = String(x || '').replace(/\s+/g, ' ').trim().slice(0, list === sizes ? 20 : 40);
        if (x && !list.some(function (s) { return s.toLowerCase() === x.toLowerCase(); })) list.push(x);
      });
      redraw();
    }
    var sizeIn = $('#p-size'), colorIn = $('#p-color');
    var addSize = function () { addTo(sizes, sizeIn.value.split(/[,;/]+/), drawSizes); sizeIn.value = ''; sizeIn.focus(); };
    var addColor = function () { addTo(colors, colorIn.value.split(/[,;/]+/), drawColors); colorIn.value = ''; colorIn.focus(); };
    sizeIn.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); addSize(); } });
    colorIn.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { ev.preventDefault(); addColor(); } });
    $('#p-size-add').addEventListener('click', addSize);
    $('#p-color-add').addEventListener('click', addColor);
    S.$$('[data-ps]', d.el).forEach(function (b) { b.addEventListener('click', function () { addTo(sizes, SIZE_PRESETS[+b.dataset.ps][1], drawSizes); }); });
    S.$$('[data-qc]', d.el).forEach(function (b) { b.addEventListener('click', function () { addTo(colors, [b.dataset.qc], drawColors); }); });
    $('#p-size-clear').addEventListener('click', function () { sizes = []; drawSizes(); });
    bindCollectionSelect($('#p-col'), function () { if (previewReady) preview(); });

    // Таблица остатков: строки — размеры, столбцы — цвета (или один «Остаток»)
    function drawMatrix() {
      var box = $('#p-matrix'); if (!box) return;
      if (!sizes.length && !colors.length) { box.innerHTML = '<div class="hint">Добавьте размеры выше (и цвета, если нужно) — здесь появится таблица остатков.</div>'; updateStockHint(); return; }
      var rows = sizes.length ? sizes : [''];
      var cs = colors.length ? colors : [''];
      var cell = function (s, c) {
        var k = s + '|' + c, q = qtyMap[k];
        return '<input class="input' + (q === 0 ? ' zero' : '') + '" type="number" min="0" step="1" data-q="' + esc(k) + '" value="' + (q == null ? '' : esc(q)) + '" placeholder="0">';
      };
      var grid = cs.length <= 1 || rows.length <= 1;
      if (grid) {
        // Один цвет (или один размер) — компактная сетка «размер: штук», а не длинная таблица
        var pairs = [];
        rows.forEach(function (s) { cs.forEach(function (c) { if (s || c) pairs.push([s, c]); }); });
        box.innerHTML = (cs.length === 1 && cs[0] && rows.length > 1 ? '<div class="hint" style="margin:4px 0">Цвет: ' + esc(cs[0]) + '</div>' : '') +
          '<div class="st-qgrid">' + pairs.map(function (pc) {
            return '<label><span>' + esc(rows.length > 1 || !pc[1] ? (pc[0] || 'Все') : pc[1]) + '</span>' + cell(pc[0], pc[1]) + '</label>';
          }).join('') + '</div>' +
          '<div class="st-quick"><button type="button" id="p-fill1">Всем по 1</button><button type="button" id="p-fill0">Обнулить</button></div>';
      } else
      box.innerHTML = '<div class="st-matrix"><table><thead><tr><th>' + (sizes.length ? 'Размер' : '') + '</th>' + cs.map(function (c) { return '<th>' + esc(c || 'Остаток, шт.') + '</th>'; }).join('') + '</tr></thead><tbody>' +
        rows.map(function (s) {
          return '<tr><td><b>' + esc(s || 'Все') + '</b></td>' + cs.map(function (c) { return '<td>' + cell(s, c) + '</td>'; }).join('') + '</tr>';
        }).join('') + '</tbody></table></div>' +
        '<div class="st-quick"><button type="button" id="p-fill1">Всем по 1</button><button type="button" id="p-fill0">Обнулить</button></div>';
      S.$$('[data-q]', box).forEach(function (inp) {
        inp.addEventListener('input', function () { var v = inp.value; qtyMap[inp.dataset.q] = v === '' ? null : Math.max(0, parseInt(v, 10) || 0); inp.classList.toggle('zero', qtyMap[inp.dataset.q] === 0); updateStockHint(); preview(); });
      });
      var fill = function (n) { S.$$('[data-q]', box).forEach(function (inp) { qtyMap[inp.dataset.q] = n; inp.value = n; inp.classList.toggle('zero', n === 0); }); updateStockHint(); preview(); };
      $('#p-fill1').addEventListener('click', function () { fill(1); });
      $('#p-fill0').addEventListener('click', function () { fill(0); });
      updateStockHint();
      if (previewReady) preview();
    }
    function buildVariants() {
      var out = [];
      var rows = sizes.length ? sizes : [''];
      var cs = colors.length ? colors : [''];
      rows.forEach(function (s) { cs.forEach(function (c) { if (!s && !c) return; out.push({ size: s, color: c, qty: Number(qtyMap[s + '|' + c] || 0) }); }); });
      return out;
    }
    function setStockMode(m) {
      stockMode = m;
      S.$$('[data-sm]', d.el).forEach(function (b) { b.classList.toggle('on', b.dataset.sm === m); });
      $('#p-stock-total').style.display = m === 'total' ? '' : 'none';
      $('#p-stock-var').style.display = m === 'variants' ? '' : 'none';
      drawMatrix(); updateStockHint(); preview();
    }
    function updateStockHint() {
      var h = $('#p-stock-hint'); if (!h) return;
      if (stockMode === 'variants') {
        var v = buildVariants(), total = v.reduce(function (a, x) { return a + x.qty; }, 0);
        var have = v.filter(function (x) { return x.qty > 0; }).map(function (x) { return [x.size, x.color].filter(Boolean).join(' '); });
        h.textContent = v.length ? ('Всего ' + total + ' шт. ' + (total ? 'Бот предложит только то, что есть: ' + have.slice(0, 8).join(', ') + (have.length > 8 ? ' и ещё ' + (have.length - 8) + '.' : '.') : 'Ничего нет в наличии — бот так и скажет.')) : '';
      } else {
        h.textContent = 'Укажете остаток — наличие считается само: 0 шт. значит «нет в наличии», до 3 шт. бот скажет, что осталось мало.';
      }
    }
    S.$$('[data-sm]', d.el).forEach(function (b) { b.addEventListener('click', function () { setStockMode(b.dataset.sm); }); });
    var syncStock = function () { var v = $('#p-stock').value; var sel = $('#p-instock'); sel.disabled = v !== ''; if (v !== '') sel.value = Number(v) > 0 ? '1' : '0'; };
    $('#p-stock').addEventListener('input', function () { syncStock(); preview(); });
    $('#p-instock').addEventListener('change', preview);
    syncStock();

    // Цена, скидка в процентах и цена до скидки связаны: владелец вписывает то, что ему удобнее.
    // На живом кабинете в «старую цену» вписали 15 — имели в виду скидку 15%.
    function round10(v) { return v >= 1000 ? Math.round(v / 10) * 10 : Math.round(v); }
    function drawDisc() {
      var p = parseFloat($('#p-price').value), o = parseFloat($('#p-old').value), el = $('#p-disc');
      var warn = [];
      if (p > 5000000) warn.push('Цена ' + Number(p).toLocaleString('ru-RU') + ' — проверьте, нет ли лишних цифр.');
      if (o && p && o <= p) warn.push(o <= 95 ? 'В «Цена до скидки» вписано ' + o + ' — если это скидка в процентах, впишите её в поле «Скидка, %».' : 'Цена до скидки должна быть больше текущей — иначе скидки нет.');
      el.classList.toggle('st-warn', warn.length > 0);
      el.textContent = warn.length ? warn.join(' ') : (p > 0 && o > p ? 'Скидка ' + Math.round((1 - p / o) * 100) + '%: клиент увидит обе цены.' : '');
    }
    function syncPct() {
      var p = parseFloat($('#p-price').value), o = parseFloat($('#p-old').value);
      $('#p-pct').value = p > 0 && o > p ? Math.round((1 - p / o) * 100) : '';
    }
    $('#p-price').addEventListener('input', function () {
      var pct = parseFloat($('#p-pct').value), p = parseFloat($('#p-price').value);
      if (pct > 0 && pct < 96 && p > 0) $('#p-old').value = round10(p / (1 - pct / 100));
      drawDisc(); preview();
    });
    $('#p-pct').addEventListener('input', function () {
      var pct = parseFloat($('#p-pct').value), p = parseFloat($('#p-price').value);
      $('#p-old').value = pct > 0 && pct < 96 && p > 0 ? round10(p / (1 - pct / 100)) : '';
      drawDisc(); preview();
    });
    $('#p-old').addEventListener('input', function () { syncPct(); drawDisc(); preview(); });
    syncPct();
    ['#p-title', '#p-body'].forEach(function (s) { $(s).addEventListener('input', S.debounce(preview, 120)); });
    drawDisc();

    function drawAttrs() {
      var box = $('#e-attrs');
      box.innerHTML = attrs.map(function (a, i) {
        return '<div class="st-attr"><input class="input" data-ak="' + i + '" placeholder="Например: Цвет" value="' + esc(a.k) + '"><input class="input" data-av="' + i + '" placeholder="Например: чёрный" value="' + esc(a.v) + '">' +
          '<button class="btn btn-ghost btn-icon btn-sm" type="button" data-arm="' + i + '" aria-label="Убрать">' + icon('x') + '</button></div>';
      }).join('');
      S.$$('[data-ak]', box).forEach(function (el) { el.addEventListener('input', function () { attrs[+el.dataset.ak].k = el.value; preview(); }); });
      S.$$('[data-av]', box).forEach(function (el) { el.addEventListener('input', function () { attrs[+el.dataset.av].v = el.value; preview(); }); });
      S.$$('[data-arm]', box).forEach(function (el) { el.addEventListener('click', function () { attrs.splice(+el.dataset.arm, 1); drawAttrs(); preview(); }); });
    }
    $('#e-attr-add').addEventListener('click', function () { attrs.push({ k: '', v: '' }); drawAttrs(); });
    S.$$('[data-ak2]', d.el).forEach(function (b) {
      b.addEventListener('click', function () {
        if (!attrs.some(function (a) { return a.k === b.dataset.ak2; })) attrs.push({ k: b.dataset.ak2, v: '' });
        drawAttrs();
        var inputs = S.$$('[data-av]', d.el); if (inputs.length) inputs[inputs.length - 1].focus();
      });
    });

    var libOpen = false;
    function mediaFiles() {
      var byId = {}; kb.files.forEach(function (f) { byId[f.id] = f; });
      return picked.map(function (id) { return byId[id]; }).filter(Boolean);
    }
    function drawMedia() {
      var box = $('#e-media'); if (!box) return;
      var pics = kb.files.filter(function (f) { return /^(image|video)\//.test(f.mime_type || ''); });
      var byId = {}; pics.forEach(function (f) { byId[f.id] = f; });
      var mine = picked.filter(function (id) { return byId[id]; });
      var rest = pics.filter(function (f) { return picked.indexOf(f.id) < 0; });
      var tile = function (f, on, i) {
        return '<div class="st-media-item' + (on ? ' on' : '') + '" data-mid="' + esc(f.id) + '" title="' + (on ? 'Убрать из позиции' : 'Добавить в позицию') + '">' +
          (f.preview_url ? mediaThumb(f.preview_url, /^video\//.test(f.mime_type || '')) : '<span class="st-ph">' + icon('play') + '</span>') +
          (on && i === 0 ? '<span class="main">в карточке</span>' : '') +
          (on && i > 0 ? '<button type="button" class="mk" data-main="' + esc(f.id) + '" title="Сделать главным">' + icon('star') + '</button>' : '') +
          '<span class="chk">' + icon(on ? 'x' : 'plus') + '</span></div>';
      };
      var html = mine.length ? '<div class="st-media">' + mine.map(function (id, i) { return tile(byId[id], true, i); }).join('') + '</div>'
        : '<div class="hint">У позиции пока нет фото и видео — бот пришлёт карточку текстом.</div>';
      if (rest.length) {
        html += '<button class="btn btn-ghost btn-sm" type="button" id="e-lib" style="margin-top:8px">' + icon(libOpen ? 'chevron-up' : 'chevron-down') +
          '<span>' + (libOpen ? 'Скрыть ранее загруженные' : 'Выбрать из ранее загруженных (' + rest.length + ')') + '</span></button>';
        if (libOpen) html += '<div class="st-media st-media-lib" style="margin-top:6px">' + rest.map(function (f) { return tile(f, false, 1); }).join('') + '</div>';
      }
      box.innerHTML = html;
      S.$$('[data-main]', box).forEach(function (b) {
        b.addEventListener('click', function (ev) { ev.stopPropagation(); var id = b.dataset.main; picked.splice(picked.indexOf(id), 1); picked.unshift(id); drawMedia(); preview(); });
      });
      S.$$('[data-mid]', box).forEach(function (el) {
        el.addEventListener('click', function (ev) {
          if (ev.target.closest('[data-main]')) return;
          var i = picked.indexOf(el.dataset.mid);
          if (i >= 0) picked.splice(i, 1); else picked.push(el.dataset.mid);
          drawMedia(); preview();
        });
      });
      var lb = S.$('#e-lib', box); if (lb) lb.addEventListener('click', function () { libOpen = !libOpen; drawMedia(); });
    }
    $('#e-up').addEventListener('click', function () {
      pickFiles('image/*,video/*', true, function (rows) {
        rows.forEach(function (f) { picked.push(f.id); });
        S.api('/files?source=library').then(function (r) { if (r.ok) kb.files = r.data.files || []; drawMedia(); preview(); });
      });
    });

    // Превью карточки — та же логика, что catalog_service.product_card_text на сервере
    function preview() {
      var title = $('#p-title').value.trim() || 'Название товара';
      var p = parseFloat($('#p-price').value), o = parseFloat($('#p-old').value), cur = $('#p-cur').value;
      var lines = ['<b>' + esc(title) + '</b>'];
      if (p >= 0 && $('#p-price').value !== '') {
        lines.push(o > p ? 'Цена: ' + esc(money(p, cur)) + ' вместо ' + esc(money(o, cur)) + ', скидка ' + Math.round((1 - p / o) * 100) + '%' : 'Цена: ' + esc(money(p, cur)));
      }
      var inStock, haveSizes = [], haveColors = [], left = null;
      if (stockMode === 'variants' && buildVariants().length) {
        var v = buildVariants();
        left = v.reduce(function (a, x) { return a + x.qty; }, 0);
        inStock = left > 0;
        v.forEach(function (x) { if (x.qty > 0) { if (x.size && haveSizes.indexOf(x.size) < 0) haveSizes.push(x.size); if (x.color && haveColors.indexOf(x.color) < 0) haveColors.push(x.color); } });
      } else {
        var st = $('#p-stock').value;
        left = st === '' ? null : Number(st);
        inStock = st === '' ? $('#p-instock').value === '1' : Number(st) > 0;
        if (inStock) haveSizes = sizes.slice();
      }
      if (!inStock) lines.push('Сейчас нет в наличии');
      else {
        if (haveSizes.length) lines.push('Размеры в наличии: ' + esc(haveSizes.join(', ')));
        if (haveColors.length) lines.push('Цвета: ' + esc(haveColors.join(', ')));
        if (left != null && left > 0 && left <= LOW) lines.push('Осталось ' + left + ' шт.');
      }
      attrs.filter(function (a) { return a.k.trim() && a.v.trim(); }).slice(0, 3).forEach(function (a) { lines.push(esc(a.k.trim()) + ': ' + esc(a.v.trim())); });
      var desc = $('#p-body').value.replace(/\s+/g, ' ').trim();
      if (desc) lines.push('<span class="d">' + esc(desc.length > 350 ? desc.slice(0, 350).replace(/\s+\S*$/, '') + '…' : desc) + '</span>');
      $('#pv-cap').innerHTML = lines.join('<br>');
      var f = mediaFiles()[0];
      $('#pv-img').innerHTML = f && f.preview_url ? mediaThumb(f.preview_url, /^video\//.test(f.mime_type || '')) : '<span>' + icon('image') + 'без фото — карточка уйдёт текстом</span>';
      $('#pv-img').classList.toggle('none', !(f && f.preview_url));
      // Готовность
      var checks = [
        [!!$('#p-title').value.trim(), 'Название'],
        [!!f, 'Фото — клиент видит товар'],
        [$('#p-price').value !== '', 'Цена — бот её назовёт'],
        [!!desc, 'Описание — 1–2 фразы в карточку'],
        [stockMode === 'variants' ? buildVariants().length > 0 : ($('#p-stock').value !== '' || true), stockMode === 'variants' ? 'Остаток по размерам' : 'Наличие'],
        [($('#p-col').value && $('#p-col').value !== '__new') || !catCollections().length, 'Коллекция']
      ];
      var okN = checks.filter(function (c) { return c[0]; }).length;
      $('#pv-ready').innerHTML = '<div class="st-ready-h"><b>Готовность ' + okN + ' из ' + checks.length + '</b><span class="bar"><i style="width:' + Math.round(okN * 100 / checks.length) + '%"></i></span></div>' +
        checks.map(function (c) { return '<div class="' + (c[0] ? 'ok' : 'no') + '">' + icon(c[0] ? 'circle-check' : 'circle') + '<span>' + esc(c[1]) + '</span></div>'; }).join('');
    }

    drawAttrs(); drawSizes(); drawColors(); drawMedia();
    previewReady = true;
    setStockMode(stockMode);

    function collect() {
      var title = $('#p-title').value.trim();
      if (!title) { S.toast('Укажите название', 'error'); $('#p-title').focus(); return null; }
      var obj = {};
      Object.keys(hidden).forEach(function (k) { obj[k] = hidden[k]; });
      var skuV = $('#p-sku').value.trim(); if (skuV) obj['Артикул'] = skuV;
      attrs.forEach(function (a) { var k = (a.k || '').trim(); if (k && (a.v || '').trim()) obj[k] = a.v.trim(); });
      var p = $('#p-price').value, o = $('#p-old').value, st = $('#p-stock').value;
      var body = {
        kind: 'product', title: title, body: $('#p-body').value.trim(), keywords: $('#p-kw').value.trim(),
        collection: $('#p-col').value === '__new' ? '' : $('#p-col').value,
        price: p === '' ? null : Number(p), old_price: o === '' ? null : Number(o), currency: $('#p-cur').value,
        sizes: sizes.slice(), media_file_ids: picked.slice(), attributes: obj, is_active: $('#p-active').checked, in_prompt: false
      };
      if ($('#p-dur')) { var dur = parseInt($('#p-dur').value, 10); body.duration_min = dur > 0 ? dur : null; }
      if (stockMode === 'variants' && buildVariants().length) {
        body.variants = buildVariants();
      } else {
        body.variants = [];
        body.stock_qty = st === '' ? null : Math.max(0, parseInt(st, 10) || 0);
        body.in_stock = st === '' ? $('#p-instock').value === '1' : Number(st) > 0;
      }
      return body;
    }
    function save(more, btn) {
      var body = collect(); if (!body) return;
      btn.disabled = true;
      S.api(e ? '/knowledge/' + e.id : '/knowledge', { method: e ? 'PUT' : 'POST', body: body }).then(function (r) {
        btn.disabled = false;
        if (!r.ok) { S.toast(S.apiError(r), 'error'); return; }
        if (gapId) S.api('/knowledge/gaps/' + gapId, { method: 'DELETE' });
        d.close(); S.toast('Сохранено', 'success');
        loadKb().then(function () {
          // Следующая позиция той же коллекции: коллекция, валюта и размеры остаются
          if (more) productDialog(null, { collection: body.collection, currency: body.currency, sizes: body.sizes });
        });
      });
    }
    $('#p-save').addEventListener('click', function () { save(false, this); });
    var sm = $('#p-save-more'); if (sm) sm.addEventListener('click', function () { save(true, this); });
    setTimeout(function () { var i = $('#p-title'); if (i && !e) i.focus(); }, 60);
  }

  // Выбор и загрузка файлов в библиотеку бота → cb(список созданных файлов)
  function pickFiles(accept, multiple, cb) {
    if (!kb.storage) { S.toast('Хранилище файлов не настроено на сервере', 'error'); return; }
    var inp = document.createElement('input');
    inp.type = 'file'; inp.accept = accept || ''; inp.multiple = !!multiple;
    inp.addEventListener('change', function () {
      var list = Array.prototype.slice.call(inp.files || []);
      if (!list.length) return;
      var done = [];
      (function next() {
        if (!list.length) { cb(done); return; }
        var f = list.shift();
        S.toast('Загружаю: ' + f.name);
        S.upload('/files', f).then(function (r) {
          if (!r.ok) S.toast(S.errText(r.data.detail === 'file_too_large' ? 'Файл слишком большой' : r.data.detail === 'library_limit_reached' ? 'В библиотеке больше нельзя хранить файлов' : r.data.detail || r.data), 'error');
          else done.push(r.data.file);
          next();
        });
      })();
    });
    inp.click();
  }


  window.StartCatalog = {
    init: function (o) {
      if (o && o.kb) kb = o.kb;
      if (o && o.reload) loadKb = o.reload;
      if (o) catOpts = o;
    },
    draw: drawCatalog, productDialog: productDialog, mediaViewer: mediaViewer, mediaThumb: mediaThumb,
    pickFiles: pickFiles, curWord: curWord, priceText: priceText, CURRENCIES: CURRENCIES
  };
})();
