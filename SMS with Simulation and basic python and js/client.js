const elements = {
  messages: document.getElementById('messages'),
  conversations: document.getElementById('conversationsList'),
  count: document.getElementById('conversationCount'),
  search: document.getElementById('conversationSearch'),
  input: document.getElementById('chatInput'),
  form: document.getElementById('chatForm'),
  send: document.getElementById('chatSendBtn'),
  characterCount: document.getElementById('characterCount'),
  senderMode: document.getElementById('senderMode'),
  senderModeField: document.getElementById('senderModeField'),
  scrollLatest: document.getElementById('scrollLatestBtn'),
  title: document.getElementById('chatTitle'),
  subtitle: document.getElementById('chatSubtitle'),
  avatar: document.getElementById('headerAvatar'),
  rename: document.getElementById('setNameBtn'),
  aiToggle: document.getElementById('aiToggleBtn'),
  aiStatusText: document.getElementById('aiStatusText'),
  recipientAiToggle: document.getElementById('recipientAiToggleBtn'),
  recipientAiStatusText: document.getElementById('recipientAiStatusText'),
  deleteConversation: document.getElementById('deleteConversationBtn'),
  notification: document.getElementById('notification'),
  csvFile: document.getElementById('csvFile'),
  bulkStatus: document.getElementById('bulkStatus'),
  compose: document.getElementById('composeBtn'),
  emptyCompose: document.getElementById('emptyComposeBtn'),
  dialog: document.getElementById('contactDialog'),
  contactForm: document.getElementById('contactForm'),
  dialogTitle: document.getElementById('dialogTitle'),
  dialogEyebrow: document.getElementById('dialogEyebrow'),
  phoneField: document.getElementById('phoneField'),
  contactPhone: document.getElementById('contactPhone'),
  contactName: document.getElementById('contactName'),
  propertyAddress: document.getElementById('propertyAddress'),
  outreachReasonField: document.getElementById('outreachReasonField'),
  outreachReason: document.getElementById('outreachReason'),
  aiEnabled: document.getElementById('aiEnabled'),
  recipientAiEnabled: document.getElementById('recipientAiEnabled'),
  dialogError: document.getElementById('dialogError'),
  saveContact: document.getElementById('saveContactBtn'),
  sidebar: document.getElementById('sidebar'),
  sidebarScrim: document.getElementById('sidebarScrim'),
  openSidebar: document.getElementById('openSidebarBtn'),
  closeSidebar: document.getElementById('closeSidebarBtn'),
  connection: document.querySelector('.connection-state'),
  confirmDialog: document.getElementById('confirmDialog'),
  confirmTitle: document.getElementById('confirmTitle'),
  confirmCopy: document.getElementById('confirmCopy'),
};

let currentContact = null;
let conversations = [];
let dialogMode = 'create';
let toastTimer;

function isNearLatest() {
  return elements.messages.scrollHeight - elements.messages.scrollTop - elements.messages.clientHeight < 90;
}

function updateScrollLatest() {
  elements.scrollLatest.hidden = isNearLatest() || elements.messages.scrollHeight <= elements.messages.clientHeight;
}

function scrollToLatest(behavior = 'smooth') {
  elements.messages.scrollTo({ top: elements.messages.scrollHeight, behavior });
  elements.scrollLatest.hidden = true;
}

function initials(value = '') {
  const clean = value.replace(/^\+/, '').trim();
  if (!clean) return 'R';
  const words = clean.split(/\s+/).filter(Boolean);
  return words.length > 1
    ? `${words[0][0]}${words[1][0]}`.toUpperCase()
    : clean.slice(0, 2).toUpperCase();
}

function formatTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit' }).format(date);
  }
  return new Intl.DateTimeFormat([], { month: 'short', day: 'numeric' }).format(date);
}

function dateLabel(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return 'Today';
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return new Intl.DateTimeFormat([], { month: 'long', day: 'numeric', year: date.getFullYear() === today.getFullYear() ? undefined : 'numeric' }).format(date);
}

