/**
 * Team roles (Tenant Admin): the roles offered in the "Add team member" form.
 * An admin creates roles like "Closer", "Support" or "Manager". Each role has an access level (fixed once created):
 *   Sales — sees only their own deals, chats and tasks (and can be given new deals automatically)
 *   Admin — full access; never gets automatic deals
 *
 *   RolesModule.open()      opens the "Team roles" dialog (list, add, rename, delete)
 * Emits Crm 'roles-changed' after any change so the Team screen refreshes.
 */
const RolesModule = (() => {
  const esc = (v) => Crm.esc(v);
  let dialog = null;
  let roles = [];
  let editing = null; // role being edited, {} when adding, null while listing

  const ACCESS_TEXT = {
    SALES: 'Sees only their own deals, chats and tasks',
    ADMIN: 'Full access to everything in the workspace',
  };

  function errText(err, fallback) {
    const detail = err && err.data && err.data.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map((d) => d.msg).join('; ');
    return (err && err.message && err.message !== '[object Object]') ? err.message : fallback;
  }

  function dealsText(r) {
    if (r.access === 'ADMIN') return 'Never gets automatic deals';
    return r.receives_deals ? 'Gets new deals automatically' : 'Does not get automatic deals';
  }

  async function open() {
    dialog = Crm.modal({
      title: 'Team roles',
      width: 660,
      body: '<div class="roles-loading">Loading roles…</div>',
      onClose: () => { dialog = null; editing = null; },
    });
    editing = null;
    await refresh();
  }

  async function refresh() {
    try {
      roles = await Api.getRoles();
    } catch (err) {
      if (dialog) dialog.body.innerHTML = `<div class="crm-error">${esc(errText(err, 'Could not load the roles.'))}</div>`;
      return;
    }
    render();
  }

  function render() {
    if (!dialog) return;
    if (editing) renderForm(); else renderList();
  }

  // ------------------------------------------------------------------ list
  function renderList() {
    const rows = roles.map((r) => `
      <div class="role-row" data-id="${esc(r.id)}">
        <div class="role-main">
          <div class="role-name">${esc(r.name)}${r.is_system ? ' <span class="role-tag">Built-in</span>' : ''}</div>
          <div class="role-meta">${esc(ACCESS_TEXT[r.access] || '')} · ${esc(dealsText(r))}</div>
        </div>
        <div class="role-count">${r.member_count} ${r.member_count === 1 ? 'person' : 'people'}</div>
        <div class="role-actions">
          ${r.is_system ? '' : `<button type="button" class="crm-link-btn" data-edit="${esc(r.id)}">Edit</button>
          <button type="button" class="crm-link-btn role-delete" data-delete="${esc(r.id)}">Delete</button>`}
        </div>
      </div>`).join('');
    dialog.body.innerHTML = `
      <p class="role-intro">Roles describe what each person does. When you add someone to the team you pick one of these.
        A role’s <strong>access</strong> decides what that person can see, and whether new deals are given to them automatically.</p>
      <div class="role-list">${rows}</div>`;
    dialog.footer.hidden = false;
    dialog.footer.innerHTML = `
      <button type="button" class="btn-secondary" data-close>Done</button>
      <button type="button" class="btn-primary" id="roleAdd">+ New role</button>`;
    dialog.footer.querySelector('[data-close]').addEventListener('click', () => dialog && dialog.close());
    dialog.footer.querySelector('#roleAdd').addEventListener('click', () => { editing = {}; render(); });
    dialog.body.querySelectorAll('[data-edit]').forEach((b) => b.addEventListener('click', () => {
      editing = roles.find((r) => r.id === b.dataset.edit) || null;
      render();
    }));
    dialog.body.querySelectorAll('[data-delete]').forEach((b) => b.addEventListener('click', () => remove(b.dataset.delete)));
  }

  async function remove(id) {
    const role = roles.find((r) => r.id === id);
    if (!role) return;
    if (role.member_count > 0) {
      Crm.toast(`${role.member_count} ${role.member_count === 1 ? 'person has' : 'people have'} this role. Give them another role first.`, 'error');
      return;
    }
    const sure = await Crm.confirm(`Delete the role “${role.name}”? Nobody has it right now.`, {
      title: 'Delete role', confirmText: 'Delete role', danger: true,
    });
    if (!sure) return;
    try {
      await Api.deleteRole(id);
      Crm.toast('Role deleted');
      Crm.emit('roles-changed');
      await refresh();
    } catch (err) {
      Crm.toast(errText(err, 'Could not delete this role.'), 'error');
    }
  }

  // ------------------------------------------------------------------ add / edit
  function renderForm() {
    const isNew = !editing.id;
    const access = editing.access || 'SALES';
    dialog.body.innerHTML = `
      <form id="roleForm" novalidate>
        <div class="crm-field">
          <label class="form-label" for="roleName">Role name *</label>
          <input type="text" id="roleName" class="form-input" maxlength="60" required autocomplete="off"
                 placeholder="e.g. Closer, Support, Manager" value="${esc(editing.name || '')}" />
        </div>
        <div class="crm-field">
          <span class="form-label">Access *</span>
          <label class="role-choice ${isNew ? '' : 'locked'}">
            <input type="radio" name="roleAccess" value="SALES" ${access === 'SALES' ? 'checked' : ''} ${isNew ? '' : 'disabled'} />
            <span><strong>Sales</strong><small>${esc(ACCESS_TEXT.SALES)}. Can be given new deals automatically.</small></span>
          </label>
          <label class="role-choice ${isNew ? '' : 'locked'}">
            <input type="radio" name="roleAccess" value="ADMIN" ${access === 'ADMIN' ? 'checked' : ''} ${isNew ? '' : 'disabled'} />
            <span><strong>Admin</strong><small>${esc(ACCESS_TEXT.ADMIN)}: all deals, the team, and settings. Never gets automatic deals.</small></span>
          </label>
          ${isNew ? '' : '<p class="crm-hint">Access can’t be changed once a role exists. To give someone different access, choose another role for them.</p>'}
        </div>
        <label class="role-check" id="roleDealsRow">
          <input type="checkbox" id="roleDeals" ${editing.receives_deals === false ? '' : 'checked'} />
          <span>Give this role new deals automatically</span>
        </label>
        <div class="crm-error" id="roleError" role="alert" hidden></div>
      </form>`;
    dialog.footer.hidden = false;
    dialog.footer.innerHTML = `
      <button type="button" class="btn-secondary" id="roleBack">Back</button>
      <button type="button" class="btn-primary" id="roleSave">${isNew ? 'Create role' : 'Save changes'}</button>`;

    const form = dialog.body.querySelector('#roleForm');
    const errorBox = form.querySelector('#roleError');
    const dealsRow = form.querySelector('#roleDealsRow');
    const chosenAccess = () => (form.querySelector('input[name="roleAccess"]:checked') || {}).value || 'SALES';
    const syncDeals = () => { dealsRow.hidden = chosenAccess() === 'ADMIN'; };
    form.querySelectorAll('input[name="roleAccess"]').forEach((r) => r.addEventListener('change', syncDeals));
    syncDeals();
    setTimeout(() => form.querySelector('#roleName').focus(), 50);

    const showError = (msg) => { errorBox.textContent = msg; errorBox.hidden = !msg; };
    const back = () => { editing = null; render(); };
    dialog.footer.querySelector('#roleBack').addEventListener('click', back);
    form.addEventListener('submit', (e) => { e.preventDefault(); save(); });

    const saveBtn = dialog.footer.querySelector('#roleSave');
    saveBtn.addEventListener('click', save);

    async function save() {
      showError('');
      const name = form.querySelector('#roleName').value.trim();
      if (!name) { showError('Give the role a name.'); form.querySelector('#roleName').focus(); return; }
      Crm.busy(saveBtn, true, 'Saving…');
      try {
        if (isNew) {
          await Api.createRole({ name, access: chosenAccess(), receives_deals: chosenAccess() === 'SALES' && form.querySelector('#roleDeals').checked });
        } else {
          await Api.updateRole(editing.id, { name, receives_deals: editing.access === 'SALES' && form.querySelector('#roleDeals').checked });
        }
        Crm.toast(isNew ? 'Role created' : 'Role saved');
        Crm.emit('roles-changed');
        editing = null;
        await refresh();
      } catch (err) {
        showError(errText(err, 'Could not save this role.'));
        Crm.busy(saveBtn, false);
      }
    }
  }

  return { open };
})();
