    tailwind.config = {
      theme: {
        extend: {
          colors: {
            primary: { DEFAULT: '#4f46e5', hover: '#4338ca', dark: '#3730a3' },
            secondary: '#64748b',
            light: '#f8f9fa',
            dark: '#343a40',
          },
        }
      }
    }


  document.addEventListener('DOMContentLoaded', function() {

    // ======================= ГЛОБАЛЬНЫЕ УТИЛИТЫ =======================
    const flashContainer = document.getElementById('flash-container');

    window.showFlash = function(message, category = 'info') {
      const colors = {'info': 'blue', 'success': 'green', 'danger': 'red', 'warning': 'yellow'};
      const alert = document.createElement('div');
      alert.className = `alert-message bg-white/80 backdrop-blur-sm border-l-4 border-${colors[category] || 'gray'}-500 text-gray-800 p-4 rounded-lg shadow-xl flex items-start justify-between`;
      alert.setAttribute('role', 'alert');
      alert.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
      alert.style.opacity = '0';
      alert.style.transform = 'translateX(20px)';
      alert.innerHTML = `
        <div class="pr-3"><p class="font-semibold text-sm">${message}</p></div>
        <button type="button" class="alert-close ml-2 text-gray-400 hover:text-gray-600" aria-label="Закрыть"><i class="bi bi-x-lg"></i></button>
      `;

      flashContainer.prepend(alert);
      requestAnimationFrame(() => {
          alert.style.opacity = '1';
          alert.style.transform = 'translateX(0)';
      });

      const timer = setTimeout(() => {
          alert.style.opacity = '0';
          setTimeout(() => alert.remove(), 300);
      }, 5000);

      alert.querySelector('.alert-close')?.addEventListener('click', () => {
          clearTimeout(timer);
          alert.remove();
      }, { once: true });
    };

    window.closeModal = function(modal) {
        if (modal) {
            modal.classList.add('hidden');
            modal.classList.remove('flex');
        }
    };

    // Утилита для плавной вставки элемента
    function appendAndReveal(container, html) {
        if (!container) return null;
        const temp = document.createElement('div');
        temp.innerHTML = html.trim();
        const el = temp.firstElementChild;
        if(!el) return null;

        el.style.opacity = '0';
        el.style.transform = 'translateY(10px)';
        el.style.transition = 'all .3s ease-out';

        container.prepend(el);

        requestAnimationFrame(() => {
            el.style.opacity = '1';
            el.style.transform = 'translateY(0)';
        });

        const acc = container.closest('.accordion-content');
        if (acc && acc.style.maxHeight) {
          acc.style.maxHeight = acc.scrollHeight + 'px';
        }
        return el;
    }

    // ======================= ИНИЦИАЛИЗАЦИЯ UI =======================
    (function setupUI() {
        // --- Навигация ---
        const navLinks = document.querySelectorAll('.sidebar-link');
        const contentSections = document.querySelectorAll('.content-section');
        const mobileHeaderTitle = document.getElementById('mobile-header-title');
        const sidebar = document.getElementById('sidebar');
        const overlay = document.getElementById('sidebar-overlay');

        function closeSidebar() {
            if(sidebar) sidebar.classList.add('-translate-x-full');
            if(overlay) overlay.classList.add('hidden');
        }

        function switchContent(targetId) {
            contentSections.forEach(s => s.classList.remove('active'));
            navLinks.forEach(l => l.classList.remove('active'));
            const activeSection = document.getElementById(targetId + '-content');
            const activeLink = document.querySelector(`.sidebar-link[data-target="${targetId}"]`);
            if (activeSection) activeSection.classList.add('active');
            if (activeLink) {
                activeLink.classList.add('active');
                if (mobileHeaderTitle) mobileHeaderTitle.textContent = activeLink.querySelector('span').textContent;
            }
            localStorage.setItem('activeTab', targetId);
            if (window.innerWidth < 768) closeSidebar();
        }

        navLinks.forEach(link => link.addEventListener('click', (e) => {
            e.preventDefault();
            switchContent(link.dataset.target);
        }));

        switchContent(localStorage.getItem('activeTab') || 'dashboard');

        document.getElementById('hamburger-btn')?.addEventListener('click', e => {
            e.stopPropagation();
            sidebar.classList.toggle('-translate-x-full');
            overlay.classList.toggle('hidden');
        });
        overlay?.addEventListener('click', closeSidebar);

        // --- Вложенные вкладки ---
        document.querySelectorAll('[id$="-sub-tabs"]').forEach(container => {
            const buttons = container.querySelectorAll('.sub-tab-button');
            const parent = container.closest('.content-section');
            const contents = parent.querySelectorAll('.sub-tab-content');

            function switchSubTab(targetId) {
                contents.forEach(c => c.classList.remove('active'));
                buttons.forEach(b => b.classList.remove('active'));
                const content = parent.querySelector(`#${targetId}-content`);
                const button = container.querySelector(`[data-sub-target="${targetId}"]`);
                if(content) content.classList.add('active');
                if(button) button.classList.add('active');
            }
            buttons.forEach(b => b.addEventListener('click', e => {
                e.preventDefault();
                switchSubTab(b.dataset.subTarget);
            }));
            if (buttons.length > 0) switchSubTab(buttons[0].dataset.subTarget);
        });

        // --- Модальные окна и Аккордеоны (делегирование) ---
        document.body.addEventListener('click', function(e) {
            const openBtn = e.target.closest('[data-modal-target]');
            if (openBtn) {
                e.preventDefault();
                const modal = document.getElementById(openBtn.dataset.modalTarget);
                if (modal) {
                    modal.classList.remove('hidden');
                    modal.classList.add('flex');
                }
                return;
            }

            const closeBtn = e.target.closest('[data-modal-close]');
            if (closeBtn) {
                window.closeModal(closeBtn.closest('.modal'));
                return;
            }

            if (e.target.classList.contains('modal')) {
                 window.closeModal(e.target);
                 return;
            }

            const accordionHeader = e.target.closest('.accordion-header');
            if (accordionHeader) {
                const content = accordionHeader.nextElementSibling;
                const icon = accordionHeader.querySelector('.accordion-icon');
                const isOpen = content.style.maxHeight && content.style.maxHeight !== '0px';
                content.style.maxHeight = isOpen ? null : content.scrollHeight + 'px';
                if (icon) icon.classList.toggle('rotate-180', !isOpen);
            }
        });

        // --- Sortable.js ---
        document.querySelectorAll('.quiz-list').forEach(list => {
          new Sortable(list, { animation: 150, ghostClass: 'sortable-ghost', dragClass: 'sortable-drag' });
        });

        // --- Поиск (Фильтрация) ---
        [
          ['searchEmployees', '#employeesTable tbody tr'],
          ['searchArchivedEmployees', '#archivedEmployeesTable tbody tr'],
          ['searchAttendance', '#attendanceTable tbody tr'],
          ['searchEvents', '#eventsTable tbody tr'],
          ['searchIdeas', '#ideasList > div:not(#no-ideas-message)'],
          ['searchKnowledge', '#knowledgeTable tbody tr'],
          ['searchRoles', '#rolesList li:not(#no-roles-message)'],
        ].forEach(([inputId, selector]) => {
          const input = document.getElementById(inputId);
          if (!input) return;
          input.addEventListener('input', () => {
            const filter = input.value.toLowerCase();
            document.querySelectorAll(selector).forEach(el => {
                el.style.display = el.textContent.toLowerCase().includes(filter) ? '' : 'none';
            });
          });
        });

        document.querySelectorAll('.search-quiz').forEach(input => {
          input.addEventListener('input', () => {
            const filter = input.value.toLowerCase();
            const list = input.nextElementSibling;
            if(list && list.classList.contains('quiz-list')) {
                list.querySelectorAll('.quiz-card').forEach(card => {
                  card.style.display = card.textContent.toLowerCase().includes(filter) ? '' : 'none';
                });
            }
          });
        });

        // --- Логика для форм квизов (свободный ввод/выбор) ---
        document.querySelectorAll('.add-quiz-form, form[action*="/quiz/edit/"]').forEach(form => {
            const qtype = form.querySelector('.quiz-type');
            const textBlock = form.querySelector('.text-answer');
            const textInput = textBlock?.querySelector('input');
            const choiceSec = form.querySelector('.choice-section');
            const optsCont = choiceSec?.querySelector('.options-container');
            const addBtn = choiceSec?.querySelector('.add-option');
            const hidOpts = form.querySelector('.hidden-options');
            const hidAns = form.querySelector('.hidden-answer');

            if (!qtype) return;

            function toggleSections() {
                const isChoice = qtype.value === 'choice';
                if(choiceSec) choiceSec.classList.toggle('hidden', !isChoice);
                if(textBlock) textBlock.classList.toggle('hidden', isChoice);
            }

            function addOption(value = '', isChecked = false) {
                if(!optsCont) return;
                const wrap = document.createElement('div');
                wrap.className = 'flex items-center space-x-2';
                wrap.innerHTML = `
                  <input type="radio" name="correct_radio" class="text-primary focus:ring-primary" ${isChecked ? 'checked' : ''}>
                  <input type="text" placeholder="Вариант" value="${value}" class="flex-1 rounded border-gray-300 px-2 py-1 focus:ring-primary focus:border-primary">
                  <button type="button" class="remove-opt text-red-600 p-1">×</button>`;
                wrap.querySelector('.remove-opt').addEventListener('click', () => wrap.remove());
                optsCont.append(wrap);
            }

            if(optsCont && optsCont.children.length === 0) { addOption(); addOption(); }
            addBtn?.addEventListener('click', () => addOption());
            qtype.addEventListener('change', toggleSections);

            form.addEventListener('submit', e => {
                if (qtype.value !== 'choice') {
                  if (textInput && !textInput.value.trim()) { e.preventDefault(); window.showFlash('Введите правильный ответ', 'danger'); }
                  return;
                }
                const texts = Array.from(optsCont.querySelectorAll('input[type="text"]')).map(i => i.value.trim()).filter(Boolean);
                const radios = Array.from(optsCont.querySelectorAll('input[type="radio"]'));
                const chosenIdx = radios.findIndex(r => r.checked);

                if (texts.length < 1) { e.preventDefault(); window.showFlash('Добавьте хотя бы 1 вариант ответа', 'danger'); return; }
                if (chosenIdx === -1) { e.preventDefault(); window.showFlash('Выберите правильный вариант', 'danger'); return; }

                if(hidOpts) hidOpts.value = texts.join(';');
                if(hidAns) hidAns.value = texts[chosenIdx];
            });
            toggleSections();
        });

    })();

    // ======================= AJAX FORM HANDLER =======================
    document.body.addEventListener('submit', async function(e) {
        const form = e.target;
        if (!form.matches('form[data-action]')) return;

        e.preventDefault();

        const confirmText = form.dataset.confirm;
        if (confirmText && !confirm(confirmText)) return;

        const submitButton = e.submitter || form.querySelector('button[type="submit"], [type="submit"]');
        let originalBtnHTML;

        if (submitButton) {
            submitButton.disabled = true;
            originalBtnHTML = submitButton.innerHTML;
            submitButton.innerHTML = `<span class="loader"></span> Выполняется...`;
        }

        try {
            // Специальная обработка для JSON-запросов (сортировка)
            if (form.dataset.action.startsWith('reorder')) {
                const role = form.dataset.role;
                const listId = {
                  'reorder-quiz': `quiz-list-${role}`,
                  'reorder-onboarding-question': `onboarding-questions-list-${role}`,
                  'reorder-onboarding-step': `onboarding-steps-list-${role}`
                }[form.dataset.action];

                const listEl = document.getElementById(listId);
                const ids = Array.from(listEl.children).map(div => div.dataset.id);

                const response = await fetch(form.action, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ordered_ids: ids})
                });
                const data = await response.json();
                 if (response.ok) {
                    window.showFlash('Порядок сохранен!', 'success');
                } else {
                    window.showFlash(data.message || 'Ошибка сохранения порядка', 'danger');
                }

            } else { // Стандартная обработка FormData
                const formData = new FormData(form);
                const response = await fetch(form.action, {
                    method: form.method.toUpperCase(),
                    body: formData,
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                const data = response.status !== 204 ? await response.json() : {};

                if (data.message) {
                    window.showFlash(data.message, data.category);
                }

                if (response.ok) {
                    if (data.action === 'reload' || form.dataset.action === 'reload') {
                        const modal = form.closest('.modal');
                        if (modal) {
                            window.closeModal(modal);
                            setTimeout(() => window.location.reload(), 300);
                        } else {
                           window.location.reload();
                        }
                    } else {
                        handleActionSuccess(form.dataset.action, data, form);
                    }
                } else if (!data.message) {
                    window.showFlash('Ошибка выполнения операции.', 'danger');
                }
            }
        } catch (error) {
            console.error('Fetch error:', error);
            window.showFlash('Произошла сетевая ошибка. Попробуйте снова.', 'danger');
        } finally {
            if (submitButton) {
                if (originalBtnHTML != null) submitButton.innerHTML = originalBtnHTML;
                submitButton.disabled = false;
            }
        }
    });

    function handleActionSuccess(action, data, form) {
        const modal = form.closest('.modal');

        const animateRemove = (el) => {
            if (!el) return;
            el.style.transition = 'opacity 0.3s ease, transform 0.3s ease, max-height 0.3s ease';
            el.style.opacity = '0';
            el.style.transform = 'scale(0.95)';
            el.style.maxHeight = '0px';
            el.style.paddingTop = '0px';
            el.style.paddingBottom = '0px';
            el.style.marginTop = '0px';
            el.style.marginBottom = '0px';
            setTimeout(() => el.remove(), 300);
        };

        switch (action) {
            case 'delete-item': {
                animateRemove(form.closest(form.dataset.parentSelector));
                break;
            }
            case 'dismiss-employee': {
                animateRemove(document.querySelector(form.dataset.parentSelector));
                if(modal) window.closeModal(modal);
                // Optionally add to archive list dynamically
                break;
            }
            case 'add-role': {
                const newRole = data.role;
                if (!newRole) return;
                document.getElementById('no-roles-message')?.remove();

                const list = document.getElementById('rolesList');
                if(list) {
                    const liHTML = `
                    <li class="flex justify-between items-center p-3 bg-gray-50 rounded-lg border">
                      <span>${newRole.name}</span>
                      <form action="/role/delete/${newRole.id}" method="POST" data-action="delete-item" data-confirm="Удалить роль ${newRole.name}?" data-parent-selector="li">
                        <button type="submit" class="p-1 text-gray-400 hover:text-red-600"><i class="bi bi-trash"></i></button>
                      </form>
                    </li>`;
                    list.insertAdjacentHTML('beforeend', liHTML);
                }

                // For a true SPA experience, we would create new DOM nodes for accordions.
                // As a compromise, we just show a success message. Reload will show the new role everywhere.
                window.showFlash('Роль добавлена. Новые разделы для нее появятся после перезагрузки страницы.', 'info');
                form.reset();
                break;
            }
            case 'add-employee': {
                 const newEmp = data.employee;
                 if (!newEmp) break;
                 document.getElementById('no-employees-row')?.remove();
                 const tableBody = document.querySelector('#employeesTable tbody');
                 const newRowHTML = `
                   <tr class="bg-white border-b hover:bg-gray-50" data-employee-id="${newEmp.id}">
                     <td class="px-6 py-4 font-medium text-gray-900 whitespace-nowrap">
                       <div class="font-semibold name-cell">${newEmp.name}</div>
                       <div class="text-xs text-gray-500 email-cell">${newEmp.email}</div>
                     </td>
                     <td class="px-6 py-4 role-cell">
                       <span class="px-2 py-1 bg-indigo-100 text-primary font-medium rounded-full text-xs">${newEmp.role}</span>
                     </td>
                     <td class="px-6 py-4 status-cell">
                       <div class="flex flex-col space-y-1">
                         <span class="flex items-center text-xs font-medium text-gray-500"><i class="bi bi-x-circle-fill mr-1.5"></i> Telegram</span>
                         <span class="flex items-center text-xs font-medium text-gray-500"><i class="bi bi-x-circle-fill mr-1.5"></i> Тренинг</span>
                       </div>
                     </td>
                     <td class="px-6 py-4 text-right space-x-2 whitespace-nowrap">
                       <small class="text-gray-400">Действия доступны после перезагрузки</small>
                     </td>
                   </tr>
                 `;
                 appendAndReveal(tableBody, newRowHTML);
                 form.reset();
                 if(modal) window.closeModal(modal);
                 break;
            }
            case 'edit-employee': {
                 const emp = data.employee;
                 const row = document.querySelector(`tr[data-employee-id="${emp.id}"]`);
                 if(row) {
                     row.querySelector('.name-cell').textContent = emp.name;
                     row.querySelector('.email-cell').textContent = emp.email;
                     row.querySelector('.role-cell span').textContent = emp.role;
                 }
                 if(modal) window.closeModal(modal);
                 break;
            }
            case 'add-quiz': {
                const role = form.dataset.role;
                const list = document.getElementById(`quiz-list-${role}`);
                if (!list) break;

                list.querySelector('.no-quizzes-message')?.remove();

                const item = data.item;
                const newQuizHTML = `
                <div class="quiz-card flex justify-between items-start bg-white p-3 rounded-lg shadow-sm border" data-id="${item.id}">
                  <div>
                    <p class="font-semibold">${item.question}</p>
                    <p class="text-green-700 text-sm">Ответ: ${item.answer}</p>
                  </div>
                  <div class="flex space-x-2">
                    <small class="text-gray-400 text-xs p-1">Ред. после F5</small>
                    <form action="${item.delete_url}" method="POST" data-action="delete-item" data-confirm="Удалить вопрос?" data-parent-selector="div.quiz-card">
                      <button type="submit" class="p-1 text-gray-400 hover:text-red-600"><i class="bi bi-trash"></i></button>
                    </form>
                  </div>
                </div>`;
                appendAndReveal(list, newQuizHTML);
                form.reset();
                // Сброс полей для квиза с выбором
                const choiceSection = form.querySelector('.choice-section');
                if(choiceSection) {
                    choiceSection.querySelector('.options-container').innerHTML = '';
                    const qtype = form.querySelector('.quiz-type');
                    if (qtype) {
                        qtype.value = 'text';
                        qtype.dispatchEvent(new Event('change'));
                    }
                }

                if (modal) window.closeModal(modal);
                break;
            }
            case 'add-onboarding-question': {
                const role = form.dataset.role;
                const list = document.getElementById(`onboarding-questions-list-${role}`);
                if (!list) break;

                list.querySelector('p')?.remove(); // Удаляем "пустое" сообщение если оно есть

                const item = data.item;
                const requiredText = item.is_required ? 'Обязательный' : 'Необязательный';
                const newQuestionHTML = `
                <div class="quiz-card flex justify-between items-start bg-white p-3 rounded-lg shadow-sm border" data-id="${item.id}">
                    <div>
                        <p class="font-semibold">${item.question_text}</p>
                        <p class="text-xs text-blue-600">Ключ: ${item.data_key} | ${requiredText}</p>
                    </div>
                    <form action="${item.delete_url}" method="POST" data-action="delete-item" data-confirm="Удалить вопрос?" data-parent-selector="div.quiz-card">
                      <button type="submit" class="p-1 text-gray-400 hover:text-red-600"><i class="bi bi-trash"></i></button>
                    </form>
                </div>`;
                appendAndReveal(list, newQuestionHTML);
                form.reset();
                if (modal) window.closeModal(modal);
                break;
            }
            case 'add-onboarding-step': {
                const role = form.dataset.role;
                const list = document.getElementById(`onboarding-steps-list-${role}`);
                if (!list) break;

                list.querySelector('p')?.remove();

                const item = data.item;
                const fileInfo = item.file_path ? `<p class="text-xs text-green-600">Файл: ${item.file_type}</p>` : '';
                const newStepHTML = `
                <div class="quiz-card flex justify-between items-start bg-white p-3 rounded-lg shadow-sm border" data-id="${item.id}">
                   <div>
                       <p class="font-medium">${item.message_text || "Только файл"}</p>
                       ${fileInfo}
                   </div>
                    <form action="${item.delete_url}" method="POST" data-action="delete-item" data-confirm="Удалить шаг?" data-parent-selector="div.quiz-card">
                      <button type="submit" class="p-1 text-gray-400 hover:text-red-600"><i class="bi bi-trash"></i></button>
                    </form>
                </div>`;
                appendAndReveal(list, newStepHTML);
                form.reset();
                if (modal) window.closeModal(modal);
                break;
            }
            // Simple actions that just show a message and close the modal
            case 'update-text':
            case 'simple-action':
            case 'send-broadcast':
            case 'update-config':
            case 'update-onboarding': {
                 if (modal) window.closeModal(modal);
                 break;
            }
            default: {
                if (modal) window.closeModal(modal);
            }
        }
    }
  });

  (function(){
    const modal = document.getElementById("bot-chats-modal");
    const openBtn = document.getElementById("bot-chats-open");
    const closeBtn = document.getElementById("bot-chats-close");
    const refreshBtn = document.getElementById("bot-chats-refresh");
    const tbody = document.getElementById("bot-chats-tbody");
    const status = document.getElementById("bot-chats-status");

    function open(){ modal.style.display='flex'; refresh(); }
    function close(){ modal.style.display='none'; }

    async function refresh(){
      status.textContent = "Загрузка…";
      tbody.innerHTML = "";
      refreshBtn.disabled = true;

      try{
        await fetch("/api/bot/chats/recheck", { method:"POST" }).catch(()=>{});
        const r = await fetch("/api/bot/chats");
        if(!r.ok) throw new Error("Failed to fetch chats");
        const j = await r.json();
        const items = Array.isArray(j?.data) ? j.data : [];

        if(items.length === 0){
          status.textContent = "Пока нет известных боту чатов. Добавьте бота в нужную группу.";
          return;
        }
        status.textContent = "";

        const fmt = (iso) => { try{ return new Date(iso).toLocaleString(); }catch(_){ return "—"; } };
        items.forEach(c => {
          const tr = document.createElement("tr");
          tr.innerHTML = `
            <td>${c.title || "—"}</td>
            <td style="font-family:ui-monospace,monospace">${c.chat_id}</td>
            <td>${c.type || "—"}</td>
            <td>${c.is_admin ? '<span class="badge badge-green">Да</span>' : '<span class="badge badge-gray">Нет</span>'}</td>
            <td>${c.username ? '@'+c.username : '—'}</td>
            <td style="color:#6b7280">${c.updated_at ? fmt(c.updated_at) : "—"}</td>
          `;
          tbody.appendChild(tr);
        });
      }catch(e){
        console.error(e);
        status.textContent = "Ошибка загрузки.";
      } finally {
        refreshBtn.disabled = false;
      }
    }

    openBtn.addEventListener("click", open);
    closeBtn.addEventListener("click", close);
    refreshBtn.addEventListener("click", refresh);
    modal.addEventListener("click", (e)=>{ if(e.target===modal) close(); });
  })();