function activeConversation() {
  return conversations.find(item => item.contact === currentContact);
}

function conversationTitle(contact = currentContact) {
  const conversation = conversations.find(item => item.contact === contact);
  return conversation?.name || contact || 'Your messages';
}

function updateAiStatus(conversation) {
  const enabled = Boolean(conversation?.ai_enabled);
  const recipientEnabled = Boolean(conversation?.recipient_ai_enabled);
  elements.aiToggle.disabled = !conversation;
  elements.aiToggle.classList.toggle('active', enabled);
  elements.aiToggle.setAttribute('aria-pressed', String(enabled));
  elements.aiStatusText.textContent = enabled ? 'Bobbie on' : 'Bobbie off';
  elements.recipientAiToggle.disabled = !conversation || !conversation?.simulation_mode;
  elements.recipientAiToggle.classList.toggle('active', recipientEnabled);
  elements.recipientAiToggle.setAttribute('aria-pressed', String(recipientEnabled));
  elements.recipientAiStatusText.textContent = recipientEnabled ? 'Recipient on' : 'Recipient off';
  updateSenderMode(conversation);
}

function updateSenderMode(conversation) {
  elements.senderModeField.hidden = !conversation;
  if (!conversation) {
    elements.senderMode.disabled = true;
    return;
  }

  const bobbieEnabled = Boolean(conversation.ai_enabled);
  const recipientEnabled = Boolean(conversation.recipient_ai_enabled);
  if (bobbieEnabled && !recipientEnabled) {
    elements.senderMode.value = 'recipient';
    elements.senderMode.disabled = true;
  } else if (!bobbieEnabled && recipientEnabled) {
    elements.senderMode.value = 'bobbie';
    elements.senderMode.disabled = true;
  } else if (bobbieEnabled && recipientEnabled) {
    elements.senderMode.value = 'bobbie';
    elements.senderMode.disabled = true;
  } else {
    elements.senderMode.disabled = false;
  }
  setComposerPlaceholder(true);
}

function setComposerPlaceholder(enabled) {
  if (!enabled) {
    elements.input.placeholder = 'Select a conversation to send a message';
    return;
  }
  const sender = elements.senderMode.value === 'recipient' ? 'recipient' : 'Bobbie';
  elements.input.placeholder = `Write as ${sender}…`;
}

function updateIntentStatus(conversation) {
}

function showNotification(text, isError = false) {
  clearTimeout(toastTimer);
  elements.notification.textContent = text;
  elements.notification.style.background = isError ? '#8f3f32' : '';
  elements.notification.hidden = false;
  toastTimer = setTimeout(() => { elements.notification.hidden = true; }, 4200);
}

function showEmptyState(title = 'Conversations, all in one place', copy = 'Select a message from the sidebar or start a new conversation.', showButton = true) {
  elements.messages.replaceChildren();
  const wrapper = document.createElement('div');
  wrapper.className = 'empty-state';
  wrapper.innerHTML = `<div class="empty-illustration" aria-hidden="true"><svg viewBox="0 0 64 64"><path d="M18 43 9 50l2.5-13A23 23 0 1 1 18 43Z"/><path d="M22 27h20M22 34h13"/></svg></div>`;
  const heading = document.createElement('h2');
  heading.textContent = title;
  const paragraph = document.createElement('p');
  paragraph.textContent = copy;
  wrapper.append(heading, paragraph);
  if (showButton) {
    const button = document.createElement('button');
    button.className = 'secondary-button';
    button.textContent = 'Start a conversation';
    button.addEventListener('click', openCreateDialog);
    wrapper.append(button);
  }
  elements.messages.append(wrapper);
}

function askToDelete(title, copy) {
  elements.confirmTitle.textContent = title;
  elements.confirmCopy.textContent = copy;
  elements.confirmDialog.showModal();
  return new Promise(resolve => {
    elements.confirmDialog.addEventListener('close', () => {
      resolve(elements.confirmDialog.returnValue === 'confirm');
    }, { once: true });
  });
}

