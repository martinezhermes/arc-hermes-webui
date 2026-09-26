/* Messaging workspaces use the authenticated server; native credentials never enter this module. */
(function (root) {
  'use strict';
  let view = null;
  let pending = 0;
  const text = (key) => typeof t === 'function' ? t(key) : key;
  const profile = () => typeof S !== 'undefined' ? S.activeProfile || 'default' : 'default';

  function element(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined && content !== null) node.textContent = String(content);
    return node;
  }
  function button(label, action, disabled) {
    const node = element('button', 'messaging-button', label);
    node.type = 'button';
    node.disabled = !!disabled;
    node.addEventListener('click', action);
    return node;
  }
  function note(container, message, error) {
    const node = element('p', error ? 'messaging-error' : 'messaging-note', message);
    if (error) node.setAttribute('role', 'alert');
    container.append(node);
    return node;
  }
  function feedback(container, message, error) {
    let node = container.querySelector(':scope > .messaging-feedback');
    if (!node) { node = element('p'); container.append(node); }
    node.className = 'messaging-feedback ' + (error ? 'messaging-error' : 'messaging-note');
    node.setAttribute('role', error ? 'alert' : 'status');
    node.textContent = message;
  }
  function allowed(principal, operation, room) {
    return !!(principal && principal.enabled && (
      principal.administrator || (
        Array.isArray(principal.operations) && principal.operations.includes(operation) &&
        (!room || principal.rooms === '*' || (Array.isArray(principal.rooms) && principal.rooms.includes(room)))
      )
    ));
  }
  function errorDetails(error) {
    let detail = {};
    try { detail = JSON.parse(error.body || '{}').detail || {}; } catch (_) {}
    return {
      message: typeof detail.message === 'string' ? detail.message : text('messaging_request_failed'),
      uncertain: detail.uncertain === true || !!error.timeout || error.name === 'TypeError',
    };
  }
  async function request(ctx, path, body, signal) {
    if (profile() !== ctx.profile) throw new Error('profile_changed');
    const query = path.includes('?') ? '&' : '?';
    try {
      const result = await api(path + query + 'profile=' + encodeURIComponent(ctx.profile), {
        method: body === undefined ? 'GET' : 'POST',
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        signal: signal || ctx.controller.signal,
        retries: 0, timeoutMs: 120000, timeoutToast: false,
      });
      if (result === undefined) throw new Error('authentication_required');
      return result;
    } catch (error) {
      const detail = errorDetails(error);
      error.message = detail.message;
      error.uncertain = detail.uncertain;
      throw error;
    }
  }
  async function submit(ctx, path, body) {
    const result = await request(ctx, path, body);
    if (!result || result.outcome !== 'submitted') {
      const error = new Error(text('messaging_unconfirmed_outcome'));
      error.uncertain = true;
      throw error;
    }
    return result;
  }
  function current(ctx) {
    return view === ctx && profile() === ctx.profile && !ctx.controller.signal.aborted;
  }
  function close() {
    if (!view) return;
    view.controller.abort();
    clearTimeout(view.timer);
    view.container.replaceChildren();
    view = null;
  }
  function canLeave() {
    if (pending) {
      if (typeof toast === 'function') toast(text('messaging_wait'));
      return false;
    }
    return !view || !view.dirty || root.confirm(text('messaging_discard'));
  }
  function schedule(ctx, action) {
    clearTimeout(ctx.timer);
    ctx.timer = setTimeout(async () => {
      if (!current(ctx)) return;
      if (!document.hidden && !pending && !ctx.dirty) await action();
      if (current(ctx)) schedule(ctx, action);
    }, 15000);
  }
  async function mutate(ctx, container, action) {
    if (!current(ctx) || pending) return false;
    pending++;
    container.setAttribute('aria-busy', 'true');
    const controls = Array.from(container.querySelectorAll('button,input,textarea,select'));
    const wasDisabled = controls.map(node => node.disabled);
    controls.forEach(node => { node.disabled = true; });
    try {
      const result = await action();
      if (!current(ctx)) return false;
      return result;
    } catch (error) {
      if (current(ctx)) feedback(container, error.message + (error.uncertain ? ' ' + text('messaging_uncertain') : ''), true);
      return false;
    } finally {
      pending--;
      container.removeAttribute('aria-busy');
      controls.forEach((node, index) => { node.disabled = wasDisabled[index]; });
    }
  }
  async function open(panel, options = {}) {
    close();
    const container = document.getElementById(panel + 'Content');
    if (!container) return;
    const ctx = { panel, profile: profile(), controller: new AbortController(), directorySequence: 0, historySequence: 0,
      container, dirty: false, room: null, connection: null,
      mode: ['groups','contacts'].includes(options.messagingSource) ? options.messagingSource : 'sessions' };
    view = ctx;
    container.replaceChildren();
    note(container, text('messaging_loading'));
    if (typeof syncAppTitlebar === 'function') syncAppTitlebar();
    if (typeof _closeMobileSidebarAfterPanelSelection === 'function') _closeMobileSidebarAfterPanelSelection();
    try {
      let data;
      if (panel === 'connections') data = await request(ctx, '/api/messaging/connections');
      else if (panel === 'arcConnectors') data = await request(ctx, '/api/arc/connectors');
      else {
        // Agent transcripts remain available even without a native application
        // binding or the agent's optional channel-management dependencies.
        let whatsapp;
        try { whatsapp = await request(ctx, '/api/arc/whatsapp/connection'); }
        catch (error) { whatsapp = { available: false, error: { message: error.message } }; }
        data = { whatsapp, platforms: [] };
      }
      if (!current(ctx)) return;
      ctx.connection = panel === 'arcConnectors' ? data.connectors?.[0]?.connection : data.whatsapp;
      container.replaceChildren();
      if (panel === 'connections') renderConnections(ctx, data);
      else if (panel === 'arcConnectors') renderArcConnectors(ctx, data);
      else renderConversations(ctx, data);
    } catch (error) {
      if (current(ctx)) {
        container.replaceChildren();
        note(container, error.message, true);
        container.append(button(text('messaging_refresh'), () => open(panel)));
      }
    }
  }
  function connectionSummary(ctx, container) {
    const connection = ctx.connection || {};
    const connector = ctx.arcConnector || {};
    const row = element('section', 'messaging-card');
    row.dataset.platformId = 'arc-whatsapp';
    const heading = element('div', 'messaging-row');
    heading.append(element('h2', '', 'ARC WhatsApp'), element('span', 'messaging-status',
      connector.enabled ? (connection.status ? connection.status.phase : text(connection.configured === false ? 'messaging_not_configured' : 'messaging_unavailable')) : text('messaging_disabled')));
    row.append(heading);
    note(row, text('messaging_arc_binding_note'));
    if (connection.error) note(row, connection.error.message || text('messaging_unavailable'), true);
    if (connection.account) note(row, connection.account.name || connection.account.id);
    if (connection.access && connection.access.principal) {
      const principal = connection.access.principal;
      note(row, principal.id + ' · ' + text(principal.administrator ? 'messaging_administrator' : 'messaging_scoped_access'));
      row.append(button(text('tab_conversations'), () => switchPanel('conversations', { messagingSource: 'groups' })));
    } else if (connector.enabled) {
      note(row, text('messaging_whatsapp_setup'));
    }
    const actions = element('div', 'messaging-row');
    const form = element('form', 'messaging-config');
    form.hidden = true;
    const enabled = element('input');
    enabled.type = 'checkbox'; enabled.checked = !!connector.enabled;
    const enableLabel = element('label', 'messaging-check');
    enableLabel.append(enabled, document.createTextNode(text('messaging_enabled')));
    form.append(enableLabel);
    const portLabel = element('label', 'messaging-field', text('messaging_arc_port'));
    const port = element('input');
    port.type = 'number'; port.min = '1'; port.max = '65535'; port.required = true;
    port.value = connector.port || 9131;
    portLabel.append(port); form.append(portLabel);
    const tokenLabel = element('label', 'messaging-field', text('messaging_arc_token_file'));
    const tokenFile = element('input');
    tokenFile.type = 'text'; tokenFile.autocomplete = 'off';
    tokenFile.placeholder = text(connector.token_file_set ? 'messaging_keep_saved' : 'messaging_arc_token_placeholder');
    tokenLabel.append(tokenFile); form.append(tokenLabel);
    note(form, text('messaging_arc_setup_note'));
    const save = element('button', 'messaging-button', text('messaging_save'));
    save.type = 'submit'; form.append(save);
    form.addEventListener('input', () => { ctx.dirty = true; });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const body = { enabled: enabled.checked, port: Number(port.value) };
      if (tokenFile.value.trim()) body.token_file = tokenFile.value.trim();
      const result = await mutate(ctx, form, () => request(ctx,
        '/api/arc/connectors/whatsapp/configure', body));
      if (result && current(ctx)) { ctx.dirty = false; await open('arcConnectors'); }
    });
    actions.append(button(text('messaging_configure'), () => { form.hidden = !form.hidden; }));
    if (connector.enabled || connector.token_file_set) {
      actions.append(button(text(connector.enabled ? 'messaging_disable_arc' : 'messaging_enable_arc'), async () => {
        if (!canLeave()) return;
        if (connector.enabled && !root.confirm(text('messaging_confirm_disable_arc'))) return;
        const result = await mutate(ctx, row, () => request(ctx,
          '/api/arc/connectors/whatsapp/configure', { enabled: !connector.enabled }));
        if (result && current(ctx)) { ctx.dirty = false; await open('arcConnectors'); }
      }));
    }
    row.append(actions, form);
    container.append(row);
  }
  function renderArcConnectors(ctx, data) {
    ctx.arcConnector = (data.connectors || []).find(item => item.id === 'arc-whatsapp');
    if (!ctx.arcConnector) { note(ctx.container, text('messaging_unavailable'), true); return; }
    const toolbar = element('div', 'messaging-toolbar');
    toolbar.append(element('span', 'messaging-note', ctx.profile),
      button(text('messaging_refresh'), () => { if (canLeave()) open('arcConnectors'); }));
    ctx.container.append(toolbar);
    connectionSummary(ctx, ctx.container);
  }
  function renderConnections(ctx, data) {
    const toolbar = element('div', 'messaging-toolbar');
    const search = element('input');
    search.type = 'search';
    search.placeholder = text('messaging_search_platforms');
    search.setAttribute('aria-label', text('messaging_search_platforms'));
    search.addEventListener('input', () => {
      const query = search.value.trim().toLocaleLowerCase();
      ctx.container.querySelectorAll('.messaging-card').forEach(card => {
        card.hidden = !card.dataset.platformId.toLocaleLowerCase().includes(query) &&
          !card.querySelector('h2').textContent.toLocaleLowerCase().includes(query);
      });
    });
    toolbar.append(element('span', 'messaging-note', ctx.profile),
      search,
      button(text('messaging_refresh'), () => { if (canLeave()) open('connections'); }));
    ctx.container.append(toolbar);
    ctx.container.append(element('h2', 'messaging-section-title', text('messaging_gateway_section')));
    const platforms = [...(data.platforms || [])].sort((left, right) =>
      (left.id === 'whatsapp' ? -1 : right.id === 'whatsapp' ? 1 : 0));
    for (const platform of platforms) {
      const card = element('section', 'messaging-card');
      card.dataset.platformId = platform.id;
      const heading = element('div', 'messaging-row');
      heading.append(element('h2', '', platform.id === 'whatsapp' ? text('messaging_gateway_whatsapp') : platform.name),
        element('span', 'messaging-status', platform.state || text('messaging_unknown')));
      card.append(heading);
      if (platform.id === 'whatsapp') note(card, text('messaging_gateway_whatsapp_note'));
      note(card, platform.description || '');
      note(card, text(platform.id === 'whatsapp' && !platform.enabled
        ? 'messaging_gateway_setup_available'
        : platform.configured ? 'messaging_credentials_saved' : 'messaging_setup_needed'));
      const form = element('form', 'messaging-config');
      form.hidden = true;
      const enabled = element('input');
      enabled.type = 'checkbox'; enabled.checked = !!platform.enabled;
      const enableLabel = element('label', 'messaging-check');
      enableLabel.append(enabled, document.createTextNode(text('messaging_enabled')));
      form.append(enableLabel);
      const inputs = [];
      for (const field of platform.env_vars || []) {
        const label = element('label', 'messaging-field', field.prompt || field.description || field.key);
        const input = element('input');
        input.type = field.is_password ? 'password' : 'text';
        input.autocomplete = 'off';
        input.required = !!field.required && !field.is_set;
        input.placeholder = text(field.is_set ? 'messaging_keep_saved' : 'messaging_enter_value');
        label.append(input);
        const remove = element('input');
        remove.type = 'checkbox';
        if (field.is_set) {
          const clearLabel = element('label', 'messaging-check');
          clearLabel.append(remove, document.createTextNode(text('messaging_remove_value')));
          label.append(clearLabel);
        }
        form.append(label);
        inputs.push({ key: field.key, input, remove });
      }
      form.addEventListener('input', () => { ctx.dirty = true; });
      form.append(element('p', 'messaging-note', text('messaging_restart_notice')));
      const save = element('button', 'messaging-button', text('messaging_save'));
      save.type = 'submit';
      form.append(save);
      form.addEventListener('submit', async event => {
        event.preventDefault();
        const env = {}, clear = [];
        for (const item of inputs) {
          if (item.remove.checked) clear.push(item.key);
          else if (item.input.value.trim()) env[item.key] = item.input.value;
        }
        if (clear.length && !root.confirm(text('messaging_confirm_remove'))) return;
        const result = await mutate(ctx, form, () => request(ctx,
          '/api/messaging/platforms/' + encodeURIComponent(platform.id) + '/configure',
          { enabled: enabled.checked, env, clear_env: clear }));
        if (result && current(ctx)) {
          inputs.forEach(item => { item.input.value = ''; });
          ctx.dirty = false;
          await open('connections');
        }
      });
      const actions = element('div', 'messaging-row');
      actions.append(button(text('messaging_configure'), () => {
        if (form.hidden && ctx.editing && ctx.editing !== form) {
          if (!canLeave()) return;
          ctx.editing.reset();
          ctx.editing.hidden = true;
          ctx.dirty = false;
        }
        ctx.editing = form;
        form.hidden = !form.hidden;
      }),
        button(text('messaging_test'), async () => {
          const result = await mutate(ctx, card, () => request(ctx,
            '/api/messaging/platforms/' + encodeURIComponent(platform.id) + '/test', {}));
          if (result && current(ctx)) feedback(card, result.message, result.ok !== true);
        }));
      if (platform.id === 'whatsapp' && (platform.enabled || platform.configured)) {
        actions.append(button(text(platform.enabled ? 'messaging_disable_gateway' : 'messaging_enable_gateway'), async () => {
          if (!canLeave()) return;
          if (platform.enabled && !root.confirm(text('messaging_confirm_disable_gateway'))) return;
          const result = await mutate(ctx, card, () => request(ctx,
            '/api/messaging/platforms/whatsapp/configure',
            { enabled: !platform.enabled, env: {}, clear_env: [] }));
          if (result && current(ctx)) { ctx.dirty = false; await open('connections'); }
        }));
      }
      card.append(actions, form);
      ctx.container.append(card);
    }
  }
  function renderConversations(ctx, data) {
    const toolbar = element('div', 'messaging-toolbar');
    const mode = element('select');
    mode.setAttribute('aria-label', text('messaging_conversation_source'));
    for (const [value, label] of [['sessions', 'messaging_agent_sessions'], ['groups', 'messaging_whatsapp_groups'], ['contacts', 'messaging_whatsapp_contacts']]) {
      const option = element('option', '', text(label)); option.value = value; mode.append(option);
    }
    mode.value = ctx.mode;
    const filter = element('select');
    filter.setAttribute('aria-label', text('messaging_platform'));
    filter.hidden = ctx.mode !== 'sessions';
    const any = element('option', '', text('messaging_all_platforms')); any.value = ''; filter.append(any);
    const platforms = [{ id: 'whatsapp', name: 'WhatsApp' }, ...(data.platforms || [])];
    for (const platform of platforms) {
      const option = element('option', '', platform.name); option.value = platform.id; filter.append(option);
    }
    const search = element('input');
    search.type = 'search'; search.placeholder = text('messaging_search');
    search.setAttribute('aria-label', text('messaging_search'));
    const searchForm = element('form', 'messaging-search');
    const find = element('button', 'messaging-button', text('messaging_find')); find.type = 'submit';
    searchForm.append(search, find);
    toolbar.append(mode, filter, searchForm, button(text('messaging_refresh'), () => loadDirectory(ctx)));
    const layout = element('div', 'messaging-layout');
    ctx.directory = element('div', 'messaging-directory');
    ctx.timeline = element('section', 'messaging-timeline');
    ctx.timeline.setAttribute('aria-label', text('messaging_timeline'));
    layout.append(ctx.directory, ctx.timeline);
    ctx.container.append(toolbar, layout);
    ctx.filter = filter; ctx.search = search;
    mode.addEventListener('change', () => {
      if (!canLeave()) { mode.value = ctx.mode; return; }
      ctx.mode = mode.value; ctx.room = null; ctx.dirty = false;
      filter.hidden = ctx.mode !== 'sessions';
      ctx.timeline.replaceChildren();
      loadDirectory(ctx);
    });
    filter.addEventListener('change', () => loadDirectory(ctx));
    searchForm.addEventListener('submit', event => { event.preventDefault(); loadDirectory(ctx); });
    loadDirectory(ctx);
    schedule(ctx, async () => {
      if (ctx.room) await refreshRoom(ctx);
      else await loadDirectory(ctx, ctx.offset || 0);
    });
  }
  async function loadDirectory(ctx, offset) {
    if (!current(ctx) || pending) return;
    const seq = ++ctx.directorySequence;
    ctx.offset = offset || 0;
    ctx.directory.replaceChildren();
    note(ctx.directory, text('messaging_loading'));
    try {
      let data;
      if (ctx.mode === 'sessions') {
        data = await request(ctx, '/api/messaging/conversations?q=' + encodeURIComponent(ctx.search.value) +
          '&platform=' + encodeURIComponent(ctx.filter.value) + '&offset=' + (offset || 0));
      } else {
        const principal = ctx.connection && ctx.connection.access && ctx.connection.access.principal;
        if (!allowed(principal, ctx.mode + '.list')) throw new Error(text('messaging_native_access_required'));
        data = await request(ctx, '/api/arc/whatsapp/operations/' + ctx.mode + '.list',
          { ...(ctx.search.value.trim() ? { query: ctx.search.value.trim() } : {}), offset: offset || 0, limit: 50 });
      }
      if (!current(ctx) || seq !== ctx.directorySequence) return;
      if (ctx.mode === 'sessions' && Array.isArray(data.platforms)) {
        const selected = ctx.filter.value;
        ctx.filter.replaceChildren();
        const any = element('option', '', text('messaging_all_platforms')); any.value = ''; ctx.filter.append(any);
        for (const platform of data.platforms) {
          const option = element('option', '', platform.name); option.value = platform.id; ctx.filter.append(option);
        }
        ctx.filter.value = selected;
      }
      ctx.directory.replaceChildren();
      note(ctx.directory, text(ctx.mode === 'sessions' ? 'messaging_sessions_scope' : 'messaging_native_scope'));
      if (!(data.items || []).length) note(ctx.directory, text('messaging_empty'));
      for (const item of data.items || []) {
        const row = button(item.title || item.name || item.session_id || item.id, () => {
          if (!canLeave()) return;
          ctx.dirty = false;
          if (ctx.mode === 'sessions') {
            switchPanel('chat').then(() => loadSession(item.session_id));
          } else openRoom(ctx, item);
        });
        row.classList.add('messaging-directory-row');
        if (ctx.mode === 'sessions') row.append(element('span', 'messaging-note', item.source_label || item.source_tag || ''));
        ctx.directory.append(row);
      }
      const next = ctx.mode === 'sessions' ? data.next_offset : data.nextOffset;
      if (offset) ctx.directory.append(button(text('messaging_first_page'), () => loadDirectory(ctx, 0)));
      if (next !== null && next !== undefined) ctx.directory.append(button(text('messaging_next_page'), () => loadDirectory(ctx, next)));
    } catch (error) {
      if (current(ctx) && seq === ctx.directorySequence) { ctx.directory.replaceChildren(); note(ctx.directory, error.message, true); }
    }
  }
  async function openRoom(ctx, room) {
    if (!current(ctx) || pending) return;
    ctx.room = room;
    ctx.timeline.replaceChildren();
    ctx.timeline.append(element('h2', '', room.name || room.id));
    const identity = element('details', 'messaging-identity');
    identity.append(element('summary', '', text('messaging_room_identity')), element('code', '', room.id));
    ctx.timeline.append(identity);
    ctx.history = element('div', 'messaging-history');
    ctx.timeline.append(ctx.history);
    const principal = ctx.connection && ctx.connection.access && ctx.connection.access.principal;
    if (allowed(principal, 'messages.send', room.id)) {
      const compose = element('form', 'messaging-compose');
      const draft = element('textarea');
      draft.placeholder = text('messaging_message'); draft.maxLength = 10000; draft.required = true;
      draft.setAttribute('aria-label', text('messaging_message'));
      draft.addEventListener('input', () => { ctx.dirty = !!draft.value; });
      const send = element('button', 'messaging-button', text('messaging_send')); send.type = 'submit';
      compose.append(draft, send);
      compose.addEventListener('submit', async event => {
        event.preventDefault();
        if (!draft.value.trim() || !root.confirm(text('messaging_confirm_send') + ' ' + (room.name || room.id) + '?')) return;
        const result = await mutate(ctx, compose, () => submit(ctx,
          '/api/arc/whatsapp/operations/messages.send', { roomId: room.id, body: draft.value }));
        if (result && current(ctx)) {
          draft.value = '';
          ctx.dirty = false;
          feedback(compose, text('messaging_submitted'));
          await refreshRoom(ctx);
        }
      });
      ctx.timeline.append(compose);
    } else note(ctx.timeline, text('messaging_read_only'));
    ctx.timeline.append(button(text('messaging_refresh'), () => refreshRoom(ctx)));
    await refreshRoom(ctx);
  }
  async function refreshRoom(ctx) {
    if (!current(ctx) || !ctx.room || pending) return;
    const room = ctx.room;
    const seq = ++ctx.historySequence;
    const principal = ctx.connection && ctx.connection.access && ctx.connection.access.principal;
    if (!allowed(principal, 'messages.recent', room.id)) {
      ctx.history.replaceChildren(); note(ctx.history, text('messaging_native_access_required'), true); return;
    }
    try {
      const history = await request(ctx, '/api/arc/whatsapp/operations/messages.recent', {
        roomId: room.id, limit: 50, includeRelated: allowed(principal, 'messages.details', room.id),
      });
      if (!current(ctx) || seq !== ctx.historySequence || ctx.room !== room) return;
      ctx.history.replaceChildren();
      note(ctx.history, text('messaging_history_scope') + (history.observedAt ? ' · ' + new Date(history.observedAt).toLocaleTimeString() : ''));
      if (!(history.items || []).length) note(ctx.history, text('messaging_empty_history'));
      for (const message of history.items || []) renderMessage(ctx, message, principal);
    } catch (error) {
      if (current(ctx) && seq === ctx.historySequence && ctx.room === room) feedback(ctx.history, error.message, true);
    }
  }
  function renderMessage(ctx, message, principal) {
    const row = element('article', 'messaging-message');
    const meta = element('div', 'messaging-message-meta', message.fromMe ? text('messaging_you') : message.senderId || text('messaging_unknown'));
    if (message.timestamp) meta.append(element('time', '', new Date(message.timestamp * 1000).toLocaleString()));
    row.append(meta, element('p', 'messaging-message-text', message.text));
    if (message.acknowledgement) note(row, message.acknowledgement.state);
    if (message.quoted && message.quoted.message) row.append(element('blockquote', '', message.quoted.message.text));
    if (message.hasMedia && message.messageId && allowed(principal, 'messages.media', message.roomId)) {
      const link = element('a', 'messaging-button', text('messaging_attachment'));
      link.href = new URL('api/arc/whatsapp/media?profile=' + encodeURIComponent(ctx.profile) +
        '&message_id=' + encodeURIComponent(message.messageId), document.baseURI).href;
      link.download = ''; row.append(link);
    }
    if (message.messageId && allowed(principal, 'messages.details', message.roomId)) {
      row.append(button(text('messaging_details'), async () => {
        try {
          const detail = await request(ctx, '/api/arc/whatsapp/operations/messages.details', { messageId: message.messageId });
          if (!current(ctx) || !row.isConnected) return;
          const previous = row.querySelector('.messaging-details');
          if (previous) previous.remove();
          const box = element('div', 'messaging-details');
          note(box, text('messaging_receipts') + ': ' + (detail.delivery ? detail.delivery.status : text('messaging_unknown')));
          const groups = detail.message && detail.message.reactions && detail.message.reactions.items || [];
          for (const group of groups) note(box, group.emoji + ' · ' + group.count);
          row.append(box);
        } catch (error) { if (current(ctx) && row.isConnected) feedback(row, error.message, true); }
      }));
    }
    if (message.messageId && allowed(principal, 'messages.react', message.roomId)) {
      row.append(button(text('messaging_react'), async () => {
        const emoji = root.prompt(text('messaging_emoji'));
        if (emoji === null || !current(ctx)) return;
        const result = await mutate(ctx, ctx.timeline, () => submit(ctx,
          '/api/arc/whatsapp/operations/messages.react', { messageId: message.messageId, emoji }));
        if (result && current(ctx)) { feedback(ctx.timeline, text('messaging_submitted')); await refreshRoom(ctx); }
      }));
    }
    ctx.history.append(row);
  }
  root.MessagingWorkspace = { open, close, canLeave };
  root.addEventListener('beforeunload', event => {
    if (pending || (view && view.dirty)) { event.preventDefault(); event.returnValue = ''; }
  });
  if (typeof module !== 'undefined' && module.exports) module.exports = { allowed, errorDetails, request, submit };
})(typeof window !== 'undefined' ? window : globalThis);
