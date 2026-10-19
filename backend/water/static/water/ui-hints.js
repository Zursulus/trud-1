/* Shared internal help. No requests, form interception or permission decisions. */
(() => {
  'use strict';
  const mobile = window.matchMedia('(max-width: 760px)');
  const hover = window.matchMedia('(hover: hover)');
  const controls = 'a[href], button, input[type="submit"], input[type="button"], input[type="reset"], summary, [role="button"]';
  const records = new WeakMap();
  let serial = 0;
  let active = null;
  let pinned = null;
  const bubble = document.createElement('div');
  bubble.className = 'hint-bubble';
  bubble.setAttribute('aria-hidden', 'true');
  bubble.hidden = true;
  document.body.append(bubble);

  const exact = {
    'Сохранить ник': 'Этот ник увидят участники обсуждений',
    'Отправить код': 'Код придёт на указанную почту',
    'Получить код': 'Код для входа придёт на почту',
    'Подтвердить': 'Введённый код будет проверен',
    'Подтвердить и войти': 'Код будет проверен для входа',
    'Отменить / запросить новый код': 'Текущий запрос кода будет отменён',
    'Выйти': 'Завершить сеанс на этом устройстве',
    'Выйти из кабинета': 'Завершить сеанс на этом устройстве',
    'Войти': 'Проверить данные и открыть вашу учётную запись',
    'Зарегистрироваться': 'Создать учётную запись для входа',
    'Найти': 'Показать записи по введённому запросу',
    'Показать': 'Обновить список по выбранным условиям',
    'Применить': 'Обновить список по выбранным условиям',
    'Передать показание': 'Передать показание сотруднику для проверки',
    'Отправить введённые показания на проверку': 'Показания поступят сотруднику для проверки',
    'Проверить и сохранить заполненные строки': 'Проверить заполненные строки и записать показания',
    'Проверить без записи': 'Проверить данные без сохранения изменений',
    'Проверить, что получится': 'Показать результат без записи данных',
    'Только проверить файл': 'Проверить файл без записи данных',
    'Проверить пакет': 'Проверить файл перед загрузкой данных',
    'Импортировать': 'Внимание: данные файла будут записаны',
    'Принять показание': 'Внимание: показание будет учтено как официальное',
    'Отклонить': 'Внимание: заявка не будет принята',
    'Проверено': 'Записать результат проверки выбранной записи',
    'Есть расхождение': 'Открыть поля для описания расхождения',
    'Отметить расхождение': 'Сохранить замечание к проверяемым показаниям',
    'Сохранить и дальше →': 'Сохранить запись и открыть следующий счётчик',
    'Сохранить и отправить на проверку': 'Записать показания и передать на проверку',
    'Показать баланс': 'Открыть расход воды и разницу показаний',
    'Предварительный баланс': 'Раскрыть расчёт расхода и потерь воды',
    'Подтверждаю: исправить привязку': 'Внимание: изменится привязка показания к счётчику',
    'Печать / сохранить в PDF': 'Открыть настройки печати этого документа',
    'Скачать внесённые CSV': 'Скачать файл с внесёнными показаниями',
    'Рассчитать / пересчитать черновики': 'Внимание: черновики начислений будут пересчитаны',
    'Утвердить период': 'Внимание: начисления периода будут утверждены',
    'Закрыть период': 'Внимание: расчётный период будет закрыт',
    'Утвердить': 'Внимание: выбранное начисление будет утверждено',
    'Сохранить на проверку': 'Записать оплату без окончательного подтверждения',
    'Подтвердить оплату': 'Внимание: оплата будет подтверждена в учёте',
    'Распределить по правилам': 'Оплата будет распределена по начислениям',
    'Отменить': 'Внимание: черновик начисления будет отменён',
    'Отправить': 'Введённые данные будут переданы для обработки',
    '➤': 'Сообщение будет отправлено в переписку обращения',
    'Отменить подтверждение': 'Внимание: подтверждение оплаты будет отменено',
    'Сохранить ответ': 'Записать ваш ответ в предварительном опросе',
    'Обновить ответ': 'Изменить ответ, сохранив предыдущий в истории',
    'Добавить комментарий': 'Комментарий увидят участники этой переписки',
    'Отправить сообщение': 'Сообщение появится в переписке обращения',
    'Отправить обращение': 'Обращение получат ответственные сотрудники ТСН',
    'Закрыть обращение': 'Внимание: обращение будет отмечено закрытым',
    'Закрыть опрос': 'Внимание: новые ответы больше не принимаются',
    'Создать опрос': 'Сохранить новый предварительный опрос правления',
    'Прикрепить протокол': 'Добавить файл протокола к этому опросу',
    'Опубликовать': 'Внимание: материал станет доступен читателям сайта',
    'Выдать назначение': 'Внимание: человеку будут выданы выбранные рабочие права',
    'Сохранить функции': 'Внимание: изменится набор функций рабочей роли',
    'Сохранить права': 'Внимание: изменятся выбранные права жителя',
    'Подтвердить делегирование': 'Внимание: выбранные личные права получит другой человек',
    'Связать существующий логин': 'Внимание: логин будет связан с этим человеком',
    'Сменить связанный логин': 'Внимание: изменится учётная запись этого человека',
    'Подтвердить связь': 'Внимание: подтвердится связь человека с логином',
    'Создать и привязать': 'Создать счётчик и связать с выбранным объектом',
    'Создать и перейти к правам': 'Создать карточку человека и открыть настройку доступа',
    'Создать защищённый доступ': 'Создать доступ с указанными вами правами',
    'Подключить участок': 'Подключить участок после проверки введённых данных',
    'Предпросмотр': 'Посмотреть материал до его публикации',
    'Сохранить новый пароль': 'Внимание: для входа понадобится новый пароль',
    'Сохранить изменения': 'Записать изменения из заполненной формы',
    'Сохранить': 'Записать данные из заполненной формы',
    'Сохранить параметры': 'Записать выбранные настройки этого опроса',
    'Сохранить документ': 'Записать документ с выбранными настройками доступа',
    'Сохранить новость': 'Записать новость с выбранными настройками публикации',
    'Сохранить метаданные': 'Изменить описание и реквизиты документа',
    'Создать документ': 'Добавить документ с выбранными настройками доступа',
    'Сегодня': 'Подставить сегодняшнюю дату в форму',
    'Вчера': 'Подставить вчерашнюю дату в форму',
    'Позавчера': 'Подставить дату за позавчера в форму',
    'Показать пароль': 'Символы пароля станут видны на экране',
    'Скрыть пароль': 'Символы пароля будут скрыты точками',
    '＋ Вложить': 'Выбрать файл для добавления к сообщению',
    '⚡ Быстрый ответ': 'Открыть готовые фразы для ответа',
    '😊': 'Выбрать смайлик для текста сообщения',
    '×': 'Закрыть открытый блок или окно',
  };

  function labelOf(el) {
    return (el.getAttribute('aria-label') || (el.matches('input') ? el.value : el.textContent) || '').replace(/\s+/g, ' ').trim();
  }

  function hintFor(el) {
    const label = labelOf(el);
    if (el.matches('summary')) return 'Раскрыть или свернуть подробности этого раздела';
    if (el.hasAttribute('data-insert-text')) return 'Вставить выбранный текст в ваше сообщение';
    if (el.hasAttribute('data-file-remove')) return 'Убрать файл из ещё не отправленного сообщения';
    if (el.hasAttribute('data-panel-target')) return 'Открыть варианты для вставки в сообщение';
    if (el.matches('button[name="decision"][value="confirm"]')) return 'Подтвердить сверку показания на вашей линии';
    if (el.matches('button[name="decision"][value="flag"]')) return 'Записать расхождение в показании жителя';
    if (el.matches('a')) {
      if (label === 'Опубликовать') return 'Открыть форму настройки публикации материала';
      if (/удалить/i.test(label)) return 'Внимание: откроется подтверждение удаления данных';
      if (/передать показание/i.test(label)) return 'Открыть форму передачи показания на проверку';
      if (/сменить пароль/i.test(label)) return 'Открыть форму установки нового пароля';
    }
    if (exact[label]) return exact[label];
    if (/удалить|удаление/i.test(label)) return 'Внимание: выбранные данные будут удалены';
    if (/отменить.*оплат|сторнир/i.test(label)) return 'Внимание: запись об оплате будет отменена';
    if (/завершить доступ|завершить|отозвать/i.test(label)) return 'Внимание: выбранный доступ перестанет действовать';
    if (/отклонить/i.test(label)) return 'Внимание: заявка будет отклонена с указанной причиной';
    if (/одобрить.*приглашение/i.test(label)) return 'Одобрить заявку и создать ссылку для входа';
    if (/восстановлен/i.test(label) && /создать|выдать/i.test(label)) return 'Создать одноразовую ссылку для восстановления входа';
    if (/приглашение/i.test(label) && /создать|выдать/i.test(label)) return 'Создать одноразовое приглашение с выбранными правами';
    if (/экспорт|скачать|\.csv|\.xlsx/i.test(label)) return 'Скачать файл с выбранными данными';
    if (/сохранить/i.test(label)) return 'Записать данные из заполненной формы';
    if (/печат/i.test(label)) return 'Открыть настройки печати этой страницы';
    if (/назад|вернуться|←/.test(label.toLowerCase())) return 'Вернуться к предыдущему разделу';
    if (/добавить|создать/i.test(label)) return 'Открыть добавление новой записи';
    if (/изменить|редактировать/i.test(label)) return 'Открыть форму изменения выбранных данных';
    if (/продолжить|следующий|далее/i.test(label)) return 'Перейти к следующему шагу';
    if (el.matches('a')) {
      if (el.getAttribute('target') === '_blank') return 'Открыть страницу в новой вкладке';
      if ((el.getAttribute('href') || '').startsWith('#')) return 'Перейти к выбранному блоку страницы';
      if ((el.getAttribute('href') || '').startsWith('?')) return 'Показать записи по выбранным условиям';
      return 'Открыть выбранную запись или раздел';
    }
    if (el.matches('input[type="reset"]')) return 'Вернуть поля формы к исходным значениям';
    if (el.form && (el.form.method || '').toLowerCase() === 'get') return 'Показать записи по выбранным условиям';
    if (el.matches('button[type="button"], [role="button"]')) return 'Показать доступные варианты этого действия';
    return 'Отправить заполненную форму для обработки';
  }

  function hide() {
    bubble.hidden = true;
    active = null;
  }

  function unpin() {
    if (!pinned) return;
    pinned.description.hidden = true;
    pinned.description.classList.remove('hint-inline-note');
    pinned.toggle.setAttribute('aria-expanded', 'false');
    pinned = null;
  }

  function position(el) {
    const box = el.getBoundingClientRect();
    const bounds = bubble.getBoundingClientRect();
    const width = document.documentElement.clientWidth;
    const height = window.innerHeight;
    bubble.style.left = `${Math.max(8, Math.min(box.left, width - bounds.width - 8))}px`;
    const above = box.top - bounds.height - 8;
    bubble.style.top = `${Math.max(8, Math.min(above >= 8 ? above : box.bottom + 8, height - bounds.height - 8))}px`;
  }

  function show(el) {
    if (mobile.matches || !el.isConnected) return;
    const record = records.get(el);
    if (!record) return;
    bubble.textContent = record.description.textContent;
    bubble.hidden = false;
    active = el;
    position(el);
  }

  function enhance(el) {
    if (el.matches('.hint-toggle') || !el.isConnected) return;
    const previous = records.get(el);
    const explicit = previous ? previous.explicit : el.dataset.hint;
    const hint = explicit || hintFor(el);
    if (previous) {
      if (previous.description.textContent !== hint) {
        previous.description.textContent = hint;
        el.dataset.hint = hint;
        if (previous.ownTitle) el.title = hint;
        if (previous.toggle) previous.toggle.dataset.hint = hint;
        if (active === el) show(el);
      }
      return;
    }
    const description = document.createElement('span');
    description.id = `ui-hint-${++serial}`;
    description.className = 'hint-description';
    description.setAttribute('role', 'tooltip');
    description.textContent = hint;
    description.hidden = true;
    document.body.append(description);
    const ownTitle = !el.hasAttribute('title');
    el.dataset.hint = hint;
    if (ownTitle) el.title = hint;
    const ids = new Set((el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    ids.add(description.id);
    el.setAttribute('aria-describedby', [...ids].join(' '));
    const record = {description, explicit, ownTitle, toggle: null};
    records.set(el, record);
    el.addEventListener('mouseenter', () => { if (hover.matches) show(el); });
    el.addEventListener('mouseleave', () => { if (document.activeElement !== el) hide(); });
    el.addEventListener('focus', () => show(el));
    el.addEventListener('blur', hide);
    el.addEventListener('click', hide);

    // A separate, non-submitting help button only for less obvious / risky actions.
    const touchHelp = el.dataset.hintTouch === 'true' || /Внимание:/.test(hint) ||
      /Сохранить ник|Отправить код|Получить код|Подтвердить$|Распределить по правилам|Создать.*(?:приглашение|восстановления)|Выдать назначение|Проверено/.test(labelOf(el));
    if (touchHelp && !el.closest('nav, .internal-header') && !el.matches('summary')) {
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'hint-toggle';
      toggle.dataset.hint = hint;
      toggle.textContent = '?';
      toggle.setAttribute('aria-label', 'Подсказка');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-controls', description.id);
      toggle.setAttribute('aria-describedby', description.id);
      record.toggle = toggle;
      el.after(toggle);
      toggle.after(description);
      toggle.addEventListener('click', () => {
        const wasPinned = pinned === record;
        unpin();
        hide();
        if (!wasPinned) {
          pinned = record;
          description.classList.add('hint-inline-note');
          description.hidden = false;
          toggle.setAttribute('aria-expanded', 'true');
        }
      });
    }
  }

  function scan(root) {
    if (root.matches && root.matches(controls)) enhance(root);
    root.querySelectorAll(controls).forEach(enhance);
  }

  document.querySelectorAll('[data-help-key]').forEach(help => {
    const key = `trud:help:v1:${help.dataset.helpKey}`;
    try { help.hidden = localStorage.getItem(key) === 'hidden'; } catch (_) { /* Storage may be unavailable. */ }
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'page-help-close';
    close.textContent = '×';
    close.setAttribute('aria-label', 'Скрыть подсказки этого раздела');
    close.dataset.hint = 'Подсказки скроются на этом устройстве';
    help.append(close);
    close.addEventListener('click', () => {
      // Move focus out of the block before hiding its focused close button.
      const main = help.closest('main');
      if (main) { main.setAttribute('tabindex', '-1'); main.focus({preventScroll: true}); }
      help.hidden = true;
      try { localStorage.setItem(key, 'hidden'); } catch (_) { /* Hiding still works for this visit. */ }
    });
  });
  scan(document);
  new MutationObserver(changes => {
    changes.forEach(change => {
      if (change.type === 'attributes') {
        if (change.target.matches(controls)) enhance(change.target);
      } else {
        change.addedNodes.forEach(node => { if (node.nodeType === 1) scan(node); });
      }
    });
  }).observe(document.body, {childList: true, subtree: true, attributes: true, attributeFilter: ['aria-label']});
  document.addEventListener('click', event => {
    if (pinned && event.target !== pinned.toggle && !pinned.description.contains(event.target)) unpin();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') { hide(); unpin(); }
  });
  window.addEventListener('resize', () => { hide(); unpin(); });
  window.addEventListener('scroll', hide, true);
})();