async function deleteRequest(path) {
  const response = await fetch(path, { method: 'DELETE' });
  let result = {};
  try { result = await response.json(); } catch { /* Empty error response. */ }
  if (!response.ok) throw new Error(result.error || 'Delete failed');
}

async function handleDeleteMessage(message) {
  if (!message.id) return;
  const confirmed = await askToDelete('Delete this message?', 'It will be removed from this conversation permanently.');
  if (!confirmed) return;
  try {
    await deleteRequest(`/messages/${message.id}`);
    await Promise.all([openConversation(currentContact), loadConversations()]);
    showNotification('Message deleted');
  } catch (error) {
    showNotification(`Delete failed: ${error.message}`, true);
  }
}

async function handleDeleteConversation() {
  if (!currentContact) return;
  const contact = currentContact;
  const title = conversationTitle(contact);
  const confirmed = await askToDelete(`Delete ${title}?`, 'This will permanently delete the contact and every message in the conversation.');
  if (!confirmed) return;
  try {
    await deleteRequest(`/conversations/${encodeURIComponent(contact)}`);
    currentContact = null;
    elements.title.textContent = 'Your messages';
    elements.subtitle.textContent = 'Choose a conversation to get started';
    elements.avatar.textContent = 'R';
    elements.rename.disabled = true;
    elements.deleteConversation.disabled = true;
    updateAiStatus(null);
    updateIntentStatus(null);
    elements.input.value = '';
    elements.characterCount.textContent = '0 / 1600';
    setComposerEnabled(false);
    showEmptyState();
    await loadConversations();
    showNotification(`${title} was deleted`);
  } catch (error) {
    showNotification(`Delete failed: ${error.message}`, true);
  }
}

function setComposerEnabled(enabled) {
  elements.input.disabled = !enabled;
  elements.send.disabled = !enabled || !elements.input.value.trim();
  setComposerPlaceholder(enabled);
}

function addMessage(message, previousDate = '') {
  const direction = message.direction === 'outbound' ? 'outbound' : 'inbound';
  const createdAt = message.created_at || message.received_at || new Date().toISOString();
  const currentDate = dateLabel(createdAt);
  if (currentDate && currentDate !== previousDate) {
    const divider = document.createElement('div');
    divider.className = 'date-divider';
    divider.textContent = currentDate;
    elements.messages.append(divider);
  }

  const row = document.createElement('div');
  row.className = `message-row ${direction}`;
  const bubble = document.createElement('div');
  bubble.className = 'message-item';
  const text = document.createElement('div');
  text.className = 'message-text';
  text.textContent = message.text || '';
  bubble.append(text);

  const meta = document.createElement('div');
  meta.className = 'message-meta';
  const time = formatTime(createdAt);
  const status = direction === 'outbound' && message.status ? ` · ${message.status}` : '';
  meta.textContent = `${time}${status}` || (direction === 'outbound' ? 'Sent' : 'Received');
  row.append(bubble, meta);
  if (message.id) {
    const deleteButton = document.createElement('button');
    deleteButton.type = 'button';
    deleteButton.className = 'message-delete';
    deleteButton.title = 'Delete message';
    deleteButton.setAttribute('aria-label', 'Delete message');
    deleteButton.innerHTML = '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M7 7l1 13h8l1-13"/></svg>';
    deleteButton.addEventListener('click', () => handleDeleteMessage(message));
    row.append(deleteButton);
  }
  elements.messages.append(row);
  scrollToLatest('smooth');
  return currentDate || previousDate;
}

