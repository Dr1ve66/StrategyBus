function showErrorToast() {
  const node = document.getElementById('errorToast');
  if (node) bootstrap.Toast.getOrCreateInstance(node).show();
}

function showAiOverlay() {
  const overlay = document.getElementById('aiOverlay');
  if (overlay) overlay.classList.remove('d-none');
}

async function postJson(url, body) {
  const authToken = new URLSearchParams(window.location.search).get('_auth');
  if (authToken) {
    const separator = url.includes('?') ? '&' : '?';
    url = `${url}${separator}_auth=${encodeURIComponent(authToken)}`;
  }
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {})
  });
  if (!response.ok) throw new Error('request failed');
  const data = await response.json();
  if (!data.ok) throw new Error('not ok');
  return data;
}

function ensureBadge(card, className, text, colorClass) {
  const zone = card.querySelector('.badge-zone');
  if (!zone || zone.querySelector('.' + className)) return;
  const badge = document.createElement('span');
  badge.className = `badge ${colorClass} ${className}`;
  badge.textContent = text;
  zone.appendChild(badge);
}

function removeBadge(card, className) {
  card.querySelectorAll('.' + className).forEach((badge) => badge.remove());
}

function hasAcceptedCards() {
  return Boolean(document.querySelector('[data-card][data-status="accepted"]'));
}

function setContinue(enabled) {
  const button = document.getElementById('continueButton');
  if (!button) return;
  button.disabled = !enabled;
  button.classList.toggle('btn-success', enabled);
  button.classList.toggle('btn-secondary', !enabled);
}

function disableCardButtons(card, disabled) {
  card.querySelectorAll('[data-action]').forEach((button) => {
    button.disabled = disabled;
  });
}

function applyAcceptedState(card) {
  card.dataset.status = 'accepted';
  card.classList.remove('border-danger', 'rejected-card', 'dimmed-card');
  card.classList.add('border-success');
  removeBadge(card, 'rejected-badge');
  ensureBadge(card, 'selected-badge', 'Выбрано ?', 'text-bg-success');
  disableCardButtons(card, false);
  setContinue(true);
}

function applyRejectedState(card) {
  card.dataset.status = 'rejected';
  card.classList.remove('border-success', 'editing-card');
  card.classList.add('border-danger', 'rejected-card');
  removeBadge(card, 'selected-badge');
  ensureBadge(card, 'rejected-badge', 'Отклонено', 'text-bg-danger');
  disableCardButtons(card, true);
  setContinue(hasAcceptedCards());
}

function setEditing(card, editing) {
  card.classList.toggle('editing-card', editing);
  card.querySelectorAll('.editable').forEach((field) => {
    field.readOnly = !editing;
  });
  const button = card.querySelector('[data-action="edit"]');
  if (button) button.textContent = editing ? '?? Сохранить' : '?? Редактировать';
}

function collectFields(card) {
  return {
    title: card.querySelector('.field-title').value,
    description: card.querySelector('.field-description').value,
    logic: card.querySelector('.field-logic').value,
    criteria: card.querySelector('.field-criteria').value
  };
}

function isLastRejectableCard(card) {
  const prefix = card.dataset.actionPrefix;
  const activeCards = Array.from(document.querySelectorAll(`[data-card][data-action-prefix="${prefix}"]`))
    .filter((item) => item.dataset.status !== 'rejected');
  return activeCards.length === 1 && activeCards[0] === card;
}

function initializePersistedState() {
  setContinue(hasAcceptedCards());
}

function bindCardActions() {
  document.querySelectorAll('[data-card]').forEach((card) => {
    card.querySelectorAll('[data-action]').forEach((button) => {
      button.addEventListener('click', async () => {
        const action = button.dataset.action;
        const id = card.dataset.id;
        const prefix = card.dataset.actionPrefix;
        try {
          if (action === 'edit') {
            if (!card.classList.contains('editing-card')) {
              setEditing(card, true);
              return;
            }
            await postJson(`/${prefix}/save/${id}`, collectFields(card));
            setEditing(card, false);
            ensureBadge(card, 'changed-badge', '?? Изменено', 'text-bg-warning');
            return;
          }
          if (action === 'accept') {
            await postJson(`/${prefix}/accept/${id}`);

            // Для item1 — одиночный выбор, сбрасываем остальные карточки
            if (prefix === 'item1') {
              document.querySelectorAll(`[data-card][data-action-prefix="${prefix}"]`).forEach((otherCard) => {
                if (otherCard.dataset.id !== id && otherCard.dataset.status === 'accepted') {
                  otherCard.dataset.status = 'pending';
                  otherCard.classList.remove('border-success');
                  removeBadge(otherCard, 'selected-badge');
                }
              });
            }

            applyAcceptedState(card);
            return;
          }
          if (action === 'reject') {
            const shouldShowOverlay = isLastRejectableCard(card);
            if (shouldShowOverlay) showAiOverlay();
            const data = await postJson(`/${prefix}/reject/${id}`);
            applyRejectedState(card);
            if (data.reload) {
              window.location.reload();
            }
          }
        } catch (error) {
          showErrorToast();
        }
      });
    });
  });
}

function bindAiForms() {
  document.querySelectorAll('[data-ai-form]').forEach((form) => {
    form.addEventListener('submit', () => {
      showAiOverlay();
      const spinner = form.querySelector('.spinner-border');
      if (spinner) spinner.classList.remove('d-none');
      form.querySelectorAll('button').forEach((button) => button.disabled = true);
    });
  });
}

function bindCustomForms() {
  document.querySelectorAll('[data-custom-form]').forEach((form) => {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      const errorDiv = form.querySelector('.validation-error');
      if (errorDiv) errorDiv.classList.add('d-none');

      const submitBtn = form.querySelector('[type="submit"]');
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Проверка...';
      }

      const authToken = new URLSearchParams(window.location.search).get('_auth');
      let url = form.action;
      if (authToken) {
        url += (url.includes('?') ? '&' : '?') + '_auth=' + encodeURIComponent(authToken);
      }

      const formData = new FormData(form);
      const body = Object.fromEntries(formData.entries());

      try {
        const resp = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });

        const result = await resp.json();

        if (resp.status === 422) {
          // Вариант не прошёл проверку — показываем причину
          if (errorDiv) {
            errorDiv.textContent = result.reason || 'Вариант не соответствует правилам.';
            errorDiv.classList.remove('d-none');
          }
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Добавить и выбрать';
          }
          return;
        }

        if (result.ok) {
          window.location.reload();
        }
      } catch (err) {
        showErrorToast();
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = 'Добавить и выбрать';
        }
      }
    });
  });
}

document.addEventListener('DOMContentLoaded', () => {
  initializePersistedState();
  bindCardActions();
  bindAiForms();
  bindCustomForms();
});