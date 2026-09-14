(() => {
  'use strict';
  const element = id => document.getElementById(id);
  const status = element('install-status');
  let pending = {};

  function message(text, error = false) {
    status.textContent = text;
    status.dataset.error = String(error);
  }

  function show(stage) {
    element('unlock-form').hidden = stage !== 'unlock';
    element('connect-form').hidden = stage !== 'connect';
    element('verify-section').hidden = stage !== 'verify';
    element('complete-section').hidden = stage !== 'complete';
    for (const name of ['unlock', 'connect', 'verify']) {
      element(`step-${name}`).removeAttribute('aria-current');
    }
    if (stage !== 'complete') element(`step-${stage}`).setAttribute('aria-current', 'step');
  }

  async function request(path, body) {
    const response = await fetch(`/api/install/${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      credentials: 'same-origin',
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(120000),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : 'Installation request failed.');
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function storageFields() {
    const vault = element('secret-storage').value === 'key_vault';
    for (const name of ['vault-resource', 'vault-secret']) {
      element(`${name}-field`).hidden = !vault;
      element(name).required = vault;
    }
    const existing = element('use-existing').checked;
    element('client-secret-field').hidden = existing;
    element('client-secret').required = !existing;
    if (existing) element('client-secret').value = '';
  }

  async function refresh() {
    const data = await request('status');
    pending = data.connection || {};
    element('callback-url').value = data.callbackUrl;
    element('tenant-id').value = pending.tenantId || '';
    element('client-id').value = pending.clientId || '';
    element('secret-storage').value = pending.secretStorage || 'app_service';
    element('vault-resource').value = pending.keyVaultResourceId || '';
    element('vault-secret').value = pending.keyVaultSecretName || '';
    element('use-existing').checked = Boolean(pending.clientId);
    element('pending-tenant').textContent = pending.tenantId || '';
    element('pending-client').textContent = pending.clientId || '';
    storageFields();
    show(pending.clientId ? 'verify' : 'connect');
    message(pending.clientId ? 'Connection saved. Sign-in verification pending.' : 'Installation unlocked.');
  }

  async function submit(button, action) {
    button.disabled = true;
    try {
      await action();
    } catch (error) {
      message(error.status ? error.message : 'Connection interrupted. Reload to resume; the host may be restarting.', true);
    } finally {
      button.disabled = false;
    }
  }

  element('unlock-form').addEventListener('submit', event => {
    event.preventDefault();
    void submit(event.submitter, async () => {
      const code = element('setup-code').value;
      element('setup-code').value = '';
      await request('unlock', { code });
      await refresh();
    });
  });

  element('connect-form').addEventListener('submit', event => {
    event.preventDefault();
    void submit(event.submitter, async () => {
      const secretStorage = element('secret-storage').value;
      const clientSecret = element('client-secret').value;
      element('client-secret').value = '';
      message('Saving Entra connection...');
      await request('entra', {
        tenantId: element('tenant-id').value.trim(), clientId: element('client-id').value.trim(),
        secretStorage, clientSecret,
        keyVaultResourceId: secretStorage === 'key_vault' ? element('vault-resource').value.trim() : '',
        keyVaultSecretName: secretStorage === 'key_vault' ? element('vault-secret').value.trim() : '',
      });
      await refresh();
    });
  });

  element('finish-install').addEventListener('click', event => {
    void submit(event.currentTarget, async () => {
      message('Verifying sign-in and securing the portal...');
      await request('finish', {});
      show('complete');
      message('Installation complete. The portal is restarting.');
    });
  });
  element('edit-connection').addEventListener('click', () => { show('connect'); message(''); });
  element('secret-storage').addEventListener('change', storageFields);
  element('use-existing').addEventListener('change', storageFields);
  void refresh().catch(error => {
    if (error.status === 401) { show('unlock'); message(''); }
    else message(error.status ? error.message : 'Installation status is unavailable. Reload to retry.', true);
  });
})();