function renderConversations() {
  const previousScrollTop = elements.conversations.scrollTop;
  const query = elements.search.value.trim().toLowerCase();
  const filtered = conversations.filter(item => `${item.name || ''} ${item.contact} ${item.last_text || ''}`.toLowerCase().includes(query));
  elements.conversations.replaceChildren();
  elements.count.textContent = conversations.length;

  if (!filtered.length) {
    const empty = document.createElement('div');
    empty.className = 'list-empty';
    empty.textContent = query ? 'No conversations match your search.' : 'No conversations yet. Start one to see it here.';
    elements.conversations.append(empty);
    return;
  }

  filtered.forEach(conversation => {
    const button = document.createElement('button');
    button.className = `conversation-item${conversation.contact === currentContact ? ' active' : ''}`;
    button.type = 'button';
    button.setAttribute('aria-label', `Open conversation with ${conversation.name || conversation.contact}`);

    const avatar = document.createElement('span');
    avatar.className = 'conversation-avatar';
    avatar.textContent = initials(conversation.name || conversation.contact);
    const content = document.createElement('span');
    content.className = 'conversation-content';
    const top = document.createElement('span');
    top.className = 'conversation-topline';
    const name = document.createElement('span');
    name.className = 'conversation-name';
    name.textContent = conversation.name || conversation.contact;
    const coaching = document.createElement('span');
    coaching.className = 'conversation-coaching';
    coaching.hidden = true;
    const last = document.createElement('span');
    last.className = 'conversation-last';
    last.textContent = conversation.last_text || 'No messages yet';
    top.append(name);
    content.append(top, coaching, last);
    const time = document.createElement('span');
    time.className = 'conversation-time';
    time.textContent = formatTime(conversation.last_at || conversation.created_at);
    button.append(avatar, content, time);
    button.addEventListener('click', () => openConversation(conversation.contact));
    elements.conversations.append(button);
  });
  elements.conversations.scrollTop = previousScrollTop;
}

async function loadConversations() {
  try {
    const response = await fetch('/conversations');
    if (!response.ok) throw new Error('Could not load conversations');
    conversations = await response.json();
    renderConversations();
    if (currentContact) updateIntentStatus(activeConversation());
  } catch (error) {
    elements.conversations.innerHTML = '<div class="list-empty">Unable to load conversations.</div>';
    console.error(error);
  }
}

async function openConversation(contact) {
  currentContact = contact;
  renderConversations();
  elements.title.textContent = conversationTitle(contact);
  const conversation = activeConversation();
  elements.subtitle.textContent = conversation?.property_address ? `${contact} · ${conversation.property_address}` : contact;
  elements.avatar.textContent = initials(conversationTitle(contact));
  elements.rename.disabled = false;
  elements.deleteConversation.disabled = false;
  updateAiStatus(conversation);
  updateIntentStatus(conversation);
  setComposerEnabled(true);
  elements.messages.replaceChildren();
  const loading = document.createElement('div');
  loading.className = 'list-empty';
  loading.textContent = 'Loading messages…';
  elements.messages.append(loading);
  closeSidebar();

  try {
    const response = await fetch(`/conversations/${encodeURIComponent(contact)}`);
    if (!response.ok) throw new Error('Could not load messages');
    const messages = await response.json();
    elements.messages.replaceChildren();
    if (!messages.length) {
      showEmptyState('A fresh conversation', `Send the first message to ${conversationTitle(contact)}.`, false);
    } else {
      let previousDate = '';
      messages.forEach(message => { previousDate = addMessage(message, previousDate); });
    }
    elements.input.focus();
  } catch (error) {
    showEmptyState('Messages unavailable', 'We could not load this conversation. Please try again.', false);
    showNotification(error.message, true);
  }
}

async function sendMessage(to, text, sender) {
  const manualRecipient = sender === 'recipient';
  const response = await fetch(manualRecipient ? '/simulate-inbound' : '/send', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(manualRecipient ? { from: to, text } : { to, text })
  });
  const result = await response.json();
  if (!response.ok || result?.error) throw new Error(result?.error || 'Message failed to send');
  return result;
}

elements.form.addEventListener('submit', async event => {
  event.preventDefault();
  const text = elements.input.value.trim();
  if (!text || !currentContact) return;
  elements.send.disabled = true;
  const originalText = elements.send.querySelector('span').textContent;
  elements.send.querySelector('span').textContent = 'Sending';
  try {
    await sendMessage(currentContact, text, elements.senderMode.value);
    elements.input.value = '';
    elements.input.style.height = '';
    elements.characterCount.textContent = '0 / 1600';
    await loadConversations();
    await openConversation(currentContact);
  } catch (error) {
    showNotification(`Send failed: ${error.message}`, true);
  } finally {
    elements.send.querySelector('span').textContent = originalText;
    elements.send.disabled = !elements.input.value.trim();
  }
});

elements.senderMode.addEventListener('change', () => setComposerPlaceholder(Boolean(currentContact)));

elements.input.addEventListener('input', () => {
  elements.input.style.height = 'auto';
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 150)}px`;
  elements.characterCount.textContent = `${elements.input.value.length} / 1600`;
  elements.send.disabled = !currentContact || !elements.input.value.trim();
});

elements.input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    if (!elements.send.disabled) elements.form.requestSubmit();
  }
});

function openCreateDialog() {
  dialogMode = 'create';
  elements.dialogEyebrow.textContent = 'New conversation';
  elements.dialogTitle.textContent = 'Start Conversation';
  elements.phoneField.hidden = false;
  elements.contactPhone.required = true;
  elements.contactPhone.value = '';
  elements.contactName.value = '';
  elements.propertyAddress.value = '';
  elements.outreachReasonField.hidden = false;
  elements.outreachReason.required = true;
  elements.outreachReason.value = '';
  elements.aiEnabled.checked = true;
  elements.recipientAiEnabled.checked = true;
  elements.dialogError.textContent = '';
  elements.saveContact.textContent = 'Start Conversation';
  elements.dialog.showModal();
  requestAnimationFrame(() => elements.contactPhone.focus());
}

function openRenameDialog() {
  if (!currentContact) return;
  dialogMode = 'rename';
  elements.dialogEyebrow.textContent = 'Contact details';
  elements.dialogTitle.textContent = 'Contact settings';
  elements.phoneField.hidden = true;
  elements.contactPhone.required = false;
  elements.contactName.value = activeConversation()?.name || '';
  elements.propertyAddress.value = activeConversation()?.property_address || '';
  elements.outreachReasonField.hidden = true;
  elements.outreachReason.required = false;
  elements.outreachReason.value = '';
  elements.aiEnabled.checked = Boolean(activeConversation()?.ai_enabled);
  elements.recipientAiEnabled.checked = Boolean(activeConversation()?.recipient_ai_enabled);
  elements.dialogError.textContent = '';
  elements.saveContact.textContent = 'Save settings';
  elements.dialog.showModal();
  requestAnimationFrame(() => elements.contactName.focus());
}

elements.contactForm.addEventListener('submit', async event => {
  const submitter = event.submitter;
  if (submitter?.value === 'cancel') return;
  event.preventDefault();
  const contact = dialogMode === 'rename' ? currentContact : elements.contactPhone.value.trim();
  const name = elements.contactName.value.trim();
  const propertyAddress = elements.propertyAddress.value.trim();
  const outreachReason = elements.outreachReason.value.trim();
  const aiEnabled = elements.aiEnabled.checked;
  const recipientAiEnabled = elements.recipientAiEnabled.checked;
  if (!contact) {
    elements.dialogError.textContent = 'Enter a phone number to continue.';
    return;
  }
  if (!name) {
    elements.dialogError.textContent = 'Enter the contact name to continue.';
    return;
  }
  if (dialogMode === 'create' && !/^\+[1-9]\d{6,14}$/.test(contact.replace(/[\s()-]/g, ''))) {
    elements.dialogError.textContent = 'Enter a valid number with country code, such as +15550000000.';
    return;
  }
  const normalizedContact = contact.replace(/[\s()-]/g, '');
  if (dialogMode === 'create' && !propertyAddress) {
    elements.dialogError.textContent = 'Enter the property address to continue.';
    return;
  }
  if (dialogMode === 'create' && !outreachReason) {
    elements.dialogError.textContent = 'Enter the outreach reason to continue.';
    return;
  }
  elements.saveContact.disabled = true;
  elements.saveContact.textContent = dialogMode === 'create' ? 'Starting…' : 'Saving…';
  try {
    const isNewConversation = dialogMode === 'create';
    const response = await fetch(isNewConversation ? '/outreach' : '/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contact: normalizedContact, name, property_address: propertyAddress, outreach_reason: outreachReason, ai_enabled: aiEnabled, recipient_ai_enabled: recipientAiEnabled })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to save contact');
    elements.dialog.close();
    await loadConversations();
    await openConversation(normalizedContact);
    if (isNewConversation) {
      showNotification(result.queued
        ? 'Conversation queued — it will start when the current lead finishes'
        : result.started
          ? 'Introduction sent — the conversation is active'
          : 'Conversation created — write the first message as Bobbie');
    }
    else if (dialogMode === 'rename') showNotification('Contact settings updated');
  } catch (error) {
    elements.dialogError.textContent = error.message;
  } finally {
    elements.saveContact.disabled = false;
    elements.saveContact.textContent = dialogMode === 'create' ? 'Start Conversation' : 'Save settings';
  }
});

function parseCsvRows(text) {
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  const source = text.replace(/^\uFEFF/, '');
  for (let index = 0; index < source.length; index += 1) {
    const character = source[index];
    if (character === '"') {
      if (quoted && source[index + 1] === '"') {
        field += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === ',' && !quoted) {
      row.push(field.trim());
      field = '';
    } else if ((character === '\n' || character === '\r') && !quoted) {
      if (character === '\r' && source[index + 1] === '\n') index += 1;
      row.push(field.trim());
      if (row.some(value => value)) rows.push(row);
      row = [];
      field = '';
    } else {
      field += character;
    }
  }
  row.push(field.trim());
  if (row.some(value => value)) rows.push(row);
  return rows;
}

function parseCsv(text) {
  const [rawHeaders = [], ...values] = parseCsvRows(text);
  const headers = rawHeaders.map(header => header.toLowerCase());
  return values.map(columns => Object.fromEntries(
    headers.map((header, index) => [header, (columns[index] || '').trim()])
  ));
}

elements.csvFile.addEventListener('change', async () => {
  const file = elements.csvFile.files?.[0];
  if (!file) return;
  elements.bulkStatus.textContent = `Reading ${file.name}…`;
  const rows = parseCsv(await file.text());
  if (!rows.length) {
    elements.bulkStatus.textContent = 'The CSV contains no data rows.';
    return;
  }
  const isLeadImport = ['name', 'phone number', 'property address', 'lead source', 'outreach reason', 'lead details', 'initial message']
    .every(header => Object.hasOwn(rows[0], header));
  const isMessageImport = ['to', 'text'].every(header => Object.hasOwn(rows[0], header));
  if (!isLeadImport && !isMessageImport) {
    elements.bulkStatus.textContent = 'Expected the property-lead CSV columns—or to,text.';
    return;
  }
  elements.bulkStatus.textContent = isLeadImport
    ? `Creating ${rows.length} simulated conversations…`
    : `Sending ${rows.length} messages…`;
  try {
    const response = await fetch(isLeadImport ? '/bulk-outreach' : '/bulk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(isLeadImport
        ? rows.map(row => ({
            name: row.name,
            contact: row['phone number'],
            property_address: row['property address'],
            lead_source: row['lead source'],
            outreach_reason: row['outreach reason'],
            lead_details: (() => { try { return JSON.parse(row['lead details'] || '{}'); } catch { return {}; } })(),
            initial_message: row['initial message']
          }))
        : rows.map(row => ({ to: row.to, text: row.text })))
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Bulk send failed');
    const sent = result.imported ?? result.results?.filter(item => item.success).length ?? rows.length;
    const failed = result.failed ?? rows.length - sent;
    elements.bulkStatus.textContent = isLeadImport
      ? `${sent} conversations started${failed ? ` · ${failed} failed` : ''}`
      : `${sent} of ${rows.length} messages sent`;
    await loadConversations();
    showNotification(isLeadImport
      ? `Simulation import complete: ${sent} conversations started`
      : `Bulk send complete: ${sent} sent`, Boolean(failed));
  } catch (error) {
    elements.bulkStatus.textContent = error.message;
    showNotification(`Import failed: ${error.message}`, true);
  } finally {
    elements.csvFile.value = '';
  }
});

function openSidebar() {
  elements.sidebar.classList.add('open');
  elements.sidebarScrim.classList.add('visible');
}

function closeSidebar() {
  elements.sidebar.classList.remove('open');
  elements.sidebarScrim.classList.remove('visible');
}

elements.compose.addEventListener('click', openCreateDialog);
elements.emptyCompose?.addEventListener('click', openCreateDialog);
elements.rename.addEventListener('click', openRenameDialog);
elements.deleteConversation.addEventListener('click', handleDeleteConversation);
elements.aiToggle.addEventListener('click', async () => {
  const conversation = activeConversation();
  if (!conversation) return;
  const nextState = !Boolean(conversation.ai_enabled);
  if (nextState && !conversation.property_address) {
    openRenameDialog();
    elements.aiEnabled.checked = true;
    elements.dialogError.textContent = 'Add a property address before enabling AI autopilot.';
    return;
  }
  elements.aiToggle.disabled = true;
  try {
    const response = await fetch('/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contact: currentContact, ai_enabled: nextState })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to update AI autopilot');
    await loadConversations();
    updateAiStatus(activeConversation());
    showNotification(`AI autopilot ${nextState ? 'enabled' : 'paused'}`);
  } catch (error) {
    showNotification(error.message, true);
    updateAiStatus(conversation);
  }
});
elements.recipientAiToggle.addEventListener('click', async () => {
  const conversation = activeConversation();
  if (!conversation?.simulation_mode) return;
  const nextState = !Boolean(conversation.recipient_ai_enabled);
  elements.recipientAiToggle.disabled = true;
  try {
    const response = await fetch('/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contact: currentContact, recipient_ai_enabled: nextState })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Unable to update recipient AI');
    await loadConversations();
    updateAiStatus(activeConversation());
    showNotification(`Recipient AI ${nextState ? 'enabled' : 'paused'}`);
  } catch (error) {
    showNotification(error.message, true);
    updateAiStatus(conversation);
  }
});
elements.search.addEventListener('input', renderConversations);
elements.openSidebar.addEventListener('click', openSidebar);
elements.closeSidebar.addEventListener('click', closeSidebar);
elements.sidebarScrim.addEventListener('click', closeSidebar);
elements.messages.addEventListener('scroll', updateScrollLatest, { passive: true });
elements.scrollLatest.addEventListener('click', () => scrollToLatest());

const events = new EventSource('/events');
events.onopen = () => {
  elements.connection.classList.remove('offline');
  elements.connection.lastElementChild.textContent = 'Webhook connected';
};
events.addEventListener('message.received', async event => {
  try {
    const payload = JSON.parse(event.data);
    const sender = payload.from?.phone_number || payload.from || '';
    if (sender === currentContact) await openConversation(currentContact);
    await loadConversations();
    showNotification(`New message from ${conversationTitle(sender)}`);
  } catch (error) { console.error('Failed to parse message event', error); }
});
events.addEventListener('message.updated', async () => {
  await loadConversations();
  if (currentContact) await openConversation(currentContact);
});
events.addEventListener('conversation.updated', async event => {
  try {
    const payload = JSON.parse(event.data);
    await loadConversations();
    if (payload.contact === currentContact) {
      updateAiStatus(activeConversation());
      updateIntentStatus(activeConversation());
    }
  } catch (error) { console.error('Failed to parse conversation event', error); }
});
events.addEventListener('ai.error', event => {
  try {
    const payload = JSON.parse(event.data);
    if (payload.contact === currentContact) showNotification(`AI reply needs attention: ${payload.error}`, true);
  } catch (error) { console.error('Failed to parse AI error event', error); }
});
events.onerror = () => {
  elements.connection.classList.add('offline');
  elements.connection.lastElementChild.textContent = 'Reconnecting…';
};

loadConversations();
